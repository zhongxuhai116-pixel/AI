"""V3-02：校验多通道产物（在 Blender 内运行，直接读 PNG 与 EXR）。

检查项：
1. 五个通道的帧数一致，且与计划帧数相符；
2. Alpha 既不是全透明也不是全覆盖，且接近二值（产品遮罩语义）；
3. Depth 在产品区域为有限正值，背景（无命中）为 0；
4. Normal 在产品区域接近单位向量（相机空间编码）；
5. Index 只包含产品索引（1）与背景（0），不依赖导入顺序。

用法：
    blender --background --python blender/scripts/validate_passes.py -- \
        --passes <run_dir>/frames/passes --frames 24 --json <out.json>
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import bpy
import numpy as np

CHANNELS = ("beauty", "alpha", "depth", "normal", "index")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--passes", required=True)
    parser.add_argument("--frames", type=int, default=24)
    parser.add_argument("--json", default="")
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])


def load_frame(channel: str, index: int, root: Path) -> np.ndarray:
    folder = root / channel
    candidates = sorted(folder.glob(f"frame_*{index:04d}.*")) or sorted(folder.glob(f"frame_*{index}.*"))
    if not candidates:
        raise RuntimeError(f"缺少通道帧: {channel} #{index}")
    image = bpy.data.images.load(str(candidates[0]), check_existing=False)
    pixels = np.array(image.pixels[:], dtype=np.float32)
    width, height = image.size
    channels = len(pixels) // (width * height)
    array = pixels.reshape(height, width, channels)
    bpy.data.images.remove(image)
    return array


def main() -> int:
    args = parse_args()
    root = Path(args.passes)
    report: dict = {"root": str(root), "frames_expected": args.frames, "channels": {}, "failures": []}
    sample_indices = sorted({1, max(1, args.frames // 2), args.frames})

    counts = {}
    for channel in CHANNELS:
        folder = root / channel
        counts[channel] = len(list(folder.glob("frame_*"))) if folder.exists() else 0
    report["frames_found"] = counts
    for channel in CHANNELS:
        if counts[channel] != args.frames:
            report["failures"].append(f"{channel} 帧数 {counts[channel]} != {args.frames}")

    alpha_stats, depth_stats, normal_stats, index_stats = [], [], [], []
    for index in sample_indices:
        alpha = load_frame("alpha", index, root)[:, :, 0]
        covered = float((alpha > 0.5).mean())
        binary_ratio = float(((alpha > 0.95) | (alpha < 0.05)).mean())
        alpha_stats.append({"frame": index, "coverage": round(covered, 4), "binary_ratio": round(binary_ratio, 4)})

        depth = load_frame("depth", index, root)[:, :, 0]
        product_mask = alpha > 0.5
        background = depth[~product_mask]
        product_depth = depth[product_mask]
        depth_stats.append({
            "frame": index,
            "product_min": round(float(product_depth.min()), 4) if product_depth.size else None,
            "product_max": round(float(product_depth.max()), 4) if product_depth.size else None,
            "background_zero_ratio": round(float((background == 0).mean()), 4) if background.size else None,
        })

        normal = load_frame("normal", index, root)[:, :, :3]
        magnitude = np.linalg.norm(normal[product_mask], axis=-1) if product_mask.any() else np.array([0.0])
        normal_stats.append({"frame": index, "mean_magnitude": round(float(magnitude.mean()), 4)})

        index_pass = load_frame("index", index, root)[:, :, 0]
        unique = sorted({int(round(float(value))) for value in np.unique(index_pass)})
        index_stats.append({"frame": index, "unique": unique})

    report["channels"] = {
        "alpha": alpha_stats, "depth": depth_stats, "normal": normal_stats, "index": index_stats,
    }
    for item in alpha_stats:
        if item["coverage"] <= 0.01:
            report["failures"].append(f"帧 {item['frame']} alpha 覆盖过低（{item['coverage']}）")
        if item["coverage"] >= 0.99:
            report["failures"].append(f"帧 {item['frame']} alpha 几乎全覆盖（{item['coverage']}）")
        if item["binary_ratio"] < 0.98:
            report["failures"].append(f"帧 {item['frame']} alpha 不够二值（{item['binary_ratio']}）")
    for item in depth_stats:
        if item["product_min"] is None or item["product_min"] <= 0 or not math.isfinite(item["product_min"]):
            report["failures"].append(f"帧 {item['frame']} 产品区深度无效（{item['product_min']}）")
        if item["background_zero_ratio"] is None or item["background_zero_ratio"] < 0.5:
            report["failures"].append(f"帧 {item['frame']} 背景无命中像素比例过低（{item['background_zero_ratio']}）")
    for item in normal_stats:
        if not 0.8 <= item["mean_magnitude"] <= 1.2:
            report["failures"].append(f"帧 {item['frame']} 法线模长异常（{item['mean_magnitude']}）")
    for item in index_stats:
        unexpected = [value for value in item["unique"] if value not in (0, 1)]
        if unexpected:
            report["failures"].append(f"帧 {item['frame']} 出现非产品索引 {unexpected}")
        if 1 not in item["unique"]:
            report["failures"].append(f"帧 {item['frame']} 未找到产品索引 1")

    report["passed"] = not report["failures"]
    print("PASSES_VALIDATION " + json.dumps(report, ensure_ascii=False))
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
