"""V3-02：校验 Blender 5 布局的多通道产物。

布局：
- `passes/*.exr`：每帧一个多层 EXR（beauty / alpha / depth / normal），用 OpenEXR 读取；
- `passes/mask/*.png`：产品遮罩（只读产品网格的 Alpha），用 Pillow 读取。

检查：精确帧数/起始帧号、同帧对应、通道基本完整、有限数值、深度在产品区为有限正值、
法线分量齐全且模长接近 1、以及 EXR 与遮罩的产品区域在像素级轮廓上吻合。

不依赖 Blender：用项目 venv 运行即可（Pillow 读遮罩 PNG，OpenEXR 读多层 EXR）。

用法：
    .venv/bin/python blender/scripts/validate_fidelity_passes.py \
        --passes <run_dir>/frames/passes --frames 72 --start-frame 1 --json <out.json>
"""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--passes", required=True)
    parser.add_argument("--frames", type=int, default=72)
    parser.add_argument("--start-frame", type=int, default=None, help="起始帧号；未提供时按已发现文件的最小号推断")
    parser.add_argument("--json", default="")
    return parser.parse_args()


def parse_frame_index(path: Path) -> int | None:
    match = re.search(r"(?:^|[^0-9])(\d+)(?:$|[^0-9])", path.stem)
    return int(match.group(1)) if match else None


def discover_indexed_frames(paths: Iterable[Path], *, label: str, failures: list[str]) -> dict[int, Path]:
    frames: dict[int, Path] = {}
    for path in sorted(paths):
        index = parse_frame_index(path)
        if index is None:
            failures.append(f"{label} 包含不可识别帧名：{path.name}")
            continue
        if index in frames:
            failures.append(f"{label} 存在重复帧 {index}: {frames[index].name}, {path.name}")
            continue
        frames[index] = path
    return frames


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
            return plane[:, :, 0] if plane.ndim == 3 else plane
    return None


def pick_first(planes: dict, *needles: str):
    """按顺序返回第一个命中的通道（不能用 or，numpy 数组的真值判断会报错）。"""
    for needle in needles:
        found = pick(planes, needle)
        if found is not None:
            return found
    return None


def pick_raw(planes: dict, *needles: str):
    """返回未降维的原始 plane，仅用于 beauty 等多分量通道。"""
    for needle in needles:
        lowered = needle.lower()
        for name, plane in planes.items():
            if lowered in name.lower():
                return plane
    return None


def pick_token(planes: dict, channel_tokens: set[str], component_tokens: set[str] | None = None):
    """按通道名/分量名的完整 token 匹配标量通道，避免 depth 误配 normal.Z。"""
    channel_tokens = {token.lower() for token in channel_tokens}
    component_tokens = {token.lower() for token in component_tokens} if component_tokens is not None else None
    for name, plane in planes.items():
        tokens = [token.lower() for token in name.split(".")]
        if not any(token in channel_tokens for token in tokens):
            continue
        if component_tokens is not None and not any(token in component_tokens for token in tokens):
            continue
        return plane[:, :, 0] if plane.ndim == 3 else plane
    return None


def _compare_sets(base: set[int], peer: set[int], label: str, failures: list[str]) -> None:
    missing = sorted(base - peer)
    extra = sorted(peer - base)
    if missing:
        failures.append(f"{label} 缺少帧：{missing}")
    if extra:
        failures.append(f"{label} 多出帧：{extra}")


def _has_rgb_planes(planes: dict) -> bool:
    """per-channel 布局下确认存在独立的 R/G/B 分量，避免只取到 R 就判定完整。"""
    found = set()
    for name in planes:
        tokens = [token.lower() for token in name.split(".")]
        if "r" in tokens or "red" in tokens:
            found.add("r")
        if "g" in tokens or "green" in tokens:
            found.add("g")
        if "b" in tokens or "blue" in tokens:
            found.add("b")
    return found >= {"r", "g", "b"}


