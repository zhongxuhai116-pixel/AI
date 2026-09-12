"""V3-02：校验 Blender 5 布局的多通道产物。

布局：
- `passes/*.exr`：每帧一个多层 EXR（beauty / alpha / depth / normal），用 OpenEXR 读取；
- `passes/mask/*.png`：产品遮罩（只渲产品网格的 Alpha），用 Blender 图像 API 读取。

检查：帧数一致、遮罩近二值且覆盖合理、深度在产品区为有限正值、法线模长接近 1、
以及 EXR 与遮罩的产品区域基本吻合。

不依赖 Blender：用项目 venv 运行即可（Pillow 读遮罩 PNG，OpenEXR 读多层 EXR）。

用法：
    .venv/bin/python blender/scripts/validate_fidelity_passes.py \
        --passes <run_dir>/frames/passes --frames 72 --json <out.json>
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--passes", required=True)
    parser.add_argument("--frames", type=int, default=72)
    parser.add_argument("--json", default="")
    return parser.parse_args()


def read_mask(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.array(image.convert("RGBA").split()[3], dtype=np.float32) / 255.0


def read_exr(path: Path) -> dict:
    import OpenEXR

    handle = OpenEXR.File(str(path))
    part = handle.parts[0]
    channels = part.channels
    names = list(channels.keys()) if hasattr(channels, "keys") else [c.name for c in channels]
    window = part.header.get("dataWindow")
    try:
        width, height = int(window.max.x) + 1, int(window.max.y) + 1
    except AttributeError:
        # 该版本 OpenEXR 的 dataWindow 是 (min_xy, max_xy) 这样的两元素元组
        try:
            width = int(window[1][0]) + 1
            height = int(window[1][1]) + 1
        except (TypeError, IndexError):
            width = int(window[2]) - int(window[0]) + 1
            height = int(window[3]) - int(window[1]) + 1
    planes = {}
    for name in names:
        entry = channels[name]
        data = getattr(entry, "pixels", None)
        if data is None:
            continue
        array = np.array(data, dtype=np.float32)
        components = max(1, array.size // (width * height))
        planes[name] = array.reshape(height, width, components)
    return {"names": names, "planes": planes, "width": width, "height": height}


def pick(planes: dict, needle: str):
    for name, plane in planes.items():
        if needle.lower() in name.lower():
            # 通道可能以 RGBA 存储，取第一分量作为标量通道。
            return plane[:, :, 0] if plane.ndim == 3 else plane
    return None


def main() -> int:
    args = parse_args()
    root = Path(args.passes)
    report: dict = {"root": str(root), "frames_expected": args.frames, "failures": [], "frames": []}
    exr_root = sorted(root.glob("*.exr"))  # 旧布局：根目录多层 EXR
    channels = ("beauty", "alpha", "depth", "normal")
    per_channel = {name: sorted((root / name).glob("*.exr")) for name in channels}
    exr_files = exr_root or per_channel["beauty"]
    mask_files = sorted((root / "mask").glob("frame_*.png"))
    report["layout"] = "per-channel" if not exr_root else "single-multilayer"
    report["exr_frames"] = {name: len(files) for name, files in per_channel.items()} if not exr_root else len(exr_files)
    report["mask_frames"] = len(mask_files)
    if len(exr_files) != args.frames:
        report["failures"].append(f"EXR 帧数 {len(exr_files)} != {args.frames}")
    for name, files in per_channel.items():
        if len(files) != args.frames:
            report["failures"].append(f"{name} 通道帧数 {len(files)} != {args.frames}")
    if len(mask_files) != args.frames:
        report["failures"].append(f"遮罩帧数 {len(mask_files)} != {args.frames}")
    if not exr_files or not mask_files:
        report["passed"] = False
        print("FIDELITY_PASSES " + json.dumps(report, ensure_ascii=False))
        return 1

    sample = sorted({0, len(exr_files) // 2, len(exr_files) - 1})
    for index in sample:
        if exr_root:
            exr = read_exr(exr_files[index])
            planes = exr["planes"]
            channel_names = exr["names"]
        else:
            planes = {}
            channel_names = {}
            for name, files in per_channel.items():
                part = read_exr(files[index])
                channel_names[name] = part["names"]
                for suffix, plane in part["planes"].items():
                    planes[f"{name}.{suffix}"] = plane
        mask = read_mask(mask_files[index])
        alpha = pick(planes, "alpha") or pick(planes, "Alpha")
        depth = pick(planes, "depth") or pick(planes, "Depth") or pick(planes, ".Z")
        normal_x = pick(planes, "normal.X") or pick(planes, "Normal.X")
        entry: dict = {"frame_index": index, "exr_channels": channel_names}
        if alpha is not None:
            entry["alpha_coverage"] = round(float((alpha > 0.5).mean()), 4)
        entry["mask_coverage"] = round(float((mask > 0.5).mean()), 4)
        product = mask > 0.5
        entry["mask_binary_ratio"] = round(float(((mask > 0.95) | (mask < 0.05)).mean()), 4)
        if depth is not None and product.any():
            product_depth = depth[product]
            entry["depth_product_min"] = round(float(np.nanmin(product_depth)), 4)
            entry["depth_product_max"] = round(float(np.nanmax(product_depth)), 4)
        if normal_x is not None and product.any():
            normal_y = pick(planes, "normal.Y") or pick(planes, "Normal.Y")
            normal_z = pick(planes, "normal.Z") or pick(planes, "Normal.Z")
            if normal_y is not None and normal_z is not None:
                magnitude = np.sqrt(normal_x[product] ** 2 + normal_y[product] ** 2 + normal_z[product] ** 2)
                entry["normal_mean_magnitude"] = round(float(magnitude.mean()), 4)
        report["frames"].append(entry)
        if entry["mask_coverage"] <= 0.01:
            report["failures"].append(f"帧 {index} 遮罩覆盖过低 {entry['mask_coverage']}")
        if entry["mask_binary_ratio"] < 0.98:
            report["failures"].append(f"帧 {index} 遮罩不够二值 {entry['mask_binary_ratio']}")
        if "depth_product_min" in entry and (not math.isfinite(entry["depth_product_min"]) or entry["depth_product_min"] <= 0):
            report["failures"].append(f"帧 {index} 产品区深度无效 {entry['depth_product_min']}")
        if "normal_mean_magnitude" in entry and not 0.7 <= entry["normal_mean_magnitude"] <= 1.3:
            report["failures"].append(f"帧 {index} 法线模长异常 {entry['normal_mean_magnitude']}")
        if alpha is not None and abs((alpha > 0.5).mean() - (mask > 0.5).mean()) > 0.25:
            report["failures"].append(f"帧 {index} 遮罩与 Beauty Alpha 覆盖差异过大")

    report["passed"] = not report["failures"]
    print("FIDELITY_PASSES " + json.dumps(report, ensure_ascii=False))
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
