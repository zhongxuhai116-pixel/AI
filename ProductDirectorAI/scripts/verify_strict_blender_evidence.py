#!/usr/bin/env python
"""V3-02/03 Blender 5.2 Strict 产物最小 CPU 探针。

本脚本不启动 Blender、不启动 GPU；只对已有文件做结构、有限值、帧集与
Beauty/Alpha/product/mask 对应关系检查。它不能替代真实 Blender 进程证据，
也不会把旧历史产物自动升格为当前受控 workflow 来源。

预期目录：
  --beauty-dir  第一遍 Beauty 多层 EXR（frame_0001.exr ...）
  --alpha-dir   第一遍 Alpha 多层 EXR（可为独立 alpha 通道）
  --product-dir 第二遍 product RGBA PNG
  --mask-dir    第二遍 continuous mask RGBA PNG

运行示例（云端只读，需先确认输入路径与文件命名）：
  python scripts/verify_strict_blender_evidence.py \
    --beauty-dir /home/ubuntu/pd-v305-layers-evidence/Blender/passes/beauty \
    --alpha-dir /home/ubuntu/pd-v305-layers-evidence/Blender/passes/alpha \
    --product-dir /home/ubuntu/pd-v305-layers-evidence/Blender/strict/product \
    --mask-dir /home/ubuntu/pd-v305-layers-evidence/Blender/strict/mask \
    --expected-frames 72 --width 540 --height 960 --out /tmp/blender_probe.json
"""
from __future__ import annotations

import argparse
import io
import json
import re
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image

try:
    import OpenEXR
except Exception as exc:  # pragma: no cover - 现场依赖缺失应明确失败
    OpenEXR = None
    OPENEXR_IMPORT_ERROR = str(exc)
else:
    OPENEXR_IMPORT_ERROR = None


def _parse_frame_index(path: Path) -> int | None:
    match = re.search(r"(?:^|[^0-9])(\d+)(?:$|[^0-9])", path.stem)
    return int(match.group(1)) if match else None


def _discover(path: Path, suffix: str, label: str, failures: list[str]) -> dict[int, Path]:
    if not path.exists():
        failures.append(f"{label} 目录不存在: {path}")
        return {}
    files = sorted(path.glob(f"*{suffix}"))
    frames: dict[int, Path] = {}
    for file in files:
        frame = _parse_frame_index(file)
        if frame is None:
            failures.append(f"{label} 帧名不可识别: {file.name}")
            continue
        if frame in frames:
            failures.append(f"{label} 重复帧 {frame}: {frames[frame].name}, {file.name}")
            continue
        frames[frame] = file
    return frames


