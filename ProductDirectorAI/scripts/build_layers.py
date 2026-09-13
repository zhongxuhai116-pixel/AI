#!/usr/bin/env python3
"""V3-05：从渲染差分构建独立层（阴影因子 / 反射能量 / 遮挡层校验）。

数学（全部在线性空间）：
- 干净底板 P = 无产品场景渲染（render_product.py --layers 的 layers/plate）；
  Beauty B = 含产品渲染（passes/beauty，EXR 线性浮点或 PNG sRGB）。
- 非产品像素上：
  - 阴影因子 S = clip(B / P, 0, 1)——光照被产品遮蔽后的残留比例（1.0 = 无阴影）；
  - 反射能量 R = clip(B - P, 0, ∞)——产品在地面上新增的光照（倒影/互反射/高光溢出）。
- 产品掩码内像素恒为 S=1、R=0：产品区由像素锁定保护，层只作用于背景。
- 遮挡层由 render_product.py --layers 直接落盘（RGBA PNG），本脚本只校验帧数、
  统计覆盖率，并在全零时如实注明"场景无遮挡物"。

落盘（PNG，校验器与 strict_composite.py 可读）：
- <out>/shadow/frame_XXXX.png：16 位灰度，值 = 线性因子 × 65535（不做 gamma）；
- <out>/reflection/frame_XXXX.png：8 位 RGB，sRGB 编码（与背景图同一解码路径）；
- <out>/layers_report.json：逐层覆盖率、极值、全零注明与输入哈希。

EXR 读取在函数内延迟导入 OpenEXR；本机托管 Python 用 PNG 输入即可跑通全部逻辑。

用法：
    .venv/bin/python scripts/build_layers.py \
        --beauty <passes/beauty> --plate <passes/layers/plate> --mask <passes/mask> \
        --occlusion <passes/layers/occlusion> --out <layers_out>
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image

# 阴影覆盖判定阈值：因子低于该值视为"受阴影影响"
SHADOW_COVERAGE_THRESHOLD = 0.98
# 底板亮度下限：低于该值的像素不参与除法（避免除零/噪声放大），因子记 1.0
PLATE_EPSILON = 1e-4


def srgb_to_linear(value: np.ndarray) -> np.ndarray:
    return np.where(value <= 0.04045, value / 12.92, ((value + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(value: np.ndarray) -> np.ndarray:
    return np.where(value <= 0.0031308, value * 12.92, 1.055 * np.power(np.clip(value, 0, None), 1 / 2.4) - 0.055)


def read_linear_rgb(path: Path) -> np.ndarray:
    """读一帧线性 RGB（EXR 延迟导入 OpenEXR；PNG 按 sRGB 解码到线性）。"""
    if path.suffix.lower() == ".exr":
        import OpenEXR

        part = OpenEXR.File(str(path)).parts[0]
        channels = part.channels
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


def compute_layers(beauty: np.ndarray, plate: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """差分提取 (阴影因子, 反射能量)，输入均为线性 RGB，mask 为产品布尔掩码。"""
    if beauty.shape != plate.shape or beauty.shape[:2] != mask.shape:
        raise ValueError(f"尺寸不一致: beauty{beauty.shape} plate{plate.shape} mask{mask.shape}")
    safe_plate = np.maximum(plate, PLATE_EPSILON)
    shadow = np.clip(beauty / safe_plate, 0.0, 1.0).min(axis=2).astype(np.float32)
    reflection = np.clip(beauty - plate, 0.0, None).astype(np.float32)
    shadow[mask] = 1.0
    reflection[mask] = 0.0
    return shadow, reflection


def save_shadow(shadow: np.ndarray, target: Path) -> None:
    """16 位灰度 PNG：线性因子 × 65535，不做 gamma。"""
    Image.fromarray((np.clip(shadow, 0.0, 1.0) * 65535 + 0.5).astype(np.uint16)).save(target)


def save_reflection(reflection: np.ndarray, target: Path) -> None:
    """8 位 RGB PNG：线性能量先裁剪到 [0,1] 再 sRGB 编码（与背景图同一解码路径）。"""
    encoded = np.clip(linear_to_srgb(np.clip(reflection, 0.0, 1.0)), 0.0, 1.0)
    Image.fromarray((encoded * 255 + 0.5).astype(np.uint8)).save(target)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="V3-05：beauty × 干净底板差分构建阴影/反射独立层")
    parser.add_argument("--beauty", required=True,
                        help="含产品完整场景渲染目录（层提取用 layers/plate_full 的 EXR；EXR 延迟导入 OpenEXR）")
    parser.add_argument("--plate", required=True, help="干净底板目录（EXR 或 PNG，render_product.py --layers 产物）")
    parser.add_argument("--mask", required=True, help="产品掩码目录（frame_*.png）")
    parser.add_argument("--occlusion", default="", help="遮挡层目录（RGBA PNG；提供时校验帧数并统计覆盖率）")
    parser.add_argument("--out", required=True, help="层输出根目录（shadow/ 与 reflection/ 落在其下）")
    parser.add_argument("--limit", type=int, default=0, help="仅处理前 N 帧（0 表示全部）")
    args = parser.parse_args()

    beauty_dir, plate_dir, mask_dir, out_root = (Path(p) for p in (args.beauty, args.plate, args.mask, args.out))
    shadow_dir = out_root / "shadow"
    reflection_dir = out_root / "reflection"
    shadow_dir.mkdir(parents=True, exist_ok=True)
    reflection_dir.mkdir(parents=True, exist_ok=True)
    beauty_files = sorted([p for p in beauty_dir.glob("*") if p.suffix.lower() in {".exr", ".png"}])
    plate_files = sorted(plate_dir.glob("frame_*.exr")) or sorted(plate_dir.glob("frame_*.png"))
    mask_files = sorted(mask_dir.glob("frame_*.png"))
    occlusion_files = sorted(Path(args.occlusion).glob("frame_*.png")) if args.occlusion else []
    count = min(len(beauty_files), len(plate_files), len(mask_files))
    if args.limit:
        count = min(count, args.limit)
    report: dict = {
        "task": "V3-05 独立层构建（阴影/反射/遮挡）",
        "math": "shadow = clip(beauty/plate, 0, 1).min(RGB)；reflection = clip(beauty-plate, 0, inf)；产品掩码内 shadow=1、reflection=0；线性空间",
        "encoding": {
            "shadow": "16 位灰度 PNG，线性因子 × 65535，无 gamma",
            "reflection": "8 位 RGB PNG，线性能量裁剪到 [0,1] 后 sRGB 编码",
            "occlusion": "RGBA PNG（渲染侧直接落盘，本脚本只校验）",
        },
        "frames": count,
        "notes": [],
        "passed": True,
    }
    if count == 0:
        report["passed"] = False
        report["failures"] = ["输入帧不足（beauty/plate/mask 至少各需 1 帧）"]
        print(json.dumps(report, ensure_ascii=False))
        return 1
    if occlusion_files and len(occlusion_files) < count:
        report["passed"] = False
        report["failures"] = [f"遮挡层帧数 {len(occlusion_files)} < {count}"]
        print(json.dumps(report, ensure_ascii=False))
        return 1

    shadow_coverages: list[float] = []
    shadow_mins: list[float] = []
    reflection_maxs: list[float] = []
    reflection_coverages: list[float] = []
    for index in range(count):
        beauty = read_linear_rgb(beauty_files[index])
        plate = read_linear_rgb(plate_files[index])
        mask = read_mask(mask_files[index])
        shadow, reflection = compute_layers(beauty, plate, mask)
        shadow_target = shadow_dir / f"frame_{index:04d}.png"
        reflection_target = reflection_dir / f"frame_{index:04d}.png"
        save_shadow(shadow, shadow_target)
        save_reflection(reflection, reflection_target)
        shadow_coverages.append(float((shadow < SHADOW_COVERAGE_THRESHOLD).mean()))
        shadow_mins.append(float(shadow.min()))
        reflection_maxs.append(float(reflection.max()))
        reflection_coverages.append(float((reflection > 1e-3).mean()))

    occlusion_coverages: list[float] = []
    for index in range(count):
        if not occlusion_files:
            break
        with Image.open(occlusion_files[index]) as image:
            alpha = np.asarray(image.convert("RGBA").split()[3], dtype=np.float32) / 255.0
        occlusion_coverages.append(float((alpha > 0.5).mean()))

    report["shadow"] = {
        "dir": str(shadow_dir),
        "frames": count,
        "coverage_mean": round(float(np.mean(shadow_coverages)), 4),
        "factor_min": round(float(min(shadow_mins)), 4),
        "first_frame_sha256": sha256_bytes((shadow_dir / "frame_0000.png").read_bytes()),
    }
    report["reflection"] = {
        "dir": str(reflection_dir),
        "frames": count,
        "coverage_mean": round(float(np.mean(reflection_coverages)), 4),
        "energy_max": round(float(max(reflection_maxs)), 4),
        "first_frame_sha256": sha256_bytes((reflection_dir / "frame_0000.png").read_bytes()),
    }
    if max(reflection_maxs) <= 1e-3:
        report["notes"].append("反射层全零：底板差分无新增能量（场景无显著反射面/互反射）")
    if occlusion_files:
        report["occlusion"] = {
            "dir": args.occlusion,
            "frames": len(occlusion_files),
            "coverage_mean": round(float(np.mean(occlusion_coverages)), 4) if occlusion_coverages else 0.0,
        }
        if occlusion_coverages and max(occlusion_coverages) == 0.0:
            report["notes"].append("遮挡层全零：场景无遮挡物（规格要求的全零层，已在合成侧按无遮挡处理）")
    else:
        report["occlusion"] = {"dir": "", "frames": 0, "coverage_mean": 0.0}
        report["notes"].append("未提供遮挡层目录，仅校验阴影/反射两层")

    (out_root / "layers_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
