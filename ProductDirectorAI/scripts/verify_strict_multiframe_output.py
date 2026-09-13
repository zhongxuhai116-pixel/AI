#!/usr/bin/env python
"""V3 P0.1b 可复用 Strict 多帧产物验证器（云端证据版）。

只读文件，不启动 Blender/GPU。§9.11 core MAE 为合同门；逐像素 max 与边缘带
差异仅为诊断。EXR 用 OpenEXR 逐帧校验通道名/尺寸/finite/可检语义范围。
Depth 单位/空间、Normal 坐标空间、稳定产品身份、颜色空间语义必须由
--metadata-file 显式提供证据，否则保持 NOT_VERIFIED 且 release_eligible=false。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import numpy as np
from PIL import Image

try:
    import OpenEXR
except Exception as exc:  # pragma: no cover
    OpenEXR = None
    OPENEXR_IMPORT_ERROR = str(exc)
else:
    OPENEXR_IMPORT_ERROR = None

EXPECTED_EXR_CHANNELS = {
    "beauty": {"beauty.A", "beauty.B", "beauty.G", "beauty.R"},
    "alpha": {"alpha.V"},
    "depth": {"depth.V"},
    "normal": {"normal.X", "normal.Y", "normal.Z"},
}


def _parse_frame_index(path: Path) -> int | None:
    match = re.search(r"(?:^|[^0-9])(\d+)(?:$|[^0-9])", path.stem)
    return int(match.group(1)) if match else None


def _discover(path: Path, suffix: str, label: str, failures: list[str]) -> dict[int, Path]:
    if path is None or not path.exists():
        failures.append(f"{label} 目录不存在: {path}")
        return {}
    frames: dict[int, Path] = {}
    for file in sorted(path.glob(f"*{suffix}")):
        frame = _parse_frame_index(file)
        if frame is None:
            failures.append(f"{label} 帧名不可识别: {file.name}")
            continue
        if frame in frames:
            failures.append(f"{label} 重复帧 {frame}: {frames[frame].name}, {file.name}")
            continue
        frames[frame] = file
    return frames


def _check_frame_set(label: str, files: dict[int, Path], expected: set[int], failures: list[str]) -> None:
    actual = set(files)
    if actual != expected:
        failures.append(f"{label} 帧集与合同不一致：缺少 {sorted(expected - actual)}，多出 {sorted(actual - expected)}")


def _read_png(path: Path, label: str, failures: list[str], require_alpha: bool) -> tuple[np.ndarray, str] | None:
    try:
        image = Image.open(path)
        mode = image.mode
        rgba = np.asarray(image.convert("RGBA"), dtype=np.float32) / 255.0
    except Exception as exc:
        failures.append(f"{label} PNG 读取失败 {path}: {exc}")
        return None
    if require_alpha and "A" not in mode:
        failures.append(f"{label} 不是带 Alpha 的 PNG: mode={mode}")
        return None
    return rgba, mode


def _core_mask(alpha: np.ndarray, radius: int = 2) -> np.ndarray:
    binary = alpha > 0.5
    if not binary.any():
        return np.zeros_like(binary)
    height, width = binary.shape
    padded = np.pad(binary, radius, mode="constant", constant_values=False)
    out = binary.copy()
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            out &= padded[radius + dy: radius + dy + height, radius + dx: radius + dx + width]
    return out


def _open_exr_channels(path: Path) -> dict:
    if OpenEXR is None:
        raise RuntimeError(f"OpenEXR 不可用: {OPENEXR_IMPORT_ERROR}")
    handle = OpenEXR.File(str(path))
    header = handle.header()
    data_window = header.get("dataWindow")
    if not (isinstance(data_window, tuple) and len(data_window) == 2):
        raise RuntimeError("EXR 未找到可解析 dataWindow")
    minxy = np.asarray(data_window[0])
    maxxy = np.asarray(data_window[1])
    width = int(maxxy[0] - minxy[0] + 1)
    height = int(maxxy[1] - minxy[1] + 1)
    header_channels = header.get("channels", [])
    names = [channel.name for channel in header_channels]
    groups = handle.channels()
    pixels: dict[str, np.ndarray] = {}
    pixel_types: dict[str, str] = {}
    for key, channel in groups.items():
        pixels[key] = np.asarray(channel.pixels, dtype=np.float32)
        try:
            pixel_types[key] = channel.type().name
        except Exception:
            pixel_types[key] = "UNKNOWN"
    return {"width": width, "height": height, "channel_names": names, "pixel_types": pixel_types, "pixels": pixels}


def _check_exr(path: Path, category: str, expected_size: tuple[int, int], expected_channels: set[str], failures: list[str], frame: int) -> dict | None:
    try:
        info = _open_exr_channels(path)
    except Exception as exc:
        failures.append(f"帧 {frame} {category} EXR 读取失败: {exc}")
        return None
    if (info["width"], info["height"]) != expected_size:
        failures.append(f"帧 {frame} {category} 尺寸 {(info['width'], info['height'])} != {expected_size}")
    if set(info["channel_names"]) != expected_channels:
        failures.append(f"帧 {frame} {category} 通道 {sorted(info['channel_names'])} != {sorted(expected_channels)}")
    finite = all(np.isfinite(array).all() for array in info["pixels"].values())
    if not finite:
        failures.append(f"帧 {frame} {category} 包含非有限值")
    semantic: dict = {}
    if category == "beauty" and "beauty" in info["pixels"]:
        beauty = info["pixels"]["beauty"]
        if beauty.ndim == 3 and beauty.shape[2] >= 4:
            alpha = beauty[..., -1]
            rgb = beauty[..., :3]
            semantic.update(alpha_min=float(alpha.min()), alpha_max=float(alpha.max()), rgb_min=float(rgb.min()), rgb_max=float(rgb.max()))
            if alpha.min() < -1e-4 or alpha.max() > 1.0 + 1e-4:
                failures.append(f"帧 {frame} beauty Alpha 越界 [{alpha.min()},{alpha.max()}]")
            if rgb.min() < -1e-4:
                failures.append(f"帧 {frame} beauty RGB 出现负值 {rgb.min()}")
    elif category == "alpha" and "alpha.V" in info["pixels"]:
        alpha = info["pixels"]["alpha.V"]
        alpha = alpha[..., 0] if alpha.ndim == 3 else alpha
        semantic.update(alpha_min=float(alpha.min()), alpha_max=float(alpha.max()))
        if alpha.min() < -1e-4 or alpha.max() > 1.0 + 1e-4:
            failures.append(f"帧 {frame} alpha 越界 [{alpha.min()},{alpha.max()}]")
    elif category == "depth" and "depth.V" in info["pixels"]:
        depth = info["pixels"]["depth.V"]
        depth = depth[..., 0] if depth.ndim == 3 else depth
        semantic.update(depth_min=float(depth.min()), depth_max=float(depth.max()), no_hit_sentinel_pixels=int((depth > 1e9).sum()))
    elif category == "normal":
        for axis in ("normal.X", "normal.Y", "normal.Z"):
            if axis not in info["pixels"]:
                continue
            arr = info["pixels"][axis]
            arr = arr[..., 0] if arr.ndim == 3 else arr
            semantic[axis] = [float(arr.min()), float(arr.max())]
            if np.abs(arr).max() > 1.0 + 1e-4:
                failures.append(f"帧 {frame} {axis} 越界 [{arr.min()},{arr.max()}]")
    info["finite"] = finite
    info["semantic"] = semantic
    info["category"] = category
    return info


def _is_placeholder(value: str) -> bool:
    text = str(value or "").strip().lower()
    return any(token in text for token in ("not_yet", "placeholder", "todo", "unknown", "pending", "fake", "dummy")) or len(text) < 4


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdefABCDEF" for c in value)


def _load_metadata(path: Path | None) -> tuple[dict, list[str]]:
    if path is None or not path.exists():
        return {}, ["metadata 文件缺失"]
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, [f"metadata 文件读取失败: {exc}"]
    return payload, []


def _verify_metadata(payload: dict, root: Path, input_file: Path | None, metadata_file: Path | None = None, calibration_root: Path | None = None) -> tuple[bool, list[str]]:  # returns (evidence_bound, errors)
    errors: list[str] = []
    allowed_units = {"meters", "metres", "m", "mm", "cm"}
    allowed_spaces = {"camera_distance", "world_distance"}
    allowed_normal_spaces = {"camera", "world", "tangent"}
    allowed_normal_encodings = {"signed_float", "signed_half", "normalized"}
    allowed_display_transforms = {"AgX", "Filmic", "Standard"}
    allowed_display_spaces = {"sRGB", "Rec709"}

    depth = payload.get("depth")
    if not isinstance(depth, dict):
        errors.append("metadata.depth 缺失")
    else:
        if depth.get("unit") not in allowed_units:
            errors.append("metadata.depth.unit 不是受支持的严格值")
        if depth.get("space") not in allowed_spaces:
            errors.append("metadata.depth.space 不是受支持的严格值")
        sentinel = depth.get("no_hit_sentinel")
        if not isinstance(sentinel, (int, float)) or not np.isfinite(sentinel) or sentinel <= 0:
            errors.append("metadata.depth.no_hit_sentinel 不是严格有限正值")
        if _is_placeholder(depth.get("source")):
            errors.append("metadata.depth.source 是占位值或不可校验")

    normal = payload.get("normal")
    if not isinstance(normal, dict):
        errors.append("metadata.normal 缺失")
    else:
        if normal.get("space") not in allowed_normal_spaces:
            errors.append("metadata.normal.space 不是受支持的严格值")
        if normal.get("encoding") not in allowed_normal_encodings:
            errors.append("metadata.normal.encoding 不是受支持的严格值")

    product_id = payload.get("product_id")
    if not isinstance(product_id, dict):
        errors.append("metadata.product_id 缺失")
    else:
        if _is_placeholder(product_id.get("stable_id")):
            errors.append("metadata.product_id.stable_id 是占位值")
        if _is_placeholder(product_id.get("mapping_source")):
            errors.append("metadata.product_id.mapping_source 是占位值或不可校验")

    color = payload.get("color")
    if not isinstance(color, dict):
        errors.append("metadata.color 缺失")
    else:
        if _is_placeholder(color.get("scene_linear")):
            errors.append("metadata.color.scene_linear 是占位值")
        if color.get("display_transform") not in allowed_display_transforms:
            errors.append("metadata.color.display_transform 不是受支持的严格值")
        if color.get("product_display") not in allowed_display_spaces:
            errors.append("metadata.color.product_display 不是受支持的严格值")

    evidence = payload.get("calibration_evidence")
    if not isinstance(evidence, dict):
        errors.append("metadata.calibration_evidence 缺失")
        return False, errors
    bound_hash = evidence.get("bound_render_ledger_sha256")
    if not _is_sha256(bound_hash):
        errors.append("metadata.calibration_evidence.bound_render_ledger_sha256 不是合法 sha256")
    else:
        ledger = _file_ledger(root)
        if metadata_file is not None:
            excluded = Path(metadata_file).resolve()
            ledger = {k: v for k, v in ledger.items() if (Path(root) / k).resolve() != excluded}
        current = hashlib.sha256(json.dumps(ledger, sort_keys=True).encode("utf-8")).hexdigest()
        if current.lower() != bound_hash.lower():
            errors.append("metadata.calibration_evidence.bound_render_ledger_sha256 与本次产物 ledger 不一致")

    bound_input = evidence.get("bound_input_sha256")
    if not _is_sha256(bound_input):
        errors.append("metadata.calibration_evidence.bound_input_sha256 不是合法 sha256")
    elif input_file is None:
        errors.append("未提供 --input-file，无法校验 bound_input_sha256 与本次输入资产")
    elif _sha256_file(Path(input_file)).lower() != bound_input.lower():
        errors.append("metadata.calibration_evidence.bound_input_sha256 与本次输入资产不一致")

    source_files = evidence.get("source_files")
    source_hashes = evidence.get("source_sha256")
    if not isinstance(source_files, dict) or not isinstance(source_hashes, dict):
        errors.append("metadata.calibration_evidence.source_files/source_sha256 缺失")
    else:
        if calibration_root is None:
            errors.append("未提供 --calibration-root，无法校验校准证据文件")
        for key in ("depth", "normal", "product_mapping", "color_ocio"):
            rel = source_files.get(key)
            expected_hash = source_hashes.get(key)
            if not isinstance(rel, str) or _is_placeholder(rel):
                errors.append(f"metadata.calibration_evidence.source_files.{key} 缺失或占位")
                continue
            if not _is_sha256(expected_hash):
                errors.append(f"metadata.calibration_evidence.source_sha256.{key} 不是合法 sha256")
                continue
            if calibration_root is not None:
                source_path = Path(calibration_root) / rel
                if not source_path.exists():
                    errors.append(f"校准证据文件不存在: {source_path}")
                elif _sha256_file(source_path).lower() != expected_hash.lower():
                    errors.append(f"校准证据文件 hash 不匹配: {rel}")

    return not errors, errors


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _file_ledger(root: Path) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): _sha256_file(path) for path in sorted(root.rglob("*")) if path.is_file()}


def run_verification(
    root: Path,
    expected_frames: int,
    width: int,
    height: int,
    beauty_dir: Path,
    alpha_dir: Path,
    depth_dir: Path,
    normal_dir: Path,
    product_dir: Path,
    mask_dir: Path,
    display_dir: Path,
    pass_mask_dir: Path | None = None,
    core_mae_max: float = 1 / 255,
    alpha_max_abs_diff: float = 1 / 255,
    metadata_file: Path | None = None,
    require_semantics: bool = False,
    input_file: Path | None = None,
    calibration_root: Path | None = None,
) -> dict:
    failures: list[str] = []
    if OpenEXR is None:
        failures.append(f"OpenEXR 不可用: {OPENEXR_IMPORT_ERROR}")

    beauty_files = _discover(beauty_dir, ".exr", "beauty", failures)
    alpha_files = _discover(alpha_dir, ".exr", "alpha", failures)
    depth_files = _discover(depth_dir, ".exr", "depth", failures)
    normal_files = _discover(normal_dir, ".exr", "normal", failures)
    product_files = _discover(product_dir, ".png", "product", failures)
    mask_files = _discover(mask_dir, ".png", "mask", failures)
    display_files = _discover(display_dir, ".png", "display", failures)
    pass_mask_files = _discover(pass_mask_dir, ".png", "pass_mask", failures) if pass_mask_dir is not None else {}

    expected = set(range(1, expected_frames + 1))
    for label, files in (
        ("beauty", beauty_files), ("alpha", alpha_files), ("depth", depth_files),
        ("normal", normal_files), ("product", product_files), ("mask", mask_files),
        ("display", display_files),
    ):
        _check_frame_set(label, files, expected, failures)
    if pass_mask_dir is not None:
        _check_frame_set("pass_mask", pass_mask_files, expected, failures)

    exr_entries: list[dict] = []
    exr_failures: list[str] = []
    if OpenEXR is not None:
        for frame in sorted(expected):
            if frame not in beauty_files or frame not in alpha_files or frame not in depth_files or frame not in normal_files:
                continue
            for category, files, channels in (
                ("beauty", beauty_files, EXPECTED_EXR_CHANNELS["beauty"]),
                ("alpha", alpha_files, EXPECTED_EXR_CHANNELS["alpha"]),
                ("depth", depth_files, EXPECTED_EXR_CHANNELS["depth"]),
                ("normal", normal_files, EXPECTED_EXR_CHANNELS["normal"]),
            ):
                entry = _check_exr(files[frame], category, (width, height), channels, exr_failures, frame)
                if entry is not None:
                    exr_entries.append({
                        "frame": frame,
                        "category": category,
                        "width": entry["width"],
                        "height": entry["height"],
                        "channel_names": entry["channel_names"],
                        "pixel_types": entry["pixel_types"],
                        "finite": entry["finite"],
                        "semantic": entry["semantic"],
                    })
    failures.extend(exr_failures)

    core_maes: list[float] = []
    alpha_diffs: list[float] = []
    display_max_abs_diff = 0.0
    edge_max_abs_diff = 0.0
    edge_changed_pixels = 0
    checked = 0

    if not failures:
        for frame in sorted(expected):
            product_rgba = _read_png(product_files[frame], "product", failures, require_alpha=True)
            mask_rgba = _read_png(mask_files[frame], "mask", failures, require_alpha=True)
            display_rgba = _read_png(display_files[frame], "display", failures, require_alpha=False)
            if product_rgba is None or mask_rgba is None or display_rgba is None:
                continue
            product_rgb = product_rgba[0][..., :3]
            product_alpha = product_rgba[0][..., 3]
            mask_alpha = mask_rgba[0][..., 3]
            mask_rgb = mask_rgba[0][..., :3]
            display_rgb = display_rgba[0][..., :3]
            if product_rgb.shape[:2] != (height, width):
                failures.append(f"帧 {frame} product 尺寸 {product_rgb.shape[:2]} != {(height, width)}")
                continue
            if not np.array_equal(product_alpha, mask_alpha):
                failures.append(f"帧 {frame} product/mask Alpha 不一致")
            if not np.array_equal(product_rgb, mask_rgb):
                failures.append(f"帧 {frame} product/mask RGB 不一致")

            if OpenEXR is not None and frame in alpha_files:
                try:
                    alpha_info = _open_exr_channels(alpha_files[frame])
                    alpha_exr = alpha_info["pixels"].get("alpha.V")
                    if alpha_exr is None:
                        failures.append(f"帧 {frame} 未识别 alpha EXR 通道")
                    else:
                        alpha_exr = alpha_exr[..., 0] if alpha_exr.ndim == 3 else alpha_exr
                        if alpha_exr.shape != (height, width):
                            failures.append(f"帧 {frame} alpha EXR 尺寸 {alpha_exr.shape} != {(height, width)}")
                        else:
                            alpha_diffs.append(float(np.max(np.abs(product_alpha - alpha_exr))))
                except Exception as exc:
                    failures.append(f"帧 {frame} alpha EXR 读取失败: {exc}")

            core = _core_mask(product_alpha)
            if core.any():
                core_maes.append(float(np.mean(np.abs(display_rgb[core] - product_rgb[core]))))
            visible = product_alpha > 0.5
            if visible.any():
                abs_rgb = np.abs(display_rgb[visible] - product_rgb[visible])
                display_max_abs_diff = max(display_max_abs_diff, float(abs_rgb.max()))
            edge = visible & ~core
            if edge.any():
                edge_diff = np.abs(display_rgb[edge] - product_rgb[edge])
                edge_max_abs_diff = max(edge_max_abs_diff, float(edge_diff.max()))
                edge_changed_pixels += int((edge_diff.max(axis=-1) > 0).sum())
            checked += 1

    core_mae_max_value = max(core_maes) if core_maes else 0.0
    alpha_max = max(alpha_diffs) if alpha_diffs else 0.0
    if core_mae_max_value > core_mae_max:
        failures.append(f"display/product core MAE max {core_mae_max_value:.10f} > {core_mae_max:.10f}")
    if alpha_max > alpha_max_abs_diff:
        failures.append(f"product alpha 与 alpha EXR 最大绝对差 {alpha_max:.10f} > {alpha_max_abs_diff:.10f}")

    metadata, metadata_load_errors = _load_metadata(metadata_file)
    evidence_bound = False
    semantic_verified = False
    semantic_errors: list[str] = list(metadata_load_errors)
    if metadata_file is not None and not metadata_load_errors:
        evidence_bound, semantic_errors = _verify_metadata(metadata, root, input_file, metadata_file, calibration_root)
    if evidence_bound and not semantic_errors:
        semantic_status = "EVIDENCE_BOUND"
    else:
        semantic_status = "NOT_VERIFIED"
        if not semantic_errors:
            semantic_errors = ["无可校验的校准证据"]
    if require_semantics:
        if evidence_bound:
            failures.append("语义合同无法 VERIFIED：当前未运行可信内容校准器/审批，EVIDENCE_BOUND 不升格为 VERIFIED")
        else:
            failures.extend(f"语义合同未通过校验: {item}" for item in semantic_errors)

    status = "FAIL" if failures else "PASS"
    return {
        "probe": "strict_multiframe_output",
        "status": status,
        "release_eligible": False,
        "expected_frames": expected_frames,
        "width": width,
        "height": height,
        "frames_checked": checked,
        "core_mae": {
            "max": round(core_mae_max_value, 10),
            "limit": round(core_mae_max, 10),
            "pass": core_mae_max_value <= core_mae_max,
            "metric": "mean_absolute_error over product alpha core eroded 2px",
        },
        "alpha": {
            "max_abs_diff": round(alpha_max, 10),
            "limit": round(alpha_max_abs_diff, 10),
            "pass": alpha_max <= alpha_max_abs_diff,
        },
        "edge_diagnostic": {
            "max_abs_diff": round(edge_max_abs_diff, 6),
            "changed_pixels": edge_changed_pixels,
            "is_contract_gate": False,
        },
        "max_diagnostic": {
            "display_product_rgb_max_abs_diff": round(display_max_abs_diff, 6),
            "is_contract_gate": False,
        },
        "exr": {
            "files_checked": len(exr_entries),
            "status": "PASS" if OpenEXR is not None and not exr_failures else ("NOT_VERIFIED" if OpenEXR is None else "FAIL"),
            "entries": exr_entries,
        },
        "semantic_contract_status": semantic_status,
        "semantic_verified": semantic_verified,
        "evidence_bound": evidence_bound,
        "semantic_verification_errors": semantic_errors,
        "failures": failures,
        "limitations": [
            "只读已有文件；未启动 Blender/GPU，未证明文件由当前 Job/Run 生成",
            "Depth 单位/空间、Normal 坐标空间、稳定产品身份、颜色空间语义仅声明状态；hash 绑定通过只标记 EVIDENCE_BOUND，未运行可信内容校准器/审批前绝不 VERIFIED",
            "release_eligible 恒为 False；本探针不是 V3 完整 P0.1 或发布门",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path)
    parser.add_argument("--beauty-dir", type=Path)
    parser.add_argument("--alpha-dir", type=Path)
    parser.add_argument("--depth-dir", type=Path)
    parser.add_argument("--normal-dir", type=Path)
    parser.add_argument("--product-dir", type=Path)
    parser.add_argument("--mask-dir", type=Path)
    parser.add_argument("--display-dir", type=Path)
    parser.add_argument("--pass-mask-dir", type=Path)
    parser.add_argument("--expected-frames", type=int, required=True)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--core-mae-max", type=float, default=1 / 255)
    parser.add_argument("--alpha-max-abs-diff", type=float, default=1 / 255)
    parser.add_argument("--metadata-file", type=Path)
    parser.add_argument("--input-file", type=Path)
    parser.add_argument("--calibration-root", type=Path)
    parser.add_argument("--require-semantics", action="store_true")
    parser.add_argument("--ledger-out", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    if args.root is not None:
        root = Path(args.root)
        beauty_dir = args.beauty_dir or root / "passes" / "beauty"
        alpha_dir = args.alpha_dir or root / "passes" / "alpha"
        depth_dir = args.depth_dir or root / "passes" / "depth"
        normal_dir = args.normal_dir or root / "passes" / "normal"
        product_dir = args.product_dir or root / "product"
        mask_dir = args.mask_dir or root / "mask"
        display_dir = args.display_dir or root
        pass_mask_dir = args.pass_mask_dir or root / "passes" / "mask"
    else:
        missing = [name for name in ("beauty-dir", "alpha-dir", "depth-dir", "normal-dir", "product-dir", "mask-dir", "display-dir") if getattr(args, name.replace("-", "_")) is None]
        if missing:
            parser.error("缺少参数: " + ", ".join(missing))
        root = Path(".")
        beauty_dir, alpha_dir = args.beauty_dir, args.alpha_dir
        depth_dir, normal_dir = args.depth_dir, args.normal_dir
        product_dir, mask_dir, display_dir = args.product_dir, args.mask_dir, args.display_dir
        pass_mask_dir = args.pass_mask_dir

    result = run_verification(
        root=root,
        expected_frames=args.expected_frames,
        width=args.width,
        height=args.height,
        beauty_dir=beauty_dir,
        alpha_dir=alpha_dir,
        depth_dir=depth_dir,
        normal_dir=normal_dir,
        product_dir=product_dir,
        mask_dir=mask_dir,
        display_dir=display_dir,
        pass_mask_dir=pass_mask_dir,
        core_mae_max=args.core_mae_max,
        alpha_max_abs_diff=args.alpha_max_abs_diff,
        metadata_file=args.metadata_file,
        require_semantics=args.require_semantics,
        input_file=args.input_file,
        calibration_root=args.calibration_root,
    )
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
    else:
        print(output)
    if args.ledger_out and args.root is not None:
        ledger = _file_ledger(root)
        payload = {"root": str(root), "files": ledger, "sha256": hashlib.sha256(json.dumps(ledger, sort_keys=True).encode()).hexdigest()}
        Path(args.ledger_out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