def _open_exr(path: Path):
    if OpenEXR is None:
        raise RuntimeError(f"OpenEXR 不可用: {OPENEXR_IMPORT_ERROR}")
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
    names = list(channels.keys()) if hasattr(channels, "keys") else [c.name for c in channels]
    planes: dict[str, np.ndarray] = {}
    for name in names:
        entry = channels[name]
        data = getattr(entry, "pixels", None)
        if data is None:
            continue
        array = np.asarray(data, dtype=np.float32)
        components = max(1, array.size // (width * height))
        planes[name] = array.reshape(height, width, components)
    return planes, width, height


def _tokens(name: str) -> set[str]:
    return {token.lower() for token in name.split(".")}


def _compose_rgb(planes: dict[str, np.ndarray]) -> np.ndarray | None:
    for name, plane in planes.items():
        if any(token in {"beauty", "rgb", "rgba"} for token in _tokens(name)):
            if plane.ndim == 3 and plane.shape[2] >= 3:
                return plane[:, :, :3]
    grouped: dict[str, np.ndarray] = {}
    for name, plane in planes.items():
        tokens = _tokens(name)
        if "beauty" in tokens or "rgb" in tokens or "rgba" in tokens:
            for index, channel in enumerate(("r", "g", "b")):
                if channel in tokens:
                    grouped[channel] = plane[:, :, 0]
    if set(grouped) >= {"r", "g", "b"}:
        return np.stack([grouped["r"], grouped["g"], grouped["b"]], axis=2)
    return None


def _compose_alpha(planes: dict[str, np.ndarray]) -> np.ndarray | None:
    for name, plane in planes.items():
        tokens = _tokens(name)
        if "alpha" in tokens or "a" in tokens:
            return plane[:, :, 0] if plane.ndim == 3 else plane
    for name, plane in planes.items():
        if any(token in {"beauty", "rgba", "rgb"} for token in _tokens(name)):
            if plane.ndim == 3 and plane.shape[2] >= 4:
                return plane[:, :, 3]
    for name, plane in planes.items():
        tokens = _tokens(name)
        if "beauty" in tokens and "a" in tokens:
            return plane[:, :, 0] if plane.ndim == 3 else plane
    return None


def _read_png_alpha(path: Path) -> tuple[np.ndarray, np.ndarray | None]:
    with Image.open(path) as image:
        bands = image.getbands()
        if "A" not in bands:
            raise ValueError(f"PNG 缺少 Alpha（原模式 {image.mode}, bands={bands}）: {path}")
        rgb = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
        alpha = np.asarray(image.getchannel("A"), dtype=np.float32) / 255.0
    return rgb, alpha


def _srgba_to_linear(rgb: np.ndarray) -> np.ndarray:
    linear = rgb.copy()
    mask = linear <= 0.04045
    linear[mask] /= 12.92
    linear[~mask] = ((linear[~mask] + 0.055) / 1.055) ** 2.4
    return linear


def _finite_or_fail(array: np.ndarray, label: str, frame: int, failures: list[str]) -> None:
    if not np.isfinite(array).all():
        failures.append(f"帧 {frame} {label} 包含非有限值")


def _core_mask(alpha: np.ndarray, radius: int = 2) -> np.ndarray:
    if not alpha.any():
        return np.zeros_like(alpha, dtype=bool)
    protected = alpha > 1e-4
    padded = np.pad(protected, radius, mode="constant", constant_values=False)
    out = protected.copy()
    height, width = protected.shape
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dy == 0 and dx == 0:
                continue
            out &= padded[radius + dy: radius + dy + height, radius + dx: radius + dx + width]
    return out


def run_probe(
    beauty_dir: Path,
    alpha_dir: Path,
    product_dir: Path,
    mask_dir: Path,
    expected_frames: int,
    width: int,
    height: int,
    alpha_max_abs_diff: float = 1 / 255,
    display_dir: Path | None = None,
    display_mae_max: float = 1 / 255,
) -> dict:
    failures: list[str] = []
    beauty_files = _discover(beauty_dir, ".exr", "beauty", failures)
    alpha_files = _discover(alpha_dir, ".exr", "alpha", failures)
    product_files = _discover(product_dir, ".png", "product", failures)
    mask_files = _discover(mask_dir, ".png", "mask", failures)
    display_files = _discover(display_dir, ".png", "display", failures) if display_dir is not None else {}
    if display_dir is None:
        failures.append("未提供可比的 display PNG 目录，Beauty linear EXR 与 product display PNG 不能直接跨色彩空间比较")

    expected = set(range(1, expected_frames + 1))
    for label, files in (("beauty", beauty_files), ("alpha", alpha_files), ("product", product_files), ("mask", mask_files)):
        actual = set(files)
        if actual != expected:
            failures.append(
                f"{label} 帧集与合同不一致：缺少 {sorted(expected - actual)}，多出 {sorted(actual - expected)}"
            )
    if display_dir is not None and set(display_files) != expected:
        failures.append(
            f"display 帧集与合同不一致：缺少 {sorted(expected - set(display_files))}，多出 {sorted(set(display_files) - expected)}"
        )

    alpha_diffs: list[float] = []
    display_diffs: list[float] = []
    checked = 0
    if not failures:
        for frame in sorted(expected):
            try:
                beauty_planes, exr_w, exr_h = _open_exr(beauty_files[frame])
                alpha_planes, alpha_w, alpha_h = _open_exr(alpha_files[frame])
            except Exception as exc:
                failures.append(f"帧 {frame} EXR 读取失败: {exc}")
                continue
            if (exr_w, exr_h) != (width, height):
                failures.append(f"帧 {frame} beauty 尺寸 {(exr_w, exr_h)} 与合同 {(width, height)} 不一致")
            if (alpha_w, alpha_h) != (width, height):
                failures.append(f"帧 {frame} alpha 尺寸 {(alpha_w, alpha_h)} 与合同 {(width, height)} 不一致")
            beauty = _compose_rgb(beauty_planes)
            alpha_exr = _compose_alpha(beauty_planes) if _compose_alpha(beauty_planes) is not None else _compose_alpha(alpha_planes)
            if beauty is None:
                failures.append(f"帧 {frame} 未识别可信 Beauty RGB 通道")
                continue
            if alpha_exr is None:
                failures.append(f"帧 {frame} 未识别可信 Beauty/Alpha 通道")
                continue
            _finite_or_fail(beauty, "beauty", frame, failures)
            _finite_or_fail(alpha_exr, "alpha", frame, failures)
            try:
                product_rgb, product_alpha = _read_png_alpha(product_files[frame])
                _, mask_alpha = _read_png_alpha(mask_files[frame])
                display_rgb = _read_png_alpha(display_files[frame])[0] if display_dir is not None else None
            except Exception as exc:
                failures.append(f"帧 {frame} PNG 读取失败: {exc}")
                continue
            if product_rgb.shape[:2] != (height, width) or product_alpha.shape != (height, width):
                failures.append(f"帧 {frame} product 尺寸/Alpha 与合同不一致")
                continue
            if mask_alpha is None or mask_alpha.shape != (height, width):
                failures.append(f"帧 {frame} mask 尺寸/Alpha 与合同不一致")
                continue
            _finite_or_fail(product_alpha, "product alpha", frame, failures)
            _finite_or_fail(mask_alpha, "mask alpha", frame, failures)
            if display_rgb is not None:
                _finite_or_fail(display_rgb, "display RGB", frame, failures)
            if not np.allclose(product_alpha, mask_alpha, atol=1 / 255):
                failures.append(f"帧 {frame} product/mask Alpha 不是同帧一致")
            core = _core_mask(alpha_exr)
            if core.any():
                alpha_diffs.append(float(np.max(np.abs(product_alpha[core] - alpha_exr[core]))))
            if display_rgb is not None:
                display_diffs.append(float(np.max(np.abs(display_rgb - product_rgb))))
            checked += 1

    if alpha_diffs and max(alpha_diffs) > alpha_max_abs_diff:
        failures.append(
            f"Beauty/Alpha 与 product/mask Alpha 最大绝对差 {max(alpha_diffs):.6f} 超过阈值 {alpha_max_abs_diff:.6f}"
        )
    if display_diffs and max(display_diffs) > display_mae_max:
        failures.append(
            f"display PNG 与 product PNG RGB 最大绝对差 {max(display_diffs):.6f} 超过阈值 {display_mae_max:.6f}"
        )

    if not alpha_diffs:
        alpha_status = "NOT_VERIFIED"
    elif max(alpha_diffs) <= alpha_max_abs_diff:
        alpha_status = "PASS"
    else:
        alpha_status = "FAIL"
    if display_dir is None or not display_diffs:
        display_status = "NOT_VERIFIED"
    elif max(display_diffs) <= display_mae_max:
        display_status = "PASS"
    else:
        display_status = "FAIL"
    beauty_rgb_status = "NOT_VERIFIED"
    status = "FAIL" if failures else "PASS"
    return {
        "probe": "strict_blender_evidence",
        "status": status,
        "expected_frames": expected_frames,
        "width": width,
        "height": height,
        "frames_checked": checked,
        "beauty_product_alpha_max_abs_diff": round(max(alpha_diffs), 6) if alpha_diffs else None,
        "display_product_rgb_max_abs_diff": round(max(display_diffs), 6) if display_diffs else None,
        "display_mae_max": round(display_mae_max, 6),
        "alpha_max_abs_diff": round(alpha_max_abs_diff, 6),
        "alpha_consistency_status": alpha_status,
        "display_product_rgb_status": display_status,
        "beauty_product_rgb_status": beauty_rgb_status,
        "beauty_product_rgb_comparison": "NOT_COMPARABLE",
        "beauty_product_rgb_not_comparable_reason": "Blender HDR linear Beauty 与 8-bit display-referred product PNG 位于不同色彩空间；需用 OCIO/AgX display transform 或提供第一遍 display PNG 做可比比较",
        "failures": failures,
        "limitations": [
            "只读已有文件；未启动 Blender/GPU，未证明文件由当前脚本或当前 Run 生成",
            "RGB 一致性只比较 display PNG 与 product PNG 的 display-referred 像素；Beauty linear EXR 不直接与 product PNG 做跨色彩空间比较",
            "旧历史产物不能据此自动成为当前受控 workflow 的可发布来源",
            "compositor 是否真正隔离仍需 render_product.py 运行时 hash/mtime 探针或现场进程日志",
        ],
    }


def _write_self_test_evidence(root: Path) -> tuple[Path, Path, Path, Path]:
    beauty_dir = root / "passes" / "beauty"
    alpha_dir = root / "passes" / "alpha"
    product_dir = root / "strict" / "product"
    mask_dir = root / "strict" / "mask"
    for folder in (beauty_dir, alpha_dir, product_dir, mask_dir):
        folder.mkdir(parents=True, exist_ok=True)
    for frame in (1, 2):
        height, width = 8, 8
        beauty = np.zeros((height, width, 4), dtype=np.float32)
        beauty[:, :, 0] = 0.2
        beauty[:, :, 1] = 0.3
        beauty[:, :, 2] = 0.4
        beauty[:, :, 3] = 0.75
        OpenEXR.File({}, {"beauty": beauty}).write(str(beauty_dir / f"frame_{frame:04d}.exr"))
        alpha = np.full((height, width), 0.75, dtype=np.float32)
        OpenEXR.File({}, {"alpha": alpha}).write(str(alpha_dir / f"frame_{frame:04d}.exr"))
        rgba = np.zeros((height, width, 4), dtype=np.uint8)
        rgba[:, :, 0] = 124
        rgba[:, :, 1] = 149
        rgba[:, :, 2] = 170
        rgba[:, :, 3] = 191
        Image.fromarray(rgba).save(product_dir / f"frame_{frame:04d}.png")
        Image.fromarray(rgba).save(mask_dir / f"frame_{frame:04d}.png")
    return beauty_dir, alpha_dir, product_dir, mask_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--beauty-dir", type=Path)
    parser.add_argument("--alpha-dir", type=Path)
    parser.add_argument("--product-dir", type=Path)
    parser.add_argument("--mask-dir", type=Path)
    parser.add_argument("--display-dir", type=Path)
    parser.add_argument("--expected-frames", type=int, default=72)
    parser.add_argument("--width", type=int, default=540)
    parser.add_argument("--height", type=int, default=960)
    parser.add_argument("--display-mae-max", type=float, default=1 / 255)
    parser.add_argument("--alpha-max-abs-diff", type=float, default=1 / 255)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        tmp = Path(__file__).resolve().parents[1] / "var" / "_probe_tmp" / "self_test"
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True, exist_ok=True)
        dirs = _write_self_test_evidence(tmp)
        beauty_dir, alpha_dir, product_dir, mask_dir = dirs
        expected_frames, width, height = 2, 8, 8
        display_dir = product_dir
    else:
        missing = [name for name, value in (
            ("beauty-dir", args.beauty_dir),
            ("alpha-dir", args.alpha_dir),
            ("product-dir", args.product_dir),
            ("mask-dir", args.mask_dir),
        ) if value is None]
        if missing:
            parser.error("缺少参数: " + ", ".join(missing))
        beauty_dir, alpha_dir, product_dir, mask_dir = args.beauty_dir, args.alpha_dir, args.product_dir, args.mask_dir
        expected_frames, width, height = args.expected_frames, args.width, args.height
        display_dir = args.display_dir

    result = run_probe(
        beauty_dir,
        alpha_dir,
        product_dir,
        mask_dir,
        expected_frames,
        width,
        height,
        alpha_max_abs_diff=args.alpha_max_abs_diff,
        display_dir=display_dir,
        display_mae_max=args.display_mae_max,
    )
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(output, encoding="utf-8")
    print(output)
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
