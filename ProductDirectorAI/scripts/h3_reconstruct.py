#!/usr/bin/env python3
"""V2 重建路径：用云端 ComfyUI 的 Hunyuan3D 从产品图重建 GLB。

节点图照抄现场已验证的 3D 子图（`ImageOnlyCheckpointLoader` 提供 MODEL/
CLIP_VISION/VAE，采样用 euler + simple + 4 步），只保留重建所需的最小链路：

    LoadImage -> ImageCrop -> CLIPVisionEncode -> Hunyuan3Dv2Conditioning
      -> EmptyLatentHunyuan3Dv2 -> KSampler -> VAEDecodeHunyuan3D
      -> VoxelToMesh -> SaveGLB

用法：
    python scripts/h3_reconstruct.py --image 微信图片_20260910103140_2459_7.png \
        --crop 160 5 360 500 --prefix pd-product-recon/robot-front --out /tmp/robot.glb
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def api(base: str, path: str, payload: dict | None = None, timeout: int = 60):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{base}{path}",
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def build_workflow(
    image: str, crop: list[int], prefix: str, seed: int, resolution: int, octree: int,
    steps: int = 4, cfg: float = 1.0, threshold: float = 0.6,
) -> dict:
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


COMFY_ROOT = Path(os.environ.get("PD_COMFY_ROOT", "/home/ubuntu/comfy-h3"))


def resolve_glb(outputs: dict) -> Path:
    """从 ComfyUI 的 history outputs 里解析出 GLB 的磁盘路径。

    SaveGLB 返回的是 `{"filename": ..., "subfolder": ..., "type": "output"}`
    这样的结构（不是纯字符串），必须按 subfolder 拼回路径。
    """

    def candidates():
        for node_output in outputs.values():
            for value in node_output.values():
                items = value if isinstance(value, list) else [value]
                for item in items:
                    if isinstance(item, dict):
                        filename = item.get("filename") or ""
                        if not str(filename).lower().endswith(".glb"):
                            continue
                        subfolder = item.get("subfolder") or ""
                        kind = item.get("type") or "output"
                        root = COMFY_ROOT / (kind if kind in {"output", "input", "temp"} else "output")
                        yield root / subfolder / str(filename)
                    elif isinstance(item, str) and item.lower().endswith(".glb"):
                        path = Path(item)
                        yield path if path.is_absolute() else COMFY_ROOT / "output" / path

    for candidate in candidates():
        if candidate.exists():
            return candidate
    return next(candidates(), Path("/nonexistent.glb"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8188")
    parser.add_argument("--image", required=True, help="ComfyUI input 目录中的图片名")
    parser.add_argument("--crop", nargs=4, type=int, metavar=("X", "Y", "W", "H"), required=True)
    parser.add_argument("--prefix", required=True, help="SaveGLB 的 filename_prefix")
    parser.add_argument("--out", required=True, help="把生成的 GLB 复制到这里")
    parser.add_argument("--seed", type=int, default=20260912)
    parser.add_argument("--resolution", type=int, default=3072)
    parser.add_argument("--octree", type=int, default=256)
    parser.add_argument("--steps", type=int, default=4, help="采样步数（现场工作流为 4）")
    parser.add_argument("--cfg", type=float, default=1.0)
    parser.add_argument("--threshold", type=float, default=0.6, help="VoxelToMesh 阈值")
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()

    workflow = build_workflow(
        args.image, args.crop, args.prefix, args.seed, args.resolution, args.octree,
        steps=args.steps, cfg=args.cfg, threshold=args.threshold,
    )
    submitted = api(args.base, "/prompt", {"prompt": workflow, "client_id": "productdirector-recon"})
    prompt_id = submitted.get("prompt_id")
    if not prompt_id:
        print(json.dumps(submitted, ensure_ascii=False), file=sys.stderr)
        return 1
    print(f"prompt_id={prompt_id}", flush=True)

    started = time.time()
    history: dict = {}
    while time.time() - started < args.timeout:
        try:
            history = api(args.base, f"/history/{prompt_id}", timeout=30)
        except urllib.error.URLError as exc:
            print(f"history 查询失败（继续等待）: {exc}", flush=True)
        if prompt_id in history:
            break
        time.sleep(5)
    else:
        print("超时：重建未在限定时间内完成", file=sys.stderr)
        return 1

    record = history[prompt_id]
    status = record.get("status", {})
    print(json.dumps({"status": status.get("status_str"), "completed": status.get("completed"),
                      "seconds": round(time.time() - started, 1)}, ensure_ascii=False))
    if not status.get("completed"):
        print(json.dumps(record.get("outputs", {}), ensure_ascii=False)[:2000], file=sys.stderr)
        return 1

    outputs = record.get("outputs", {})
    source = resolve_glb(outputs)
    if not source.exists():
        print(json.dumps(outputs, ensure_ascii=False)[:2000], file=sys.stderr)
        print(f"找不到输出文件: {source}", file=sys.stderr)
        return 1

    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    print(json.dumps({"glb": str(target), "bytes": target.stat().st_size}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
