"""ComfyUI Provider 客户端（V2）。

V2 要求把现有自托管 H3/ComfyUI 接进项目，而不是让用户去填节点 ID。
本模块只做「提交 / 查询 / 下载」三件事，并且：

- 不保存任何凭证（ComfyUI 部署在回环地址，鉴权由外层 Caddy 负责）；
- 所有 HTTP 错误转成可读异常，便于任务记录失败原因；
- 图形（workflow graph）由 `build_hunyuan3d_graph()` 生成，参数显式。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request


class ComfyUIError(RuntimeError):
    """Provider 调用失败（网络、HTTP 状态或响应格式）。"""


def base_url() -> str:
    return os.getenv("PRODUCTDIRECTOR_COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")


def _request(path: str, payload: dict | None = None, timeout: int = 60):
    url = f"{base_url()}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise ComfyUIError(f"ComfyUI 返回 HTTP {exc.code}: {path}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ComfyUIError(f"无法连接 ComfyUI（{base_url()}）: {exc}") from exc


def health(timeout: int = 10) -> dict:
    """返回 Provider 可用性与队列状态；不可用时 reachable=False，不抛异常。"""
    try:
        stats = json.loads(_request("/system_stats", timeout=timeout).decode("utf-8"))
        queue = json.loads(_request("/queue", timeout=timeout).decode("utf-8"))
    except (ComfyUIError, json.JSONDecodeError) as exc:
        return {"reachable": False, "base_url": base_url(), "error": str(exc)}
    devices = stats.get("devices") or []
    return {
        "reachable": True,
        "base_url": base_url(),
        "comfyui_version": stats.get("system", {}).get("comfyui_version"),
        "device": (devices[0].get("name") if devices else None),
        "queue_running": len(queue.get("queue_running", [])),
        "queue_pending": len(queue.get("queue_pending", [])),
    }


def submit(graph: dict, client_id: str = "productdirector") -> str:
    """提交工作流，返回 ComfyUI 的 prompt_id（即 operation ID）。"""
    payload = json.loads(_request("/prompt", {"prompt": graph, "client_id": client_id}).decode("utf-8"))
    prompt_id = payload.get("prompt_id")
    if not prompt_id:
        # ComfyUI 在节点校验失败时返回 error/node_errors
        raise ComfyUIError(f"提交被拒绝: {json.dumps(payload, ensure_ascii=False)[:400]}")
    return str(prompt_id)


def history(prompt_id: str) -> dict | None:
    """返回该 prompt 的历史记录；未完成时返回 None。"""
    payload = json.loads(_request(f"/history/{urllib.parse.quote(prompt_id)}", timeout=30).decode("utf-8"))
    return payload.get(prompt_id)


def is_completed(record: dict) -> bool:
    return bool(record.get("status", {}).get("completed"))


def status_text(record: dict | None) -> str:
    if record is None:
        return "RUNNING"
    status = record.get("status", {})
    if status.get("completed"):
        return "SUCCEEDED" if status.get("status_str") == "success" else "FAILED"
    return "RUNNING"


def outputs(record: dict) -> list[dict]:
    """把 history 里的输出摊平成 [{filename, subfolder, type}, ...]。"""
    collected: list[dict] = []
    for node_output in (record.get("outputs") or {}).values():
        for value in node_output.values():
            items = value if isinstance(value, list) else [value]
            for item in items:
                if isinstance(item, dict) and item.get("filename"):
                    collected.append({
                        "filename": str(item["filename"]),
                        "subfolder": str(item.get("subfolder") or ""),
                        "type": str(item.get("type") or "output"),
                    })
    return collected


def download(item: dict) -> bytes:
    """通过 /view 下载产物（不需要文件系统访问权限，远端部署同样适用）。"""
    query = urllib.parse.urlencode({
        "filename": item["filename"],
        "subfolder": item.get("subfolder", ""),
        "type": item.get("type", "output"),
    })
    return _request(f"/view?{query}", timeout=300)


def queue_snapshot(timeout: int = 15) -> dict:
    """当前队列：运行中与排队中的 prompt。"""
    try:
        payload = json.loads(_request("/queue", timeout=timeout).decode("utf-8"))
    except (ComfyUIError, json.JSONDecodeError) as exc:
        raise ComfyUIError(f"无法读取 ComfyUI 队列: {exc}") from exc
    running = [str(entry[1]) for entry in payload.get("queue_running", []) if len(entry) > 1]
    pending = [str(entry[1]) for entry in payload.get("queue_pending", []) if len(entry) > 1]
    return {"running": running, "pending": pending, "depth": len(running) + len(pending)}


def delete_pending(prompt_id: str, timeout: int = 15) -> bool:
    """从队列里删除一个**尚未开始**的 prompt；返回是否已不在队列中。"""
    _request("/queue", {"delete": [prompt_id]}, timeout=timeout)
    snapshot = queue_snapshot(timeout=timeout)
    return prompt_id not in snapshot["pending"] and prompt_id not in snapshot["running"]


def interrupt(timeout: int = 15) -> None:
    """中断当前正在执行的 prompt。

    注意：这是 ComfyUI 的**全局**中断，会影响该实例上当前运行的任务，
    因此只能在私有单租户部署里由调用方显式要求时使用。
    """
    _request("/interrupt", {}, timeout=timeout)


def upload_image(filename: str, data: bytes, timeout: int = 120) -> str:
    """把图片上传到 ComfyUI 的 input 目录，返回它在 LoadImage 里可用的名字。"""
    boundary = "----productdirectorboundary"
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'.encode(),
        b"Content-Type: application/octet-stream\r\n\r\n",
        data,
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    request = urllib.request.Request(
        f"{base_url()}/upload/image",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ComfyUIError(f"上传图片失败 HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ComfyUIError(f"无法连接 ComfyUI 上传图片: {exc}") from exc
    name = payload.get("name")
    if not name:
        raise ComfyUIError(f"上传响应缺少 name: {json.dumps(payload, ensure_ascii=False)[:200]}")
    return str(name)


def build_hunyuan3d_graph(
    image: str,
    crop: tuple[int, int, int, int],
    prefix: str,
    seed: int = 20260912,
    resolution: int = 3072,
    octree: int = 256,
    steps: int = 50,
    cfg: float = 5.0,
    threshold: float = 0.6,
) -> dict:
    """Hunyuan3D 单视图重建图。

    采样默认 50 步 / cfg 5.0：现场工作流的 4 步 / cfg 1.0 会产出碎片几何
    （见 docs/reports/A07_V1_ACCEPTANCE.md 第 6 节）。
    """
    x, y, width, height = crop
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": image}},
        "2": {"class_type": "ImageCrop", "inputs": {"image": ["1", 0], "width": width, "height": height, "x": x, "y": y}},
        "3": {"class_type": "ImageOnlyCheckpointLoader", "inputs": {"ckpt_name": "hunyuan3d-dit-v2_fp16.safetensors"}},
        "4": {"class_type": "CLIPVisionEncode", "inputs": {"clip_vision": ["3", 1], "image": ["2", 0], "crop": "none"}},
        "5": {"class_type": "Hunyuan3Dv2Conditioning", "inputs": {"clip_vision_output": ["4", 0]}},
        "6": {"class_type": "EmptyLatentHunyuan3Dv2", "inputs": {"resolution": resolution, "batch_size": 1}},
        "7": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["3", 0],
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": "euler",
                "scheduler": "simple",
                "positive": ["5", 0],
                "negative": ["5", 1],
                "latent_image": ["6", 0],
                "denoise": 1.0,
            },
        },
        "8": {
            "class_type": "VAEDecodeHunyuan3D",
            "inputs": {"samples": ["7", 0], "vae": ["3", 2], "num_chunks": 8000, "octree_resolution": octree},
        },
        "9": {"class_type": "VoxelToMesh", "inputs": {"voxel": ["8", 0], "algorithm": "surface net", "threshold": threshold}},
        "10": {"class_type": "SaveGLB", "inputs": {"mesh": ["9", 0], "filename_prefix": prefix}},
    }


def build_h3_video_graph(
    reference_video: str,
    product_image: str,
    prompt: str,
    prefix: str,
    crop: tuple[int, int, int, int] = (0, 0, 512, 512),
    width: int = 576,
    height: int = 1024,
    length: int = 124,
    steps: int = 4,
    seed: int = 20260912,
    fps: int = 24,
) -> dict:
    """H3 参考视频 → 视频生成图（产品图作为 ref_image，参考视频作为 ref_video）。

    接线照抄现场已验证工作流的视频子图；与现场版本的差别是**不含**
    `ProductBlenderRender` 的多视图渲染（那属于 V3 产品保真链路），
    因此这里只声明"参考视频 + 产品图"这条已实现的能力。
    """
    x, y, crop_width, crop_height = crop
    video_name = os.getenv("PD_H3_VIDEO_MODEL", "minimax_h3_ref2va_pruned_int8_convrot.safetensors")
    clip_name = os.getenv("PD_H3_CLIP_MODEL", "qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors")
    video_vae = os.getenv("PD_H3_VIDEO_VAE", "minimax_h3_video_vae_fp16.safetensors")
    audio_vae = os.getenv("PD_H3_AUDIO_VAE", "minimax_h3_audio_vae_fp32.safetensors")
    lora_name = os.getenv("PD_H3_LORA", "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors")
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": video_name, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": clip_name, "type": "minimax", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": video_vae}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": audio_vae}},
        "5": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": lora_name, "strength_model": 1.0}},
        "6": {"class_type": "MiniMaxH3SigmaShift", "inputs": {"model": ["5", 0], "shift_video": 12.0, "shift_audio": 3.0}},
        "10": {"class_type": "LoadVideo", "inputs": {"file": reference_video}},
        "11": {"class_type": "GetVideoComponents", "inputs": {"video": ["10", 0]}},
        "12": {"class_type": "LoadImage", "inputs": {"image": product_image}},
        "13": {"class_type": "ImageCrop", "inputs": {"image": ["12", 0], "width": crop_width, "height": crop_height, "x": x, "y": y}},
        "7": {
            "class_type": "MiniMaxH3ReferenceToVideo",
            "inputs": {
                "clip": ["2", 0],
                "vae": ["3", 0],
                "audio_vae": ["4", 0],
                "prompt": prompt,
                "width": width,
                "height": height,
                "length": length,
                "ref_image_size": "match",
                "ref_images.ref_image_0": ["13", 0],
                "ref_videos.ref_video_0": ["11", 0],
            },
        },
        "8": {"class_type": "BasicGuider", "inputs": {"model": ["6", 0], "conditioning": ["7", 0]}},
        "9": {"class_type": "BasicScheduler", "inputs": {"model": ["6", 0], "scheduler": "simple", "steps": steps, "denoise": 1.0}},
        "14": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "15": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "16": {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "noise": ["15", 0],
                "guider": ["8", 0],
                "sampler": ["14", 0],
                "sigmas": ["9", 0],
                "latent_image": ["7", 1],
            },
        },
        "17": {"class_type": "VAEDecode", "inputs": {"samples": ["16", 0], "vae": ["3", 0]}},
        "18": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["16", 0], "vae": ["4", 0]}},
        "19": {"class_type": "CreateVideo", "inputs": {"images": ["17", 0], "fps": float(fps), "audio": ["18", 0]}},
        "20": {
            "class_type": "SaveVideo",
            "inputs": {"video": ["19", 0], "filename_prefix": prefix, "format": "mp4", "codec": "h264"},
        },
    }


def build_product_video_graph(
    reference_video: str,
    product_image: str,
    prompt: str,
    prefix: str,
    crop: tuple[int, int, int, int] = (0, 0, 512, 512),
    width: int = 576,
    height: int = 1024,
    length: int = 124,
    steps: int = 4,
    seed: int = 20260912,
    fps: int = 24,
    mesh_resolution: int = 3072,
    mesh_octree: int = 256,
    mesh_steps: int = 50,
    mesh_cfg: float = 5.0,
    scene: str = "studio",
    motion: str = "pan",
    size_cm: float = 35.0,
    seconds: int = 4,
    quality: str = "preview",
    photo_texture: str = "sheet",
    framing: str = "product",
) -> dict:
    """产品替换视频图：先在 H3 环境里重建产品网格，再用 Blender 出多视图，最后交给 H3 生成视频。

    与 `build_h3_video_graph()` 的差别（也是与现场工作流对齐的部分）：
    H3 的参考图不再只有原图裁切，而是 **原图裁切 + Blender 渲染的正面/45° 视图**，
    这正是现场"原视频产品替换"工作流的做法；
    产物由 `ProductBlenderRender` 提供，避免只用单张平面图导致的产品漂移。
    """
    graph = build_h3_video_graph(
        reference_video=reference_video,
        product_image=product_image,
        prompt=prompt,
        prefix=prefix,
        crop=crop,
        width=width,
        height=height,
        length=length,
        steps=steps,
        seed=seed,
        fps=fps,
    )
    checkpoint = os.getenv("PD_H3_MESH_CHECKPOINT", "hunyuan3d-dit-v2_fp16.safetensors")
    graph.update({
        # 重建子图：与现场工作流一致（50 步 / cfg 5.0，4 步会产出碎片几何）
        "29": {"class_type": "ImageOnlyCheckpointLoader", "inputs": {"ckpt_name": checkpoint}},
        "30": {"class_type": "CLIPVisionEncode", "inputs": {"clip_vision": ["29", 1], "image": ["13", 0], "crop": "none"}},
        "31": {"class_type": "Hunyuan3Dv2Conditioning", "inputs": {"clip_vision_output": ["30", 0]}},
        "32": {"class_type": "EmptyLatentHunyuan3Dv2", "inputs": {"resolution": mesh_resolution, "batch_size": 1}},
        "33": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["29", 0], "seed": seed, "steps": mesh_steps, "cfg": mesh_cfg,
                "sampler_name": "euler", "scheduler": "simple",
                "positive": ["31", 0], "negative": ["31", 1],
                "latent_image": ["32", 0], "denoise": 1.0,
            },
        },
        "34": {
            "class_type": "VAEDecodeHunyuan3D",
            "inputs": {"samples": ["33", 0], "vae": ["29", 2], "num_chunks": 8000, "octree_resolution": mesh_octree},
        },
        "35": {"class_type": "VoxelToMesh", "inputs": {"voxel": ["34", 0], "algorithm": "surface net", "threshold": 0.6}},
        "36": {
            "class_type": "ProductBlenderRender",
            "inputs": {
                "mesh": ["35", 0],
                "reference_image": ["13", 0],
                "scene": scene,
                "motion": motion,
                "size_cm": size_cm,
                "seconds": seconds,
                "quality": quality,
                "photo_texture": photo_texture,
                "framing": framing,
                "render_preview_video": False,
            },
        },
    })
    # H3 参考图：0 = 原图裁切，1/2 = Blender 正面与 45° 视图（现场工作流即此接线）
    graph["7"]["inputs"]["ref_images.ref_image_1"] = ["36", 1]
    graph["7"]["inputs"]["ref_images.ref_image_2"] = ["36", 3]
    return graph
