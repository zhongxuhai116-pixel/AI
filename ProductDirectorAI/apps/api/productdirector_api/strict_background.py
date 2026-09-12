"""V3-03 受控背景工作流 Producer 适配层。

职责：由系统侧（持租约 Worker）按冻结 background_workflow 触发背景生成或受控导入，
记录系统侧证据、逐帧 hash 与产品保护区隔离结论。客户端自报 producer 或本地预置文件
不能进入本模块的受控证据链。

当前真实状态：
- `INDEPENDENT_BACKGROUND_WORKFLOW`：只接受现场已核实、路径与 hash 匹配的工作流文件，
  通过现有 `providers.comfyui` 的提交/轮询/下载接口执行；本地无现场文件时 fail-closed。
- `CONTROLLED_IMPORT`：从系统侧受控目录导入 RGBA 背景帧，必须提供可信产品 Mask，
  且逐帧证明产品保护区内 alpha 为 0；否则 fail-closed。
- `FULL_FRAME_EXTRACT_NON_PRODUCT`：未完成现场校准，当前一律 fail-closed，防止把
  V2 整幅 H3 video graph 伪称为背景-only。
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Callable, Iterable

import numpy as np
from PIL import Image

from .providers import comfyui


BACKGROUND_EVIDENCE_FILE = "background_evidence.json"
CONTROLLED_WORKER = "CONTROLLED_WORKER"

MODE_CONTROLLED_IMPORT = "CONTROLLED_IMPORT"
MODE_INDEPENDENT_WORKFLOW = "INDEPENDENT_BACKGROUND_WORKFLOW"
MODE_FULL_FRAME_EXTRACT = "FULL_FRAME_EXTRACT_NON_PRODUCT"

UNSAFE_PRODUCT_REPLACEMENT_NODES = {
    "ProductBlenderRender",
    "ProductBlenderCached",
}
UNSAFE_FULL_FRAME_H3_NODES = {
    "MiniMaxH3ReferenceToVideo",
    "MiniMaxH3SpeedCache",
}

class BackgroundProducerError(RuntimeError):
    """背景 Producer 合同、来源或生成失败。"""


def _canonical_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _graph_node_types(graph: dict) -> set[str]:
    """兼容 ComfyUI UI workflow（nodes）与 API prompt（node id -> class_type）。"""
    types: set[str] = set()
    if not isinstance(graph, dict):
        return types
    nodes = graph.get("nodes")
    if isinstance(nodes, list):
        for node in nodes:
            if isinstance(node, dict):
                value = node.get("type") or node.get("class_type")
                if value:
                    types.add(str(value))
        return types
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        value = node.get("class_type") or node.get("type")
        if value:
            types.add(str(value))
    return types


def verify_background_workflow_graph(graph: dict) -> list[str]:
    """拒绝把 V2 全帧产品替换图冒充独立背景工作流。

    - 含 ProductBlenderRender/Cached 的是产品替换链路，直接拒绝。
    - 含 MiniMaxH3ReferenceToVideo / MiniMaxH3SpeedCache 的整幅 H3 视频生成图，
      当前没有现场核实的独立 background-only 证据，一律不能声称 background-only。
    """
    if not isinstance(graph, dict):
        return ["背景工作流图必须是 JSON 对象"]
    types = _graph_node_types(graph)
    if not types:
        return ["背景工作流图没有可识别节点"]
    failures: list[str] = []
    product_replacement = sorted(UNSAFE_PRODUCT_REPLACEMENT_NODES & types)
    if product_replacement:
        failures.append(
            "工作流包含产品替换渲染节点 " + ", ".join(product_replacement) + "，不是独立背景工作流"
        )
    full_frame_h3 = sorted(UNSAFE_FULL_FRAME_H3_NODES & types)
    if full_frame_h3:
        failures.append(
            "工作流包含整幅 H3 视频生成节点 "
            + ", ".join(full_frame_h3)
            + "，当前没有现场核实的独立 background-only 证据，不能作为独立背景工作流"
        )
    return failures


MODE_BACKGROUND_ONLY_H3 = "BACKGROUND_ONLY_H3"
BACKGROUND_ONLY_H3_NODE = "MiniMaxH3ImageToVideo"
BACKGROUND_ONLY_H3_REQUIRED_NODES = {
    "UNETLoader",
    "CLIPLoader",
    "VAELoader",
    "MiniMaxH3ImageToVideo",
    "BasicGuider",
    "BasicScheduler",
    "KSamplerSelect",
    "RandomNoise",
    "SamplerCustomAdvanced",
    "VAEDecode",
    "SaveImage",
}
BACKGROUND_ONLY_H3_FORBIDDEN_NODES = {
    "ProductBlenderRender",
    "ProductBlenderCached",
    "MiniMaxH3ReferenceToVideo",
    "MiniMaxH3SpeedCache",
    "LoadImage",
    "LoadVideo",
    "GetVideoComponents",
    "ImageCrop",
    "ProductReferenceShot",
}


def verify_background_only_h3_graph(graph: dict) -> list[str]:
    """校验真正 background-only H3 图。

    这里只做可机器证明的图结构检查，不把 prompt 文案或模型文件名当作生成隔离证据。
    """
    failures = verify_background_workflow_graph(graph)
    types = _graph_node_types(graph)
    if BACKGROUND_ONLY_H3_NODE not in types:
        failures.append(f"缺少背景专用生成节点 {BACKGROUND_ONLY_H3_NODE}")
    forbidden = sorted(BACKGROUND_ONLY_H3_FORBIDDEN_NODES & types)
    if forbidden:
        failures.append("background-only H3 图包含禁止节点: " + ", ".join(forbidden))
    missing_required = sorted(BACKGROUND_ONLY_H3_REQUIRED_NODES - types)
    if missing_required:
        failures.append("background-only H3 图缺少必需节点: " + ", ".join(missing_required))

    image_to_video_inputs: dict[str, object] = {}
    for node in graph.values():
        if isinstance(node, dict) and node.get("class_type") == BACKGROUND_ONLY_H3_NODE:
            image_to_video_inputs = node.get("inputs") or {}
            break
    keyframes = [key for key in image_to_video_inputs if str(key).startswith("first_frame") or str(key).startswith("last_frame")]
    if keyframes:
        failures.append("background-only H3 图不得连接 first_frame/last_frame 产品关键帧")
    return failures


BACKGROUND_ONLY_H3_RUNTIME_STATUS = "NOT_VERIFIED"
BACKGROUND_ONLY_H3_SCHEMA_STATUS = "SCHEMA_VERIFIED_ONLY"


# 2026-09-12 只读云端 ComfyUI /object_info/<node> 的实际 schema 快照。
# 该快照只用于本地静态契约校验，不证明真实 GPU 生成能力。
BACKGROUND_ONLY_H3_SCHEMA_SNAPSHOT: dict[str, dict] = {
    "UNETLoader": {
        "required": {"unet_name": "STRING", "weight_dtype": "STRING"},
        "optional": {},
        "output": ["MODEL"],
    },
    "CLIPLoader": {
        "required": {"clip_name": "STRING", "type": "STRING"},
        "optional": {"device": "STRING"},
        "output": ["CLIP"],
    },
    "VAELoader": {
        "required": {"vae_name": "STRING"},
        "optional": {},
        "output": ["VAE"],
    },
    "LoraLoaderModelOnly": {
        "required": {"model": "MODEL", "lora_name": "STRING", "strength_model": "FLOAT"},
        "optional": {},
        "output": ["MODEL"],
    },
    "MiniMaxH3SigmaShift": {
        "required": {"model": "MODEL", "shift_video": "FLOAT", "shift_audio": "FLOAT"},
        "optional": {},
        "output": ["MODEL"],
    },
    "MiniMaxH3ImageToVideo": {
        "required": {
            "clip": "CLIP",
            "vae": "VAE",
            "prompt": "STRING",
            "width": "INT",
            "height": "INT",
            "length": "INT",
        },
        "optional": {"first_frame": "IMAGE", "last_frame": "IMAGE"},
        "output": ["CONDITIONING", "LATENT"],
    },
    "BasicGuider": {
        "required": {"model": "MODEL", "conditioning": "CONDITIONING"},
        "optional": {},
        "output": ["GUIDER"],
    },
    "BasicScheduler": {
        "required": {"model": "MODEL", "scheduler": "STRING", "steps": "INT", "denoise": "FLOAT"},
        "optional": {},
        "output": ["SIGMAS"],
    },
    "KSamplerSelect": {
        "required": {"sampler_name": "STRING"},
        "optional": {},
        "output": ["SAMPLER"],
    },
    "RandomNoise": {
        "required": {"noise_seed": "INT"},
        "optional": {},
        "output": ["NOISE"],
    },
    "SamplerCustomAdvanced": {
        "required": {
            "noise": "NOISE",
            "guider": "GUIDER",
            "sampler": "SAMPLER",
            "sigmas": "SIGMAS",
            "latent_image": "LATENT",
        },
        "optional": {},
        "output": ["LATENT", "LATENT"],
    },
    "VAEDecode": {
        "required": {"samples": "LATENT", "vae": "VAE"},
        "optional": {},
        "output": ["IMAGE"],
    },
    "SaveImage": {
        "required": {"images": "IMAGE", "filename_prefix": "STRING"},
        "optional": {},
        "output": ["IMAGE"],
    },
}


_BACKGROUND_ONLY_H3_LINK_TYPES = {
    "MODEL",
    "CLIP",
    "VAE",
    "CONDITIONING",
    "LATENT",
    "NOISE",
    "GUIDER",
    "SAMPLER",
    "SIGMAS",
    "IMAGE",
}


def verify_background_only_h3_graph_schema(graph: dict, schema: dict | None = None) -> list[str]:
    """对照只读云端 schema 校验候选 graph 的输入键、输出索引与链接类型。"""
    schema = schema if schema is not None else BACKGROUND_ONLY_H3_SCHEMA_SNAPSHOT
    if not isinstance(graph, dict):
        return ["background-only H3 graph 必须是对象"]

    failures: list[str] = []
    nodes: dict[str, dict] = {}
    for node_id, node in graph.items():
        if isinstance(node_id, str) and isinstance(node, dict):
            nodes[node_id] = node

    for node_id, node in nodes.items():
        class_type = node.get("class_type")
        if not isinstance(class_type, str):
            failures.append(f"节点 {node_id} 缺少 class_type")
            continue
        info = schema.get(class_type)
        if info is None:
            failures.append(f"节点 {node_id} 类型 {class_type} 不在现场只读 schema 快照中")
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            failures.append(f"节点 {node_id} 缺少 inputs 对象")
            continue
        required = info.get("required", {})
        optional = info.get("optional", {})
        for key in required:
            if key not in inputs:
                failures.append(f"节点 {node_id} ({class_type}) 缺少必需输入 {key}")
        for key in inputs:
            if key not in required and key not in optional:
                failures.append(f"节点 {node_id} ({class_type}) 输入 {key} 不在现场 schema 中")

        for key, value in inputs.items():
            expected_type = required.get(key, optional.get(key))
            if expected_type is None:
                continue
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and isinstance(value[1], int):
                continue
            if expected_type in _BACKGROUND_ONLY_H3_LINK_TYPES:
                failures.append(f"节点 {node_id} ({class_type}) 输入 {key} 必须连接 {expected_type} 输出")
            elif expected_type == "INT" and (not isinstance(value, int) or isinstance(value, bool)):
                failures.append(f"节点 {node_id} ({class_type}) 输入 {key} 必须是整数")
            elif expected_type == "FLOAT" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
                failures.append(f"节点 {node_id} ({class_type}) 输入 {key} 必须是数值")
            elif expected_type == "STRING" and not isinstance(value, str):
                failures.append(f"节点 {node_id} ({class_type}) 输入 {key} 必须是字符串")

    for node_id, node in nodes.items():
        class_type = node.get("class_type")
        info = schema.get(class_type, {})
        inputs = node.get("inputs") or {}
        required = info.get("required", {})
        optional = info.get("optional", {})
        for key, value in inputs.items():
            if not (isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and isinstance(value[1], int)):
                continue
            source_id, source_index = value[0], value[1]
            source_node = nodes.get(source_id)
            if source_node is None:
                failures.append(f"节点 {node_id} 输入 {key} 引用了不存在的源节点 {source_id}")
                continue
            source_class = source_node.get("class_type")
            source_info = schema.get(source_class, {})
            source_outputs = source_info.get("output", [])
            if source_index < 0 or source_index >= len(source_outputs):
                failures.append(f"节点 {node_id} 输入 {key} 引用了源节点 {source_id} 不存在的输出索引 {source_index}")
                continue
            expected_type = required.get(key, optional.get(key))
            source_type = source_outputs[source_index]
            if expected_type in _BACKGROUND_ONLY_H3_LINK_TYPES and expected_type != source_type:
                failures.append(
                    f"节点 {node_id} 输入 {key} 期望 {expected_type}，源节点 {source_id} 输出 {source_index} 是 {source_type}"
                )
    return failures


def _default_background_only_model_names() -> dict[str, str]:
    return {
        "unet_name": "minimax_h3_ref2va_pruned_int8_convrot.safetensors",
        "clip_name": "qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors",
        "video_vae_name": "minimax_h3_video_vae_fp16.safetensors",
        "lora_name": "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors",
    }


def build_background_only_h3_graph(workflow: dict, seed: int | None = None) -> dict:
    """构造可提交的 API prompt graph：MiniMaxH3ImageToVideo -> SaveImage。

    该图不接 LoadImage/LoadVideo/ProductBlenderRender，也不接 first/last keyframe。
    """
    if not isinstance(workflow, dict):
        raise BackgroundProducerError("background-only H3 工作流合同必须是对象")
    if workflow.get("background_only") is not True:
        raise BackgroundProducerError("background-only H3 工作流必须显式标记 background_only=true")
    prompt = workflow.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise BackgroundProducerError("background-only H3 工作流缺少 prompt")
    for field in ("width", "height", "length", "steps"):
        value = workflow.get(field)
        if not isinstance(value, int) or value <= 0:
            raise BackgroundProducerError(f"background-only H3 工作流字段 {field} 必须是正整数")
    if seed is None:
        seed = workflow.get("seed")
    if not isinstance(seed, int) or seed < 0:
        raise BackgroundProducerError("background-only H3 工作流缺少有效 seed")

    models = workflow.get("model_names") or {}
    if not isinstance(models, dict):
        raise BackgroundProducerError("background-only H3 工作流 model_names 必须是对象")
    defaults = _default_background_only_model_names()
    model_names = {key: models.get(key, defaults[key]) for key in defaults}

    graph = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": model_names["unet_name"], "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": model_names["clip_name"], "type": "minimax", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": model_names["video_vae_name"]}},
        "4": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": model_names["lora_name"], "strength_model": 1.0}},
        "5": {"class_type": "MiniMaxH3SigmaShift", "inputs": {"model": ["4", 0], "shift_video": 12.0, "shift_audio": 3.0}},
        "6": {
            "class_type": BACKGROUND_ONLY_H3_NODE,
            "inputs": {
                "clip": ["2", 0],
                "vae": ["3", 0],
                "prompt": prompt.strip(),
                "width": int(workflow["width"]),
                "height": int(workflow["height"]),
                "length": int(workflow["length"]),
            },
        },
        "7": {"class_type": "BasicGuider", "inputs": {"model": ["5", 0], "conditioning": ["6", 0]}},
        "8": {"class_type": "BasicScheduler", "inputs": {"model": ["5", 0], "scheduler": "simple", "steps": int(workflow["steps"]), "denoise": 1.0}},
        "9": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "10": {"class_type": "RandomNoise", "inputs": {"noise_seed": int(seed)}},
        "11": {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "noise": ["10", 0],
                "guider": ["7", 0],
                "sampler": ["9", 0],
                "sigmas": ["8", 0],
                "latent_image": ["6", 1],
            },
        },
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["3", 0]}},
        "13": {"class_type": "SaveImage", "inputs": {"images": ["12", 0], "filename_prefix": "Strict-Background/bg_only"}},
    }
    return graph


def background_only_h3_graph_hash(graph: dict) -> str:
    return sha256_bytes(_canonical_json(graph).encode("utf-8"))


def verify_background_only_h3_contract(graph: dict, workflow: dict) -> list[str]:
    failures = verify_background_only_h3_graph(graph)
    failures.extend(verify_background_only_h3_graph_schema(graph))
    expected_hash = workflow.get("workflow_hash")
    if not isinstance(expected_hash, str) or not expected_hash:
        failures.append("background-only H3 工作流缺少 workflow_hash")
    elif background_only_h3_graph_hash(graph).lower() != expected_hash.lower():
        failures.append("background-only H3 graph hash 与冻结 workflow_hash 不一致")
    return failures


def collect_background_only_h3_frames(
    job_id: str,
    snapshot: dict,
    workflow: dict,
    submission: dict,
    strict_root: Path,
    output_spec,
    *,
    client: object = comfyui,
    poll_seconds: float = 2.0,
    max_poll_seconds: float = 600.0,
) -> dict:
    """轮询 background-only H3 SaveImage 产物，允许模型 grid 多出的尾部帧被明确丢弃。

    冻结合同要求 raw_frame_count >= output_spec.frame_count；只登记并发布 1..frame_count。
    """
    external_id = submission["external_id"]
    deadline = time.time() + max_poll_seconds
    record = None
    while time.time() < deadline:
        try:
            record = client.history(external_id)
        except comfyui.ComfyUIError as exc:
            raise BackgroundProducerError(f"背景工作流轮询失败: {exc}") from exc
        if record is not None and client.is_completed(record):
            break
        time.sleep(poll_seconds)
    if record is None or not client.is_completed(record):
        raise BackgroundProducerError("背景工作流未在限时内完成")
    if client.status_text(record) != "SUCCEEDED":
        raise BackgroundProducerError("背景工作流执行失败")

    raw_frame_count = int(workflow.get("raw_frame_count") or workflow.get("length") or output_spec.frame_count)
    if raw_frame_count < output_spec.frame_count:
        raise BackgroundProducerError("background-only H3 raw_frame_count 小于计划帧数")
    expected = set(range(1, output_spec.frame_count + 1))
    items = [item for item in client.outputs(record) if item.get("filename", "").lower().endswith(".png")]
    if not items:
        raise BackgroundProducerError("背景工作流完成但没有 PNG 帧产物")

    frame_items: dict[int, dict] = {}
    failures: list[str] = []
    for item in sorted(items, key=lambda entry: entry.get("filename", "")):
        frame = _parse_frame_index(Path(item["filename"]))
        if frame is None:
            failures.append(f"背景工作流产物帧名不可识别: {item['filename']}")
            continue
        if frame > raw_frame_count:
            failures.append(f"背景工作流产物越出冻结 raw_frame_count: {item['filename']}")
            continue
        if frame in frame_items:
            failures.append(f"背景工作流存在重复帧 {frame}: {frame_items[frame].get('filename')}, {item['filename']}")
            continue
        frame_items[frame] = item
    if failures:
        raise BackgroundProducerError("；".join(failures))

    selected_items = {frame: frame_items[frame] for frame in expected if frame in frame_items}
    if set(selected_items) != expected:
        raise BackgroundProducerError(
            f"背景工作流选定帧集与计划不一致：缺少 {sorted(expected - set(selected_items))}"
        )

    frame_bytes: dict[int, bytes] = {}
    for frame in sorted(expected):
        data = client.download(selected_items[frame])
        failures.extend(_validate_png_payload(data, frame, output_spec))
        if failures:
            break
        frame_bytes[frame] = data
    if failures:
        raise BackgroundProducerError("；".join(failures))

    frames_evidence = _publish_background_frames(strict_root, frame_bytes)
    return {
        "schema_version": "1.0",
        "producer_control": CONTROLLED_WORKER,
        "mode": MODE_BACKGROUND_ONLY_H3,
        "generation_claim": True,
        "input_trust": "BACKGROUND_ONLY_H3_VERIFICATION_SAMPLE",
        "release_eligible": False,
        "job_id": job_id,
        "plan_id": snapshot["plan_id"],
        "plan_contract_id": snapshot["plan_contract_id"],
        "product_version_id": snapshot["product_version_id"],
        "owner_id": snapshot["owner_id"],
        "workflow": workflow,
        "workflow_hash": workflow.get("workflow_hash", ""),
        "graph_hash": background_only_h3_graph_hash(workflow.get("_graph", {})),
        "submission": submission,
        "raw_frame_count": raw_frame_count,
        "selected_frame_count": output_spec.frame_count,
        "tail_frames_dropped": raw_frame_count - output_spec.frame_count,
        "product_protection": "BACKGROUND_ONLY_H3_PIXEL_LOCK_REQUIRED",
        "pixel_lock_required": True,
        "frames": {
            "start_frame": 1,
            "frame_count": output_spec.frame_count,
            "files": frames_evidence,
        },
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def run_background_only_h3_producer(
    job_id: str,
    worker_id: str,
    lease_epoch: int,
    snapshot: dict,
    approved_workflow: dict,
    strict_root: Path,
    output_spec,
    *,
    requested_workflow: dict | None = None,
    lease_check: Callable[[str, str, int], None] | None = None,
    client: object = comfyui,
    poll_seconds: float = 2.0,
    max_poll_seconds: float = 600.0,
) -> dict:
    """系统受控 background-only H3 Producer：构造并冻结 graph，提交后收集 PNG 帧。"""
    if lease_check is not None:
        lease_check(job_id, worker_id, lease_epoch)
    frozen_workflow = snapshot.get("background_workflow")
    if not isinstance(frozen_workflow, dict):
        raise BackgroundProducerError("冻结 Run 快照缺少 background_workflow")
    if requested_workflow is not None:
        contract_failures = verify_workflow_contract(requested_workflow, frozen_workflow)
        if contract_failures:
            raise BackgroundProducerError("调用方请求与冻结 Run 快照不一致: " + "；".join(contract_failures))
    else:
        requested_workflow = frozen_workflow
    failures = verify_workflow_contract(frozen_workflow, approved_workflow)
    if failures:
        raise BackgroundProducerError("冻结 Run 工作流与批准策略不一致: " + "；".join(failures))

    graph = build_background_only_h3_graph(requested_workflow)
    graph_failures = verify_background_only_h3_contract(graph, requested_workflow)
    if graph_failures:
        raise BackgroundProducerError("；".join(graph_failures))
    requested_workflow = dict(requested_workflow)
    requested_workflow["_graph"] = graph
    requested_workflow["_graph_hash"] = background_only_h3_graph_hash(graph)
    if requested_workflow.get("runtime_generation_status") != "VERIFIED" or not requested_workflow.get("runtime_verification_evidence"):
        raise BackgroundProducerError(
            "BACKGROUND_ONLY_H3 候选 graph 仅 SCHEMA_VERIFIED_ONLY，"
            "runtime_generation=NOT_VERIFIED，未获现场独立工作流运行证据，不得提交实际生成"
        )
    try:
        external_id = client.submit(graph, client_id=f"background-{job_id}")
    except comfyui.ComfyUIError as exc:
        raise BackgroundProducerError(f"background-only H3 工作流提交失败: {exc}") from exc
    submission = {
        "mode": MODE_BACKGROUND_ONLY_H3,
        "external_id": external_id,
        "graph_hash": requested_workflow["_graph_hash"],
        "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    return collect_background_only_h3_frames(
        job_id, snapshot, requested_workflow, submission, strict_root, output_spec,
        client=client, poll_seconds=poll_seconds, max_poll_seconds=max_poll_seconds,
    )

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _parse_frame_index(path: Path) -> int | None:
    import re

    match = re.search(r"(?:^|[^0-9])(\d+)(?:$|[^0-9])", path.stem)
    return int(match.group(1)) if match else None


def discover_frames(paths: Iterable[Path], label: str, failures: list[str]) -> dict[int, Path]:
    frames: dict[int, Path] = {}
    for path in sorted(paths):
        frame = _parse_frame_index(path)
        if frame is None:
            failures.append(f"{label} 包含不可识别帧名：{path.name}")
            continue
        if frame in frames:
            failures.append(f"{label} 存在重复帧 {frame}: {frames[frame].name}, {path.name}")
            continue
        frames[frame] = path
    return frames


def default_controlled_source_root() -> Path:
    raw = os.getenv("PRODUCTDIRECTOR_BACKGROUND_SOURCE_ROOT", "")
    if raw:
        return Path(raw).resolve()
    return (Path(__file__).resolve().parents[3] / "var" / "background_sources").resolve()


def resolve_controlled_source(source_dir: Path, controlled_root: Path | None = None) -> Path:
    root = (controlled_root or default_controlled_source_root()).resolve()
    source = source_dir.resolve()
    try:
        source.relative_to(root)
    except ValueError as exc:
        raise BackgroundProducerError("背景源目录不在受控目录内") from exc
    if not source.exists() or not source.is_dir():
        raise BackgroundProducerError(f"背景源目录不存在: {source}")
    return source


def verify_workflow_contract(workflow: dict, approved: dict) -> list[str]:
    """冻结 workflow 必须与策略批准项在 name/version/hash/protection_map 完全一致。"""
    failures: list[str] = []
    for field in ("name", "version", "workflow_hash"):
        if str(workflow.get(field, "")) != str(approved.get(field, "")):
            failures.append(f"background_workflow.{field} 与冻结策略不一致")
    expected_map = _canonical_json({"protection_map": approved.get("protection_map", [])})
    actual_map = _canonical_json({"protection_map": workflow.get("protection_map", [])})
    if expected_map != actual_map:
        failures.append("background_workflow.protection_map 与冻结策略不一致")
    return failures


def verify_evidence_binding(evidence: dict, job_id: str, snapshot: dict, workflow_hash: str) -> list[str]:
    failures: list[str] = []
    if evidence.get("job_id") != job_id:
        failures.append("背景证据不属于当前 Job")
    for field in ("plan_id", "plan_contract_id", "product_version_id", "owner_id"):
        if evidence.get(field) != snapshot.get(field):
            failures.append(f"背景证据 {field} 与 Run 快照不一致")
    if str(evidence.get("workflow_hash", "")).lower() != str(workflow_hash).lower():
        failures.append("背景证据 workflow_hash 与批准工作流不一致")
    if evidence.get("producer_control") != CONTROLLED_WORKER:
        failures.append("背景证据不是系统受控 Worker 产物")
    return failures


def verify_background_evidence_files(strict_root: Path, evidence: dict, output_spec) -> list[str]:
    """运行后复核：目标帧集、逐文件 hash 与受控导入时的产品区隔离证据。"""
    failures: list[str] = []
    frame_block = evidence.get("frames") or {}
    if frame_block.get("start_frame") != 1:
        failures.append("背景证据 start_frame 必须为 1")
    expected_frames = set(range(1, int(output_spec.frame_count) + 1))
    evidence_frames = {int(entry.get("frame")) for entry in frame_block.get("files", [])}
    if evidence_frames != expected_frames:
        failures.append(
            f"背景证据帧集与计划不一致：缺少 {sorted(expected_frames - evidence_frames)}，多出 {sorted(evidence_frames - expected_frames)}"
        )

    expected_files: dict[str, str] = {}
    for entry in frame_block.get("files", []):
        expected_files[entry["path"]] = str(entry.get("sha256", "")).lower()
    background_dir = strict_root / "background"
    current_files: dict[str, str] = {}
    if background_dir.exists():
        for path in background_dir.glob("*.png"):
            relative = path.relative_to(strict_root).as_posix()
            current_files[relative] = sha256_file(path).lower()
    missing = sorted(set(expected_files) - set(current_files))
    extra = sorted(set(current_files) - set(expected_files))
    if missing:
        failures.append(f"背景产物缺少文件：{missing[:8]}")
    if extra:
        failures.append(f"背景产物多出未登记文件：{extra[:8]}")
    for relative, expected_hash in expected_files.items():
        actual = current_files.get(relative)
        if actual is not None and actual != expected_hash:
            failures.append(f"背景产物已被替换：{relative}")

    if evidence.get("product_protection") == "IMPORT_ISOLATION_VERIFIED":
        workflow = evidence.get("workflow") or {}
        protection_map = workflow.get("protection_map", [])
        for entry in frame_block.get("files", []):
            source_path = entry.get("source_path")
            mask_path = entry.get("mask_path")
            if not source_path or not mask_path:
                failures.append(f"受控导入帧 {entry.get('frame')} 缺少隔离复核路径")
                continue
            source_file = Path(source_path)
            mask_file = Path(mask_path)
            if not source_file.exists() or not mask_file.exists():
                failures.append(f"受控导入帧 {entry.get('frame')} 隔离源文件缺失")
                continue
            if sha256_file(source_file).lower() != str(entry.get("source_sha256", "")).lower():
                failures.append(f"受控导入帧 {entry.get('frame')} 源文件已被替换")
                continue
            if sha256_file(mask_file).lower() != str(entry.get("mask_sha256", "")).lower():
                failures.append(f"受控导入帧 {entry.get('frame')} Mask 已被替换")
                continue
            failures.extend(verify_product_isolation(source_file, mask_file, protection_map))
    return failures


def _dilate_protected_mask(mask: np.ndarray, radius: int) -> np.ndarray:
    """把连续产品可见像素向边缘扩一圈，避免抗锯齿低 alpha 边缘被背景覆盖。"""
    if radius <= 0:
        return mask
    protected = mask > 1e-4
    padded = np.pad(protected, radius, mode="constant", constant_values=False)
    height, width = protected.shape
    dilated = protected.copy()
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dy == 0 and dx == 0:
                continue
            dilated |= padded[radius + dy:radius + dy + height, radius + dx:radius + dx + width]
    return dilated


def _protection_map_failures(protection_map: list[dict]) -> list[str]:
    """保护映射不能仅非空；声明可覆盖产品区或不含明确隔离区的映射直接 fail-closed。"""
    if not protection_map:
        return ["background_workflow 缺少 protection_map，无法证明产品保护区隔离"]
    unsafe_background_regions = {
        "anywhere", "full_frame", "fullframe", "entire_frame", "all_frame",
        "product", "inside_product", "overlay_product", "cover_product",
    }
    unsafe_flags = {
        "include_product", "covers_product", "overlay_product", "allow_product_overlay",
        "background_in_product_region",
    }
    failures: list[str] = []
    for index, item in enumerate(protection_map):
        if not isinstance(item, dict):
            failures.append(f"protection_map[{index}] 必须是对象")
            continue
        product_region = str(item.get("product_region", "")).strip().lower()
        background_region = str(item.get("background_region", "")).strip().lower()
        if not product_region or not background_region:
            failures.append(f"protection_map[{index}] 必须同时声明 product_region 与 background_region")
            continue
        if background_region == product_region or background_region in unsafe_background_regions:
            failures.append(f"protection_map[{index}] 声明背景可覆盖产品区，不能用于背景-only 证据")
            continue
        for flag in unsafe_flags:
            value = item.get(flag)
            if value is True or str(value).strip().lower() in {"true", "1", "yes"}:
                failures.append(f"protection_map[{index}] 含不安全标志 {flag}")
                break
    return failures


def _publish_background_frames(strict_root: Path, frames: dict[int, bytes]) -> list[dict]:
    """把已通过全部校验的帧先落到独立暂存目录，再一次性发布到 strict/background。"""
    strict_root.mkdir(parents=True, exist_ok=True)
    stage_parent = strict_root.parent
    stage_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=stage_parent, prefix=".background_stage_") as stage_raw:
        stage = Path(stage_raw)
        staged: dict[int, Path] = {}
        for frame, data in frames.items():
            staged_path = stage / f"frame_{frame:04d}.png"
            staged_path.write_bytes(data)
            staged[frame] = staged_path

        evidence: list[dict] = []
        for frame in sorted(staged):
            path = staged[frame]
            evidence.append({
                **_frame_evidence(f"background/frame_{frame:04d}.png", frame),
                "sha256": sha256_file(path),
            })

        target_dir = strict_root / "background"
        if target_dir.exists():
            for old in target_dir.iterdir():
                if old.is_file():
                    old.unlink()
        target_dir.mkdir(parents=True, exist_ok=True)
        for frame, staged_path in sorted(staged.items()):
            shutil.move(str(staged_path), str(target_dir / f"frame_{frame:04d}.png"))
        return evidence


def _png_frame_bytes(source_path: Path) -> bytes:
    with Image.open(source_path) as image:
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="PNG")
        return buffer.getvalue()


def _validate_png_payload(data: bytes, frame: int, output_spec) -> list[str]:
    failures: list[str] = []
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            size = image.size
    except Exception as exc:
        return [f"background 帧 {frame} 不是有效 PNG: {exc}"]
    if (int(size[0]), int(size[1])) != (int(output_spec.width), int(output_spec.height)):
        failures.append(
            f"background 帧 {frame} 尺寸 {size[0]}x{size[1]} 与计划 {output_spec.width}x{output_spec.height} 不一致"
        )
    return failures


def verify_product_isolation(
    source_path: Path, mask_path: Path, protection_map: list[dict]
) -> list[str]:
    """逐帧证明背景在产品 Mask 保护区内没有非零 alpha；无 Mask 或非 RGBA 均失败。"""
    failures: list[str] = []
    map_failures = _protection_map_failures(protection_map)
    if map_failures:
        return map_failures
    with Image.open(mask_path) as mask_image, Image.open(source_path) as source_image:
        if "A" not in source_image.getbands():
            return [f"背景源帧缺少 Alpha，无法证明产品保护区隔离：{source_path.name}"]
        mask = np.asarray(mask_image.convert("L"), dtype=np.float32) / 255.0
        alpha = np.asarray(source_image.getchannel("A"), dtype=np.float32) / 255.0
        if mask.shape != alpha.shape:
            return [f"产品 Mask 与背景源帧尺寸不一致：{mask.shape} / {alpha.shape}"]
        protected = _dilate_protected_mask(mask, radius=1)
        if np.any(alpha[protected] > 1e-4):
            failures.append(f"背景源帧覆盖产品保护区：{source_path.name}")
    return failures


def _frame_evidence(relative_path: str, frame: int) -> dict:
    return {"path": relative_path, "frame": frame}


def run_controlled_import(
    job_id: str,
    snapshot: dict,
    workflow: dict,
    source_dir: Path,
    strict_root: Path,
    output_spec,
    *,
    mask_dir: Path | None = None,
    controlled_root: Path | None = None,
) -> dict:
    """受控导入：只从系统侧受控目录复制，并证明产品保护区未被覆盖。"""
    source = resolve_controlled_source(source_dir, controlled_root)
    strict_root.mkdir(parents=True, exist_ok=True)

    failures: list[str] = []
    source_files = discover_frames(
        [p for p in source.glob("*.png") if p.is_file()], "background_source", failures
    )
    if not mask_dir:
        failures.append("未提供可信产品 Mask，不能执行受控背景导入")
        raise BackgroundProducerError("；".join(failures))
    mask_files = discover_frames(mask_dir.glob("*.png"), "product_mask", failures)
    expected = set(range(1, output_spec.frame_count + 1))
    if set(source_files) != expected:
        failures.append(
            f"background_source 帧集与冻结计划不一致：缺少 {sorted(expected - set(source_files))}，多出 {sorted(set(source_files) - expected)}"
        )
    if set(mask_files) != expected:
        failures.append(
            f"product_mask 帧集与冻结计划不一致：缺少 {sorted(expected - set(mask_files))}，多出 {sorted(set(mask_files) - expected)}"
        )
    if failures:
        raise BackgroundProducerError("；".join(failures))

    frame_bytes: dict[int, bytes] = {}
    source_records: dict[int, dict] = {}
    for frame in sorted(expected):
        source_path = source_files[frame]
        mask_path = mask_files[frame]
        isolation_failures = verify_product_isolation(
            source_path, mask_path, workflow.get("protection_map", [])
        )
        if isolation_failures:
            failures.extend(isolation_failures)
            continue
        data = _png_frame_bytes(source_path)
        failures.extend(_validate_png_payload(data, frame, output_spec))
        if failures:
            break
        frame_bytes[frame] = data
        source_records[frame] = {
            "source_path": str(source_path),
            "mask_path": str(mask_path),
            "source_sha256": sha256_file(source_path),
            "mask_sha256": sha256_file(mask_path),
        }
    if failures:
        raise BackgroundProducerError("；".join(failures[:20]))
    frames_evidence = _publish_background_frames(strict_root, frame_bytes)
    for record in frames_evidence:
        record.update(source_records[record["frame"]])

    evidence = {
        "schema_version": "1.0",
        "producer_control": CONTROLLED_WORKER,
        "mode": MODE_CONTROLLED_IMPORT,
        "job_id": job_id,
        "plan_id": snapshot["plan_id"],
        "plan_contract_id": snapshot["plan_contract_id"],
        "product_version_id": snapshot["product_version_id"],
        "owner_id": snapshot["owner_id"],
        "workflow": workflow,
        "workflow_hash": workflow.get("workflow_hash", ""),
        "source_root": str(source),
        "product_protection": "IMPORT_ISOLATION_VERIFIED",
        "source_provenance": "CONTROLLED_IMPORT",
        "generation_claim": False,
        "input_trust": "CONTROLLED_IMPORT_VERIFICATION_SAMPLE",
        "pixel_lock_required": True,
        "frames": {
            "start_frame": 1,
            "frame_count": output_spec.frame_count,
            "files": frames_evidence,
        },
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    return evidence


def submit_controlled_comfyui_workflow(
    job_id: str,
    workflow: dict,
    workflow_file: Path,
    *,
    client: object = comfyui,
) -> dict:
    """提交现场已核实工作流；文件 hash 必须等于冻结 workflow_hash。"""
    if not workflow_file.exists() or not workflow_file.is_file():
        raise BackgroundProducerError(f"现场背景工作流文件未配置: {workflow_file}")
    if sha256_file(workflow_file).lower() != str(workflow.get("workflow_hash", "")).lower():
        raise BackgroundProducerError("现场背景工作流文件 hash 与冻结 workflow_hash 不一致")
    try:
        graph = json.loads(workflow_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BackgroundProducerError(f"现场背景工作流文件 JSON 无效: {exc}") from exc
    if not isinstance(graph, dict):
        raise BackgroundProducerError("现场背景工作流文件必须是 JSON 对象")
    graph_failures = verify_background_workflow_graph(graph)
    if graph_failures:
        raise BackgroundProducerError("；".join(graph_failures))
    try:
        external_id = client.submit(graph, client_id=f"background-{job_id}")
    except comfyui.ComfyUIError as exc:
        raise BackgroundProducerError(f"背景工作流提交失败: {exc}") from exc
    return {
        "mode": MODE_INDEPENDENT_WORKFLOW,
        "workflow_file": str(workflow_file),
        "workflow_file_sha256": sha256_file(workflow_file),
        "external_id": external_id,
        "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def collect_controlled_comfyui_frames(
    job_id: str,
    snapshot: dict,
    workflow: dict,
    submission: dict,
    strict_root: Path,
    output_spec,
    *,
    client: object = comfyui,
    poll_seconds: float = 2.0,
    max_poll_seconds: float = 600.0,
) -> dict:
    """轮询并下载独立背景工作流逐帧产物；失败或缺帧 fail-closed。"""
    external_id = submission["external_id"]
    deadline = time.time() + max_poll_seconds
    record = None
    while time.time() < deadline:
        try:
            record = client.history(external_id)
        except comfyui.ComfyUIError as exc:
            raise BackgroundProducerError(f"背景工作流轮询失败: {exc}") from exc
        if record is not None and client.is_completed(record):
            break
        time.sleep(poll_seconds)
    if record is None or not client.is_completed(record):
        raise BackgroundProducerError("背景工作流未在限时内完成")
    if client.status_text(record) != "SUCCEEDED":
        raise BackgroundProducerError("背景工作流执行失败")

    items = [item for item in client.outputs(record) if item.get("filename", "").lower().endswith(".png")]
    if not items:
        raise BackgroundProducerError("背景工作流完成但没有 PNG 帧产物")
    expected = set(range(1, output_spec.frame_count + 1))
    frame_items: dict[int, dict] = {}
    failures: list[str] = []
    for item in sorted(items, key=lambda entry: entry.get("filename", "")):
        source_name = item["filename"]
        frame = _parse_frame_index(Path(source_name))
        if frame is None:
            failures.append(f"背景工作流产物帧名不可识别: {source_name}")
            continue
        if frame not in expected:
            failures.append(f"背景工作流产物越出冻结帧集: {source_name}")
            continue
        if frame in frame_items:
            failures.append(f"背景工作流存在重复帧 {frame}: {frame_items[frame].get('filename')}, {source_name}")
            continue
        frame_items[frame] = item
    if failures:
        raise BackgroundProducerError("；".join(failures))

    frame_bytes: dict[int, bytes] = {}
    for frame, item in sorted(frame_items.items()):
        data = client.download(item)
        failures.extend(_validate_png_payload(data, frame, output_spec))
        if failures:
            break
        frame_bytes[frame] = data
    if failures:
        raise BackgroundProducerError("；".join(failures))

    present = set(frame_bytes)
    if present != expected:
        raise BackgroundProducerError(
            f"背景工作流帧集与冻结计划不一致：缺少 {sorted(expected - present)}，多出 {sorted(present - expected)}"
        )
    frames_evidence = _publish_background_frames(strict_root, frame_bytes)
    return {
        "schema_version": "1.0",
        "producer_control": CONTROLLED_WORKER,
        "mode": MODE_INDEPENDENT_WORKFLOW,
        "job_id": job_id,
        "plan_id": snapshot["plan_id"],
        "plan_contract_id": snapshot["plan_contract_id"],
        "product_version_id": snapshot["product_version_id"],
        "owner_id": snapshot["owner_id"],
        "workflow": workflow,
        "workflow_hash": workflow.get("workflow_hash", ""),
        "submission": submission,
        "product_protection": "INDEPENDENT_WORKFLOW_PIXEL_LOCK_REQUIRED",
        "pixel_lock_required": True,
        "frames": {
            "start_frame": 1,
            "frame_count": output_spec.frame_count,
            "files": frames_evidence,
        },
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def run_controlled_background_workflow(
    job_id: str,
    worker_id: str,
    lease_epoch: int,
    snapshot: dict,
    approved_workflow: dict,
    strict_root: Path,
    output_spec,
    *,
    requested_workflow: dict | None = None,
    mode: str = MODE_CONTROLLED_IMPORT,
    source_dir: Path | None = None,
    mask_dir: Path | None = None,
    workflow_file: Path | None = None,
    lease_check: Callable[[str, str, int], None] | None = None,
    controlled_root: Path | None = None,
    client: object = comfyui,
) -> dict:
    """由持租约 Worker 触发；先复核租约、冻结 Run 请求与批准策略项，再按模式执行。"""
    if lease_check is not None:
        lease_check(job_id, worker_id, lease_epoch)
    frozen_workflow = snapshot.get("background_workflow")
    if not isinstance(frozen_workflow, dict):
        raise BackgroundProducerError("冻结 Run 快照缺少独立 background_workflow，不能启动受控背景 Producer")
    missing_fields = [field for field in ("name", "version", "workflow_hash", "protection_map") if field not in frozen_workflow]
    if missing_fields:
        raise BackgroundProducerError("冻结 Run 快照 background_workflow 缺少字段: " + ", ".join(missing_fields))
    if requested_workflow is not None:
        request_failures = verify_workflow_contract(requested_workflow, frozen_workflow)
        if request_failures:
            raise BackgroundProducerError("调用方请求与冻结 Run 快照不一致: " + "；".join(request_failures))
    else:
        requested_workflow = frozen_workflow
    failures = verify_workflow_contract(frozen_workflow, approved_workflow)
    if failures:
        raise BackgroundProducerError("冻结 Run 工作流与批准策略不一致: " + "；".join(failures))
    map_failures = _protection_map_failures(frozen_workflow.get("protection_map", []))
    if map_failures:
        raise BackgroundProducerError("；".join(map_failures))

    if mode == MODE_CONTROLLED_IMPORT:
        if source_dir is None:
            raise BackgroundProducerError("受控导入模式必须提供 source_dir")
        return run_controlled_import(
            job_id, snapshot, requested_workflow, source_dir, strict_root, output_spec,
            mask_dir=mask_dir, controlled_root=controlled_root,
        )
    if mode == MODE_INDEPENDENT_WORKFLOW:
        if workflow_file is None:
            raise BackgroundProducerError("独立背景工作流模式必须提供 workflow_file")
        submission = submit_controlled_comfyui_workflow(
            job_id, requested_workflow, workflow_file, client=client
        )
        return collect_controlled_comfyui_frames(
            job_id, snapshot, requested_workflow, submission, strict_root, output_spec, client=client
        )
    if mode == MODE_FULL_FRAME_EXTRACT:
        raise BackgroundProducerError(
            "FULL_FRAME_EXTRACT_NON_PRODUCT 尚未完成现场校准；不能从整幅 H3 video graph 直接升格为背景-only"
        )
    raise BackgroundProducerError(f"未知背景生产模式: {mode}")


def write_background_evidence(strict_root: Path, evidence: dict) -> Path:
    path = strict_root / BACKGROUND_EVIDENCE_FILE
    path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_background_evidence(strict_root: Path) -> dict | None:
    path = strict_root / BACKGROUND_EVIDENCE_FILE
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BackgroundProducerError(f"背景证据 JSON 无效: {exc}") from exc
