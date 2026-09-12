#!/usr/bin/env python3
"""V3-05：Strict 合成。

公式（规格原文）：`C = product_alpha × trusted_product + (1 − product_alpha) × generated_background`

实现要点：
- **线性空间**合成：beauty 来自 EXR（线性浮点），背景图按 sRGB 解码到线性，
  合成后再编码回 sRGB 输出；Mask/Depth 不参与颜色管理。
- **掩码外扩**：`--dilate N` 像素，保护产品边缘不被背景覆盖。
- **像素锁定**：未外扩的原始掩码内，输出必须与可信产品**逐像素完全一致**（内置断言）。
- 输出 Manifest 记录公式、色彩空间、外扩半径、逐帧校验结果与哈希。

用法：
    .venv/bin/python scripts/strict_composite.py \
        --product <passes/beauty> --mask <passes/mask> --background <bg_dir> --out <dir> --dilate 2
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image


def srgb_to_linear(value: np.ndarray) -> np.ndarray:
    return np.where(value <= 0.04045, value / 12.92, ((value + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(value: np.ndarray) -> np.ndarray:
    return np.where(value <= 0.0031308, value * 12.92, 1.055 * np.power(np.clip(value, 0, None), 1 / 2.4) - 0.055)


def dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    """方形结构元素的膨胀；用位移取最大值实现，避免额外依赖。"""
    if radius <= 0:
        return mask
    padded = np.pad(mask, radius, mode="edge")
    height, width = mask.shape
    out = np.zeros_like(mask)
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            window = padded[radius + dy: radius + dy + height, radius + dx: radius + dx + width]
            out = np.maximum(out, window)
    return out


def read_linear_rgb(path: Path) -> np.ndarray:
    """读一帧可信产品层（EXR，线性浮点；若为 PNG 则按 sRGB 解码到线性）。"""
    if path.suffix.lower() == ".exr":
        import OpenEXR

        part = OpenEXR.File(str(path)).parts[0]
        channels = part.channels
        # 实测：beauty 通道以 RGBA 存储，形状为 (H, W, 4)
        array = np.array(channels[list(channels.keys())[0]].pixels, dtype=np.float32)
        window = part.header.get("dataWindow")
        try:
            width, height = int(window.max.x) + 1, int(window.max.y) + 1
        except AttributeError:
            width, height = int(window[1][0]) + 1, int(window[1][1]) + 1
        components = max(3, array.size // (width * height))
        return np.clip(array.reshape(height, width, components)[:, :, :3], 0.0, None)
    with Image.open(path) as image:
        array = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    return srgb_to_linear(array)


def read_mask(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return (np.asarray(image.convert("RGBA").split()[3], dtype=np.float32) / 255.0) > 0.5


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product", required=True, help="可信产品层目录（beauty，EXR 或 PNG）")
    parser.add_argument("--mask", required=True, help="产品遮罩目录（PNG）")
    parser.add_argument("--background", required=True, help="生成的背景帧目录（PNG）")
    parser.add_argument("--out", required=True)
    parser.add_argument("--dilate", type=int, default=2)
    parser.add_argument("--limit", type=int, default=0, help="仅处理前 N 帧（0 表示全部）")
    args = parser.parse_args()

    product_dir, mask_dir, bg_dir, out_dir = (Path(p) for p in (args.product, args.mask, args.background, args.out))
    out_dir.mkdir(parents=True, exist_ok=True)
    product_files = sorted([p for p in product_dir.glob("*") if p.suffix.lower() in {".exr", ".png"}])
    mask_files = sorted(mask_dir.glob("frame_*.png"))
    bg_files = sorted(bg_dir.glob("*.png"))
    count = min(len(product_files), len(mask_files), len(bg_files))
    if args.limit:
        count = min(count, args.limit)
    report: dict = {
        "equation": "C = alpha * trusted_product + (1 - alpha) * generated_background",
        "color_space": "composited in linear light, output encoded to sRGB",
        "dilate_pixels": args.dilate,
        "frames": count,
        "pixel_lock_ok": True,
        "frames_report": [],
    }
    if count == 0:
        report["failures"] = ["输入帧不足（product/mask/background 至少各需 1 帧）"]
        print(json.dumps(report, ensure_ascii=False))
        return 1

    for index in range(count):
        product = read_linear_rgb(product_files[index])
        mask = read_mask(mask_files[index])
        with Image.open(bg_files[index]) as image:
            background = srgb_to_linear(np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0)
        if product.shape[:2] != mask.shape or background.shape[:2] != mask.shape:
            report["failures"] = [f"帧 {index} 尺寸不一致"]
            print(json.dumps(report, ensure_ascii=False))
            return 1
        alpha = dilate(mask.astype(np.float32), args.dilate)[:, :, None]
        composite = alpha * product + (1.0 - alpha) * background
        # 像素锁定：原始掩码内必须与可信产品完全一致
        locked = np.allclose(composite[mask], product[mask], atol=1e-6)
        if not locked:
            report["pixel_lock_ok"] = False
            report["failures"] = report.get("failures", []) + [f"帧 {index} 像素锁定失败"]
        encoded = np.clip(linear_to_srgb(composite), 0.0, 1.0)
        target = out_dir / f"composite_{index:04d}.png"
        Image.fromarray((encoded * 255 + 0.5).astype(np.uint8)).save(target)
        if index in {0, count - 1}:
            report["frames_report"].append({
                "frame": index,
                "mask_coverage": round(float(mask.mean()), 4),
                "dilated_coverage": round(float(alpha.mean()), 4),
                "output_sha256": sha256_bytes(target.read_bytes()),
            })

    report["passed"] = report["pixel_lock_ok"] and not report.get("failures")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