def main() -> int:
    args = parse_args()
    root = Path(args.passes)
    report: dict = {"root": str(root), "frames_expected": args.frames, "start_frame": args.start_frame,
                    "failures": [], "frames": []}
    failures: list[str] = report["failures"]

    exr_root = discover_indexed_frames(root.glob("*.exr"), label="single-layout exr", failures=failures)
    channels = ("beauty", "alpha", "depth", "normal")
    per_channel = {name: discover_indexed_frames((root / name).glob("*.exr"), label=f"{name} 通道", failures=failures) for name in channels}
    mask_files = discover_indexed_frames((root / "mask").glob("*.png"), label="mask", failures=failures)

    use_single_layout = bool(exr_root)
    layout = "single-multilayer" if use_single_layout else "per-channel"
    report["layout"] = layout

    if args.frames <= 0:
        failures.append("frames 必须为正整数")

    discovered = set(exr_root.keys())
    for name in channels:
        discovered.update(per_channel[name].keys())
    discovered.update(mask_files.keys())
    start_frame = args.start_frame if args.start_frame is not None else (min(discovered) if discovered else 1)
    report["start_frame"] = start_frame
    expected_frames = set(range(start_frame, start_frame + args.frames)) if args.frames > 0 else set()
    report["expected_frames"] = sorted(expected_frames)

    if use_single_layout:
        _compare_sets(expected_frames, set(exr_root.keys()), "single-layout exr", failures)
        report["exr_frames"] = len(exr_root)
    else:
        report["exr_frames"] = {name: len(files) for name, files in per_channel.items()}
        if not per_channel["beauty"]:
            failures.append("未找到 beauty 通道 EXR")
        for name, files in per_channel.items():
            _compare_sets(expected_frames, set(files.keys()), f"{name} 通道", failures)

    _compare_sets(expected_frames, set(mask_files.keys()), "遮罩", failures)
    report["mask_frames"] = len(mask_files)
    if not expected_frames:
        failures.append("未找到可用帧号范围")

    for index in sorted(expected_frames):
        planes: dict = {}
        channel_names = {}
        if use_single_layout:
            if index not in exr_root:
                failures.append(f"帧 {index} 缺少 single-layout EXR")
                continue
            try:
                exr = read_exr(exr_root[index])
            except Exception as exc:
                failures.append(f"帧 {index} 读取 EXR 失败：{exc}")
                continue
            planes = exr["planes"]
            channel_names = exr["names"]
        else:
            for name, files in per_channel.items():
                if index not in files:
                    continue
                try:
                    part = read_exr(files[index])
                except Exception as exc:
                    failures.append(f"帧 {index} 通道 {name} 读取失败：{exc}")
                    continue
                channel_names[name] = part["names"]
                for suffix, plane in part["planes"].items():
                    planes[f"{name}.{suffix}"] = plane

        if index not in mask_files:
            failures.append(f"帧 {index} 缺少遮罩文件")
            continue
        mask = read_mask(mask_files[index])
        beauty = pick_raw(planes, "beauty", "Beauty")
        alpha = pick_token(planes, {"alpha", "a"}, {"alpha", "a", "v"})
        depth = pick_token(planes, {"depth"}, {"depth", "v", "z"})
        normal_x = pick_token(planes, {"normal"}, {"x"})
        normal_y = pick_token(planes, {"normal"}, {"y"})
        normal_z = pick_token(planes, {"normal"}, {"z"})

        entry: dict = {"frame_index": index, "exr_channels": channel_names, "mask_frame": str(mask_files[index])}
        product = mask > 0.5
        entry["mask_coverage"] = round(float(product.mean()), 4)
        entry["mask_binary_ratio"] = round(float(((mask > 0.95) | (mask < 0.05)).mean()), 4)

        if beauty is None:
            failures.append(f"帧 {index} 缺少 beauty 通道")
        elif beauty.ndim != 3 or beauty.shape[2] < 3 or beauty.shape[:2] != mask.shape:
            failures.append(f"帧 {index} beauty 形状/分量无效：{getattr(beauty, 'shape', None)} / {mask.shape}")
        elif not np.isfinite(beauty).all():
            failures.append(f"帧 {index} beauty 包含非有限值")

        if alpha is None:
            failures.append(f"帧 {index} 缺少 alpha 通道")
        else:
            entry["alpha_coverage"] = round(float((alpha > 0.5).mean()), 4)
            if alpha.shape != mask.shape:
                failures.append(f"帧 {index} alpha 与遮罩尺寸不一致：{alpha.shape} / {mask.shape}")
            elif not np.isfinite(alpha).all():
                failures.append(f"帧 {index} alpha 包含非有限值")
            elif np.any(alpha < -1e-4) or np.any(alpha > 1.0001):
                failures.append(f"帧 {index} alpha 超出 [0,1]")

        if depth is None:
            failures.append(f"帧 {index} 缺少 depth 通道")
        elif depth.shape != mask.shape:
            failures.append(f"帧 {index} depth 与遮罩尺寸不一致：{depth.shape} / {mask.shape}")
        elif not np.isfinite(depth).all():
            failures.append(f"帧 {index} depth 包含非有限值")
        elif product.any():
            product_depth = depth[product]
            if product_depth.size == 0:
                failures.append(f"帧 {index} 产品区域为空，无法做深度校验")
            else:
                entry["depth_product_min"] = round(float(np.nanmin(product_depth)), 4)
                entry["depth_product_max"] = round(float(np.nanmax(product_depth)), 4)
                if not np.isfinite(product_depth).all():
                    failures.append(f"帧 {index} 产品区 depth 包含非有限值")

        if normal_x is None or normal_y is None or normal_z is None:
            failures.append(f"帧 {index} 法线通道不完整（需要 normal.X/Y/Z）")
        elif not (normal_x.shape == normal_y.shape == normal_z.shape == mask.shape):
            failures.append(f"帧 {index} 法线形状不一致")
        elif not (np.isfinite(normal_x).all() and np.isfinite(normal_y).all() and np.isfinite(normal_z).all()):
            failures.append(f"帧 {index} 法线分量包含非有限值")
        elif product.any():
            magnitude = np.sqrt(normal_x[product] ** 2 + normal_y[product] ** 2 + normal_z[product] ** 2)
            if magnitude.size == 0:
                failures.append(f"帧 {index} 产品区域为空，无法做法线校验")
            else:
                entry["normal_mean_magnitude"] = round(float(np.mean(magnitude)), 4)
                finite_ratio = float(np.mean(np.isfinite(magnitude)))
                entry["normal_product_fraction_finite"] = round(finite_ratio, 4)
                if finite_ratio < 1.0:
                    failures.append(f"帧 {index} 法线包含非有限值")

        report["frames"].append(entry)

        if alpha is not None and alpha.shape == mask.shape:
            alpha_mask = alpha > 0.5
            intersection = np.logical_and(alpha_mask, product).sum()
            union = np.logical_or(alpha_mask, product).sum()
            iou = float(intersection / union) if union else 0.0
            entry["mask_alpha_iou"] = round(iou, 4)
            if iou < 0.995:
                failures.append(f"帧 {index} 遮罩与 alpha 轮廓 IoU 过低：{iou:.4f}")

        if entry["mask_coverage"] <= 0.01:
            failures.append(f"帧 {index} 遮罩覆盖过低 {entry['mask_coverage']}")
        if entry["mask_binary_ratio"] < 0.98:
            failures.append(f"帧 {index} 遮罩不够二值 {entry['mask_binary_ratio']}")

        if "depth_product_min" in entry and (
            not math.isfinite(entry["depth_product_min"]) or entry["depth_product_min"] <= 0
        ):
            failures.append(f"帧 {index} 产品区深度无效 {entry['depth_product_min']}")

        if "normal_product_fraction_finite" in entry and entry["normal_product_fraction_finite"] < 1.0:
            failures.append(f"帧 {index} 法线覆盖区域存在非有效值")

        if "normal_mean_magnitude" in entry and not 0.7 <= entry["normal_mean_magnitude"] <= 1.3:
            failures.append(f"帧 {index} 法线模长异常 {entry['normal_mean_magnitude']}")

    report["passed"] = not report["failures"]
    print("FIDELITY_PASSES " + json.dumps(report, ensure_ascii=False))
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
