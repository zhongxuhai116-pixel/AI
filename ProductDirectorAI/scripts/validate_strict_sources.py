"""V3-02/03：Strict 输入来源一致性校验（不依赖 Blender）。

职责：
- 校验 passes / product / mask / background 的精确帧集合同；
- 校验产品层与 Beauty 在产品 Mask 核心区域（向内腐蚀 2px）线性空间一致；
- 校验可信 Alpha 存在且与 Mask 尺寸一致，Mask/背景帧号经显式偏移映射；
- 输出 JSON report，任何失败返回非零，不写产物。

用法：
    .venv/bin/python scripts/validate_strict_sources.py \
        --passes strict/passes --product strict/product --mask strict/mask \
        --background strict/background --frames 72 --start-frame 1 \
        --background-frame-offset 0 --json strict/source_report.json
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--passes", required=True)
    parser.add_argument("--product", required=True)
    parser.add_argument("--mask", required=True)
    parser.add_argument("--background", required=True)
    parser.add_argument("--frames", type=int, required=True)
    parser.add_argument("--start-frame", type=int, default=None)
    parser.add_argument("--background-frame-offset", type=int, default=0)
    parser.add_argument("--tolerance", type=float, default=0.004)
    parser.add_argument("--json", default="")
    return parser.parse_args()


def parse_frame_index(path: Path) -> int | None:
    match = re.search(r"(?:^|[^0-9])(\d+)(?:$|[^0-9])", path.stem)
    return int(match.group(1)) if match else None


def discover(paths: Iterable[Path], *, label: str, failures: list[str]) -> dict[int, Path]:
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


def _compare_sets(base: set[int], peer: set[int], label: str, failures: list[str]) -> None:
    missing = sorted(base - peer)
    extra = sorted(peer - base)
    if missing:
        failures.append(f"{label} 缺少帧：{missing}")
    if extra:
        failures.append(f"{label} 多出帧：{extra}")


def srgb_to_linear(value: np.ndarray) -> np.ndarray:
    return np.where(value <= 0.04045, value / 12.92, ((value + 0.055) / 1.055) ** 2.4)


def _open_exr(path: Path):
    import OpenEXR

    handle = OpenEXR.File(str(path))
    part = handle.parts[0]
    channels = part.channels
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
    return channels, width, height


def _exr_planes(path: Path) -> dict[str, np.ndarray]:
    channels, width, height = _open_exr(path)
    names = list(channels.keys()) if hasattr(channels, "keys") else [c.name for c in channels]
    planes: dict[str, np.ndarray] = {}
    for name in names:
        entry = channels[name]
        data = getattr(entry, "pixels", None)
        if data is None:
            continue
        array = np.array(data, dtype=np.float32)
        components = max(1, array.size // (width * height))
        planes[name] = array.reshape(height, width, components)
    return planes


def _first_component(array: np.ndarray) -> np.ndarray:
    return array[:, :, 0] if array.ndim == 3 else array


def _pick_plane(planes: dict[str, np.ndarray], *needles: str):
    for needle in needles:
        lowered = needle.lower()
        for name, plane in planes.items():
            if lowered in name.lower():
                return plane
    return None


def _pick_alpha(planes: dict[str, np.ndarray]) -> np.ndarray | None:
    for name, plane in planes.items():
        tokens = [token.lower() for token in name.split(".")]
        if any(token in {"alpha", "a"} for token in tokens):
            return _first_component(plane)
    beauty = _pick_plane(planes, "beauty", "rgba")
    if beauty is not None and beauty.ndim == 3 and beauty.shape[2] >= 4:
        return beauty[:, :, 3]
    return None


def _linear_rgb(planes: dict[str, np.ndarray]) -> np.ndarray | None:
    plane = _pick_plane(planes, "beauty", "rgb", "rgba")
    if plane is None and planes:
        plane = next(iter(planes.values()))
    if plane is None:
        return None
    if plane.ndim == 3 and plane.shape[2] >= 3:
        return np.clip(plane[:, :, :3], 0.0, None)
    return np.clip(np.repeat(_first_component(plane)[:, :, None], 3, axis=2), 0.0, None)


def read_mask(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGBA").split()[3], dtype=np.float32) / 255.0 > 0.5


def read_product(path: Path) -> tuple[np.ndarray, np.ndarray | None]:
    if path.suffix.lower() == ".exr":
        planes = _exr_planes(path)
        rgb = _linear_rgb(planes)
        alpha = _pick_alpha(planes)
        if rgb is None:
            raise ValueError(f"产品层 EXR 不含 RGB: {path}")
        return rgb, alpha
    with Image.open(path) as image:
        rgba = image.convert("RGBA")
        rgb = np.asarray(rgba.convert("RGB"), dtype=np.float32) / 255.0
        alpha = np.asarray(rgba.split()[3], dtype=np.float32) / 255.0 if "A" in rgba.getbands() else None
    return srgb_to_linear(rgb), alpha


def read_beauty(pass_root: Path, frame: int) -> np.ndarray | None:
    single = pass_root / f"frame_{frame:04d}.exr"
    if single.exists():
        rgb = _linear_rgb(_exr_planes(single))
        if rgb is not None:
            return rgb
    per_channel = pass_root / "beauty" / f"frame_{frame:04d}.exr"
    if per_channel.exists():
        rgb = _linear_rgb(_exr_planes(per_channel))
        if rgb is not None:
            return rgb
    return None


def erode(mask: np.ndarray, radius: int) -> np.ndarray:
    """方形结构元素的二值腐蚀；不依赖 scipy。"""
    out = mask.copy()
    for _ in range(radius):
        previous = out.copy()
        out = previous.copy()
        out[:-1, :] &= previous[1:, :]
        out[1:, :] &= previous[:-1, :]
        out[:, :-1] &= previous[:, 1:]
        out[:, 1:] &= previous[:, :-1]
    return out


def main() -> int:
    args = parse_args()
    pass_root = Path(args.passes)
    product_root = Path(args.product)
    mask_root = Path(args.mask)
    background_root = Path(args.background)
    failures: list[str] = []
    report: dict = {
        "root": str(pass_root.parent),
        "frames_expected": args.frames,
        "start_frame": args.start_frame,
        "background_frame_offset": args.background_frame_offset,
        "tolerance": args.tolerance,
        "failures": failures,
        "frames": [],
        "passed": False,
    }

    if args.start_frame is None:
        failures.append("缺少 start_frame 合同；不能从已有文件推断起始帧号")
        start_frame = 1
    else:
        start_frame = args.start_frame
    if args.frames <= 0:
        failures.append("frames 必须为正整数")
    expected = set(range(start_frame, start_frame + args.frames)) if args.frames > 0 else set()

    product_files = discover(product_root.glob("*"), label="product", failures=failures)
    mask_files = discover(mask_root.glob("*"), label="mask", failures=failures)
    background_files = discover(background_root.glob("*"), label="background", failures=failures)
    mapped_background: dict[int, Path] = {}
    for source_index, path in background_files.items():
        logical_index = source_index + args.background_frame_offset
        if logical_index in mapped_background:
            failures.append(f"background 帧号映射后重复 {logical_index}: {mapped_background[logical_index].name}, {path.name}")
            continue
        mapped_background[logical_index] = path

    _compare_sets(expected, set(product_files), "product", failures)
    _compare_sets(expected, set(mask_files), "mask", failures)
    _compare_sets(expected, set(mapped_background), "background", failures)

    single_pass_files = discover(pass_root.glob("*.exr"), label="passes", failures=failures)
    per_channel_beauty = discover((pass_root / "beauty").glob("*.exr"), label="beauty", failures=failures)
    if not single_pass_files and not per_channel_beauty:
        failures.append("未找到 beauty 通道（passes/*.exr 或 passes/beauty/*.exr）")
    if single_pass_files:
        _compare_sets(expected, set(single_pass_files), "passes", failures)
    else:
        _compare_sets(expected, set(per_channel_beauty), "beauty", failures)

    for frame in sorted(expected):
        entry: dict = {"frame": frame}
        if frame not in product_files:
            failures.append(f"帧 {frame} 缺少产品层文件")
            continue
        if frame not in mask_files:
            failures.append(f"帧 {frame} 缺少遮罩文件")
            continue
        try:
            product, product_alpha = read_product(product_files[frame])
        except Exception as exc:
            failures.append(f"帧 {frame} 产品层读取失败：{exc}")
            continue
        mask = read_mask(mask_files[frame])
        beauty = read_beauty(pass_root, frame)
        entry["mask_coverage"] = round(float(mask.mean()), 4)
        if beauty is None:
            failures.append(f"帧 {frame} 缺少 beauty 通道")
        elif beauty.shape[:2] != mask.shape:
            failures.append(f"帧 {frame} beauty 与遮罩尺寸不一致：{beauty.shape[:2]} / {mask.shape}")
        elif not np.isfinite(beauty).all():
            failures.append(f"帧 {frame} beauty 包含非有限值")
        if product.shape[:2] != mask.shape:
            failures.append(f"帧 {frame} 产品层与遮罩尺寸不一致：{product.shape[:2]} / {mask.shape}")
        elif not np.isfinite(product).all():
            failures.append(f"帧 {frame} 产品层包含非有限值")
        if product_alpha is None:
            failures.append(f"帧 {frame} 产品层缺少可信 Alpha")
        elif product_alpha.shape != mask.shape:
            failures.append(f"帧 {frame} 产品 Alpha 与遮罩尺寸不一致：{product_alpha.shape} / {mask.shape}")
        elif not np.isfinite(product_alpha).all():
            failures.append(f"帧 {frame} 产品 Alpha 包含非有限值")

        if beauty is not None and product.shape[:2] == mask.shape and beauty.shape[:2] == mask.shape and mask.any():
            core = erode(mask, 2)
            if core.any():
                diff = float(np.mean(np.abs(product[core] - beauty[core])))
                entry["beauty_product_mean_abs_diff"] = round(diff, 6)
                if diff > args.tolerance:
                    failures.append(
                        f"帧 {frame} Beauty 与产品层不一致：核心区 mean_abs_diff={diff:.6f} > {args.tolerance}"
                    )
            else:
                failures.append(f"帧 {frame} 产品 Mask 腐蚀 2px 后核心区为空，无法做 Beauty/产品层一致性校验")
        report["frames"].append(entry)

    report["passed"] = not failures
    print("STRICT_SOURCES " + json.dumps(report, ensure_ascii=False))
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
