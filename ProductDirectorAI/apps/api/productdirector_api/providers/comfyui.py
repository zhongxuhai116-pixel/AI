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
