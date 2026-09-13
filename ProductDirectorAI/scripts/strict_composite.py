#!/usr/bin/env python3
"""V3-05：Strict 合成。

公式（规格原文）：`C = product_alpha × trusted_product + (1 − product_alpha) × generated_background`

实现要点：
- **线性空间**合成：beauty 来自 EXR（线性浮点），背景图按 sRGB 解码到线性，
  合成后再编码回 sRGB 输出；Mask/Depth 不参与颜色管理。
- **掩码外扩**：`--dilate N` 像素，保护产品边缘不被背景覆盖。
- **像素锁定**：未外扩的原始掩码内，输出必须与可信产品**逐像素完全一致**（内置断言）。
- **独立层**（可选，`--layers shadow=<dir> reflection=<dir> occlusion=<dir>`，规格 9.5：
  阴影/反射/人物遮挡是单独层）：
  - shadow：16 位灰度 PNG，线性因子（1.0=无阴影），**乘算到背景**（线性空间）；
  - reflection：8 位 RGB PNG（sRGB 编码），解码后**加算到背景**（线性空间）；
  - occlusion：RGBA PNG（RGB=遮挡物颜色 sRGB，A=覆盖度），在最后**盖回产品上方**；
    遮挡区（occ_alpha>0 且在产品掩码内的像素）从像素锁定断言中豁免，
    豁免像素数逐帧记入 Manifest。
- 输出 Manifest 记录公式、色彩空间、外扩半径、逐帧校验结果与哈希。

用法：
    .venv/bin/python scripts/strict_composite.py \
        --product <passes/beauty> --mask <passes/mask> --background <bg_dir> --out <dir> --dilate 2 \
        [--layers shadow=<layers/shadow> reflection=<layers/reflection> occlusion=<layers/occlusion>]
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


def read_shadow_factor(path: Path) -> np.ndarray:
    """阴影因子层：16 位灰度 PNG，线性因子 = 值 / 65535（1.0 = 无阴影，不做 gamma）。"""
    with Image.open(path) as image:
        mode = image.mode
        array = np.asarray(image, dtype=np.float32)
    if array.ndim == 3:
        array = array[:, :, 0]
    scale = 65535.0 if mode.startswith("I") else 255.0
    return np.clip(array / scale, 0.0, 1.0)


def read_reflection(path: Path) -> np.ndarray:
    """反射层：8 位 RGB PNG，sRGB 编码，解码到线性后加算到背景。"""
    with Image.open(path) as image:
        array = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    return srgb_to_linear(array)


def read_occlusion(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """遮挡层：RGBA PNG（RGB=遮挡物颜色 sRGB，A=覆盖度）。返回 (线性 RGB, alpha)。"""
    with Image.open(path) as image:
        array = np.asarray(image.convert("RGBA"), dtype=np.float32) / 255.0
    return srgb_to_linear(array[:, :, :3]), array[:, :, 3]


LAYER_NAMES = ("shadow", "reflection", "occlusion")


def parse_layers(items: list[str] | None) -> dict[str, Path]:
    """解析 `--layers shadow=<dir> ...`；非法项直接拒绝（合成合同不接受静默忽略）。"""
    layers: dict[str, Path] = {}
    for item in items or []:
        name, sep, value = item.partition("=")
        if not sep or name not in LAYER_NAMES:
            raise SystemExit(f"--layers 项格式应为 shadow=<dir> reflection=<dir> occlusion=<dir>，收到: {item!r}")
        layers[name] = Path(value)
    return layers


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
    parser.add_argument("--layers", nargs="+", default=None, metavar="NAME=DIR",
                        help="V3-05 独立层：shadow=<dir> reflection=<dir> occlusion=<dir>（线性叠加，遮挡区豁免像素锁定）")
    args = parser.parse_args()

    product_dir, mask_dir, bg_dir, out_dir = (Path(p) for p in (args.product, args.mask, args.background, args.out))
    layers = parse_layers(args.layers)
    out_dir.mkdir(parents=True, exist_ok=True)
    product_files = sorted([p for p in product_dir.glob("*") if p.suffix.lower() in {".exr", ".png"}])
    mask_files = sorted(mask_dir.glob("frame_*.png"))
    bg_files = sorted(bg_dir.glob("*.png"))
    layer_files = {name: sorted(path.glob("*.png")) for name, path in layers.items()}
    count = min(len(product_files), len(mask_files), len(bg_files))
    if args.limit:
        count = min(count, args.limit)
    report: dict = {
        "equation": "C = alpha * trusted_product + (1 - alpha) * generated_background；"
                    "可选独立层：bg *= shadow；bg += reflection；C = occ_alpha * occluder + (1 - occ_alpha) * C",
        "color_space": "composited in linear light, output encoded to sRGB",
        "dilate_pixels": args.dilate,
        "frames": count,
        "pixel_lock_ok": True,
        "occlusion_exempted_pixels_total": 0,
        "frames_report": [],
    }
    if layers:
        report["layers"] = {name: {"dir": str(layers[name]), "frames": len(layer_files[name])} for name in layers}
    if count == 0:
        report["failures"] = ["输入帧不足（product/mask/background 至少各需 1 帧）"]
        print(json.dumps(report, ensure_ascii=False))
        return 1
    for name, files in layer_files.items():
        if len(files) < count:
            report["failures"] = [f"独立层 {name} 帧数 {len(files)} < {count}"]
            print(json.dumps(report, ensure_ascii=False))
            return 1

    layer_stats: dict[str, list[float]] = {name: [] for name in layers}
    for index in range(count):
        product = read_linear_rgb(product_files[index])
        mask = read_mask(mask_files[index])
        with Image.open(bg_files[index]) as image:
            background = srgb_to_linear(np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0)
        if product.shape[:2] != mask.shape or background.shape[:2] != mask.shape:
            report["failures"] = [f"帧 {index} 尺寸不一致"]
            print(json.dumps(report, ensure_ascii=False))
            return 1
        # 独立层：先在线性空间作用于背景（阴影乘算、反射加算）
        if "shadow" in layers:
            shadow = read_shadow_factor(layer_files["shadow"][index])
            if shadow.shape != mask.shape:
                report["failures"] = [f"帧 {index} 阴影层尺寸不一致"]
                print(json.dumps(report, ensure_ascii=False))
                return 1
            background = background * shadow[:, :, None]
            layer_stats["shadow"].append(float((shadow < 0.98).mean()))
        if "reflection" in layers:
            reflection = read_reflection(layer_files["reflection"][index])
            if reflection.shape[:2] != mask.shape:
                report["failures"] = [f"帧 {index} 反射层尺寸不一致"]
                print(json.dumps(report, ensure_ascii=False))
                return 1
            background = background + reflection
            layer_stats["reflection"].append(float((reflection > 1e-3).mean()))
        alpha = dilate(mask.astype(np.float32), args.dilate)[:, :, None]
        composite = alpha * product + (1.0 - alpha) * background
        # 遮挡层：把遮挡物盖回产品上方；遮挡区豁免像素锁定并计数
        exempted = 0
        locked_region = mask
        if "occlusion" in layers:
            occ_rgb, occ_alpha = read_occlusion(layer_files["occlusion"][index])
            if occ_alpha.shape != mask.shape:
                report["failures"] = [f"帧 {index} 遮挡层尺寸不一致"]
                print(json.dumps(report, ensure_ascii=False))
                return 1
            composite = occ_alpha[:, :, None] * occ_rgb + (1.0 - occ_alpha[:, :, None]) * composite
            exempted = int((mask & (occ_alpha > 0)).sum())
            locked_region = mask & (occ_alpha <= 0)
            layer_stats["occlusion"].append(float((occ_alpha > 0.5).mean()))
        report["occlusion_exempted_pixels_total"] += exempted
        # 像素锁定：未被遮挡的原始掩码内必须与可信产品完全一致
        locked = bool(np.allclose(composite[locked_region], product[locked_region], atol=1e-6))
        if not locked:
            report["pixel_lock_ok"] = False
            report["failures"] = report.get("failures", []) + [f"帧 {index} 像素锁定失败"]
        encoded = np.clip(linear_to_srgb(composite), 0.0, 1.0)
        target = out_dir / f"composite_{index:04d}.png"
        Image.fromarray((encoded * 255 + 0.5).astype(np.uint8)).save(target)
        if index in {0, count - 1}:
            entry: dict = {
                "frame": index,
                "mask_coverage": round(float(mask.mean()), 4),
                "dilated_coverage": round(float(alpha.mean()), 4),
                "output_sha256": sha256_bytes(target.read_bytes()),
            }
            if "occlusion" in layers:
                entry["occlusion_exempted_pixels"] = exempted
            report["frames_report"].append(entry)

    for name, values in layer_stats.items():
        report["layers"][name]["coverage_mean"] = round(float(np.mean(values)), 4) if values else 0.0
    report["passed"] = report["pixel_lock_ok"] and not report.get("failures")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
