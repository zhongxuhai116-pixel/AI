"""V3-04 Strict QA 引擎。

仅读取真实文件计算保真指标；不把客户端自报 JSON 当证据。
无法由本地文件证明的物理/视觉指标返回 NOT_VERIFIED，由调用方保持 fail-closed。
"""
from __future__ import annotations

import json
import hashlib
import math
import re
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image


DEFAULT_STRICT_QA_THRESHOLD_SET = {
    "schema_version": "1.0",
    "threshold_set_version": "2026.09.1",
    "threshold_set_id": "productdirector.strict-qa.v1",
    "color_core_mae_max": 1.0 / 255.0,
    "edge_mae_max": 0.02,
    "contour_iou_min": 0.995,
    "mask_alpha_iou_min": 0.995,
    "min_mask_coverage": 0.01,
    "min_mask_binary_ratio": 0.98,
    "logo_core_mae_max": 1.0 / 255.0,
    "logo_mask_min_coverage": 0.99,
    "verified_dimension_max_relative_error": 0.01,
}


def parse_frame_index(path: Path) -> int | None:
    match = re.search(r"(?:^|[^0-9])(\d+)(?:$|[^0-9])", path.stem)
    return int(match.group(1)) if match else None


def discover_frames(paths: Iterable[Path], label: str, failures: list[str]) -> dict[int, Path]:
    frames: dict[int, Path] = {}
    for path in sorted(paths):
        frame = parse_frame_index(path)
        if frame is None:
            failures.append(f"{label} 包含不可识别帧名：{path.name}")
            continue
        if frame in frames:
            failures.append(f"{label} 存在重复帧 {frame}: {frames[frame].name}, {path.name}")
            continue
        frames[frame] = path
    return frames


def srgb_to_linear(value: np.ndarray) -> np.ndarray:
    return np.where(value <= 0.04045, value / 12.92, ((value + 0.055) / 1.055) ** 2.4)


def erode(mask: np.ndarray, radius: int) -> np.ndarray:
    out = mask.copy()
    for _ in range(radius):
        previous = out.copy()
        out = previous.copy()
        out[:-1, :] &= previous[1:, :]
        out[1:, :] &= previous[:-1, :]
        out[:, :-1] &= previous[:, 1:]
        out[:, 1:] &= previous[:, :-1]
    return out


def dilate(mask: np.ndarray, radius: int) -> np.ndarray:
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
    return planes, width, height


def _first_component(array: np.ndarray) -> np.ndarray:
    return array[:, :, 0] if array.ndim == 3 else array


def _tokens(name: str) -> set[str]:
    return {token.lower() for token in name.split(".")}


def _pick_beauty(planes: dict[str, np.ndarray]) -> np.ndarray | None:
    for name, plane in planes.items():
        if any(token in {"beauty", "rgb", "rgba"} for token in _tokens(name)):
            if plane.ndim == 3 and plane.shape[2] >= 3:
                return plane[:, :, :3]
    return None


def _pick_alpha(planes: dict[str, np.ndarray]) -> np.ndarray | None:
    for name, plane in planes.items():
        if any(token in {"alpha", "a"} for token in _tokens(name)):
            return _first_component(plane)
    for name, plane in planes.items():
        if any(token in {"beauty", "rgba", "rgb"} for token in _tokens(name)):
            if plane.ndim == 3 and plane.shape[2] >= 4:
                return plane[:, :, 3]
    return None


def read_mask(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        if "A" not in image.getbands():
            raise ValueError(f"遮罩缺少可信 Alpha 通道（原模式 {image.mode}）: {path}")
        return np.asarray(image.getchannel("A"), dtype=np.float32) / 255.0


def read_product(path: Path) -> tuple[np.ndarray, np.ndarray | None]:
    if path.suffix.lower() == ".exr":
        planes, _, _ = _open_exr(path)
        rgb = _pick_beauty(planes)
        if rgb is None:
            raise ValueError(f"产品层 EXR 不含 RGB: {path}")
        return np.clip(rgb, 0.0, None), _pick_alpha(planes)
    with Image.open(path) as image:
        rgb = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
        alpha = np.asarray(image.getchannel("A"), dtype=np.float32) / 255.0 if "A" in image.getbands() else None
    return srgb_to_linear(rgb), alpha


def _read_pass_planes(pass_root: Path, frame: int) -> dict[str, np.ndarray] | None:
    single = pass_root / f"frame_{frame:04d}.exr"
    if single.exists():
        planes, _, _ = _open_exr(single)
        return planes
    beauty = pass_root / "beauty" / f"frame_{frame:04d}.exr"
    if not beauty.exists():
        return None
    planes, _, _ = _open_exr(beauty)
    alpha_path = pass_root / "alpha" / f"frame_{frame:04d}.exr"
    if alpha_path.exists():
        alpha_planes, _, _ = _open_exr(alpha_path)
        for name, plane in alpha_planes.items():
            planes.setdefault(name, plane)
    return planes


def read_reference(pass_root: Path, frame: int) -> tuple[np.ndarray | None, np.ndarray | None]:
    planes = _read_pass_planes(pass_root, frame)
    if planes is None:
        return None, None
    return _pick_beauty(planes), _pick_alpha(planes)


def _region_slice(region: dict, height: int, width: int) -> tuple[slice, slice]:
    x0 = max(0, min(width - 1, int(round(float(region.get("x", 0)) * width))))
    y0 = max(0, min(height - 1, int(round(float(region.get("y", 0)) * height))))
    x1 = max(x0 + 1, min(width, int(round((float(region.get("x", 0)) + float(region.get("width", 0))) * width))))
    y1 = max(y0 + 1, min(height, int(round((float(region.get("y", 0)) + float(region.get("height", 0))) * height))))
    return slice(y0, y1), slice(x0, x1)


def _frame_failures(check: dict, frame: int, failures: list[str]) -> None:
    for failure in failures:
        check["failures"].append(f"帧 {frame}: {failure}")


def run_strict_qa(
    strict_root: Path,
    plan_payload: dict,
    output_spec,
    snapshot: dict,
    policy_payload: dict,
    review_payload: dict,
    threshold_payload: dict,
    controlled_evidence: dict | None = None,
    media_report: dict | None = None,
    media_file: Path | None = None,
    asset_path: Path | None = None,
) -> dict:
    """读取 strict/product、strict/mask 与 passes 参考，计算真实保真指标。"""
    thresholds = dict(DEFAULT_STRICT_QA_THRESHOLD_SET)
    thresholds.update(threshold_payload or {})
    failures: list[str] = []
    not_verified: list[str] = []
    expected = set(range(1, int(output_spec.frame_count) + 1))
    product_root = strict_root / "product"
    mask_root = strict_root / "mask"
    pass_root = strict_root / "passes"

    product_files = discover_frames(product_root.glob("*"), "product", failures)
    mask_files = discover_frames(mask_root.glob("*"), "mask", failures)
    for label, files in (("product", product_files), ("mask", mask_files)):
        missing = sorted(expected - set(files))
        extra = sorted(set(files) - expected)
        if missing:
            failures.append(f"{label} 缺少帧：{missing[:12]}")
        if extra:
            failures.append(f"{label} 多出帧：{extra[:12]}")

    frame_report: list[dict] = []
    color_mae_max = 0.0
    edge_mae_max = 0.0
    contour_iou_min = 1.0
    logo_mae_max = 0.0
    logo_min_coverage = 1.0
    for frame in sorted(expected):
        entry = {"frame": frame}
        if frame not in product_files:
            frame_report.append(entry)
            continue
        if frame not in mask_files:
            frame_report.append(entry)
            continue
        try:
            product, product_alpha = read_product(product_files[frame])
        except Exception as exc:
            failures.append(f"帧 {frame} 产品层读取失败：{exc}")
            frame_report.append(entry)
            continue
        try:
            mask = read_mask(mask_files[frame])
        except Exception as exc:
            failures.append(f"帧 {frame} 遮罩读取失败：{exc}")
            frame_report.append(entry)
            continue
        reference, reference_alpha = read_reference(pass_root, frame)
        if reference is None:
            failures.append(f"帧 {frame} 缺少 Beauty 参考")
            frame_report.append(entry)
            continue
        if reference.shape != product.shape:
            failures.append(f"帧 {frame} Beauty 参考与产品层尺寸不一致：{reference.shape} / {product.shape}")
            frame_report.append(entry)
            continue
        if reference_alpha is not None and reference_alpha.shape != mask.shape:
            failures.append(f"帧 {frame} 参考 Alpha 与遮罩尺寸不一致：{reference_alpha.shape} / {mask.shape}")
            frame_report.append(entry)
            continue
        if product_alpha is None:
            failures.append(f"帧 {frame} 产品层缺少可信 Alpha")
            frame_report.append(entry)
            continue
        if product_alpha.shape != mask.shape:
            failures.append(f"帧 {frame} 产品 Alpha 与遮罩尺寸不一致：{product_alpha.shape} / {mask.shape}")
            frame_report.append(entry)
            continue
        if not np.isfinite(product).all() or not np.isfinite(product_alpha).all() or not np.isfinite(mask).all():
            failures.append(f"帧 {frame} 产品/遮罩包含非有限值")
            frame_report.append(entry)
            continue
        if reference_alpha is not None and not np.isfinite(reference_alpha).all():
            failures.append(f"帧 {frame} 参考 Alpha 包含非有限值")
            frame_report.append(entry)
            continue

        mask_binary = mask > 0.5
        coverage = float(mask_binary.mean())
        entry["mask_coverage"] = round(coverage, 4)
        binary_ratio = float(((mask > 0.95) | (mask < 0.05)).mean())
        entry["mask_binary_ratio"] = round(binary_ratio, 4)
        core = erode(mask_binary, 2)
        zero_visibility_frames = set(
            int(item) for item in (snapshot.get("zero_visibility_frames") or [])
        )
        if not core.any():
            alpha_empty = (
                product_alpha is not None
                and float(np.max(product_alpha)) <= 1e-4
                and reference_alpha is not None
                and float(np.max(reference_alpha)) <= 1e-4
            )
            if frame in zero_visibility_frames and alpha_empty:
                entry["zero_visibility"] = True
                frame_report.append(entry)
                continue
            if not alpha_empty:
                failures.append(f"帧 {frame} 声明空可见帧但 Alpha 实证不为空")
            else:
                failures.append(f"帧 {frame} 产品可见性合同缺失或 Alpha 实证不足")
            frame_report.append(entry)
            continue
        if coverage < float(thresholds["min_mask_coverage"]):
            failures.append(f"帧 {frame} Mask 覆盖率过低 {coverage:.4f}")
        if binary_ratio < float(thresholds["min_mask_binary_ratio"]):
            failures.append(f"帧 {frame} Mask 不够二值 {binary_ratio:.4f}")

        if reference_alpha is None:
            not_verified.append("参考 Alpha 不可用，无法验证轮廓/产品身份")
            iou = None
        else:
            reference_binary = reference_alpha > 0.5
            intersection = np.logical_and(reference_binary, mask_binary).sum()
            union = np.logical_or(reference_binary, mask_binary).sum()
            iou = float(intersection / union) if union else 0.0
            entry["reference_contour_iou"] = round(iou, 4)
            contour_iou_min = min(contour_iou_min, iou)
            if iou < float(thresholds["contour_iou_min"]):
                failures.append(f"帧 {frame} 产品轮廓 IoU 过低 {iou:.4f}")

        if core.any():
            color_diff = np.mean(np.abs(product[core] - reference[core]))
            color_mae_max = max(color_mae_max, float(color_diff))
            entry["core_color_mae"] = round(float(color_diff), 6)
            if color_diff > float(thresholds["color_core_mae_max"]):
                failures.append(f"帧 {frame} 线性核心区 MAE 过高 {color_diff:.6f}")

            edge = dilate(mask_binary, 2) & ~core
            if edge.any():
                edge_diff = float(np.mean(np.abs(product[edge] - reference[edge])))
                entry["edge_mae"] = round(edge_diff, 6)
                edge_mae_max = max(edge_mae_max, edge_diff)
                if edge_diff > float(thresholds["edge_mae_max"]):
                    failures.append(f"帧 {frame} 边缘带 MAE 过高 {edge_diff:.6f}")

        for region in review_payload.get("logo_regions", []):
            y_slice, x_slice = _region_slice(region, mask.shape[0], mask.shape[1])
            region_mask = mask_binary[y_slice, x_slice]
            region_coverage = float(region_mask.mean()) if region_mask.size else 0.0
            logo_min_coverage = min(logo_min_coverage, region_coverage)
            if region_coverage < float(thresholds["logo_mask_min_coverage"]):
                failures.append(
                    f"帧 {frame} Logo 区域 {region.get('label') or region} 覆盖不足 {region_coverage:.4f}"
                )
                continue
            region_core = region_mask & core[y_slice, x_slice]
            if not region_core.any():
                failures.append(f"帧 {frame} Logo 区域 {region.get('label') or region} 缺少可比较核心像素")
                continue
            region_diff = float(np.mean(np.abs(
                product[y_slice, x_slice][region_core] - reference[y_slice, x_slice][region_core]
            )))
            logo_mae_max = max(logo_mae_max, region_diff)
            if region_diff > float(thresholds["logo_core_mae_max"]):
                failures.append(f"帧 {frame} Logo 核心区 MAE 过高 {region_diff:.6f}")
        frame_report.append(entry)

    checks = {
        "frame_completeness": {"status": "PASS"},
        "color_core": {"status": "PASS", "max_mean_abs_diff": round(color_mae_max, 6)},
        "edge": {"status": "PASS", "max_mean_abs_diff": round(edge_mae_max, 6)},
        "contour": {"status": "PASS", "min_iou": round(contour_iou_min, 4)},
        "logo": {"status": "PASS", "max_mean_abs_diff": round(logo_mae_max, 6), "min_mask_coverage": round(logo_min_coverage, 4)},
        "product_id": {"status": "NOT_VERIFIED", "reason": "缺少稳定 Object Index/Cryptomatte 身份合同"},
        "dimension": {"status": "NOT_VERIFIED", "reason": "缺少已核实像素尺度/相机内参，无法从成片反推真实尺寸"},
        "encoded_media": {"status": "NOT_VERIFIED", "reason": "压缩损伤/闪烁/可读性未接入"},
        "asset_hash": {"status": "NOT_VERIFIED", "reason": "缺少受控渲染资产 hash 证据"},
    }
    if controlled_evidence:
        if controlled_evidence.get("producer_control") != "CONTROLLED_WORKER":
            checks["asset_hash"] = {
                "status": "FAIL",
                "reason": "受控渲染证据缺少 CONTROLLED_WORKER 来源",
            }
        elif asset_path is None or not asset_path.exists():
            checks["asset_hash"] = {
                "status": "FAIL",
                "reason": "受控渲染证据存在但当前资产文件不可用",
            }
        else:
            expected_hash = str(controlled_evidence.get("asset_sha256") or "").lower()
            actual_hash = hashlib.sha256(asset_path.read_bytes()).hexdigest().lower()
            checks["asset_hash"] = {
                "status": "PASS" if expected_hash == actual_hash else "FAIL",
                "asset_sha256": actual_hash,
                "reason": "" if expected_hash == actual_hash else "资产文件 hash 与受控证据不一致",
            }
    if (
        media_report
        and media_report.get("computed_by") == "productdirector.media_quality_report"
        and media_file is not None
        and media_file.exists()
        and media_report.get("source_sha256") == hashlib.sha256(media_file.read_bytes()).hexdigest()
    ):
        checks["encoded_media"] = {
            **media_report,
            "status": "PASS" if media_report.get("passed") else "FAIL",
        }
    else:
        checks["encoded_media"] = {
            "status": "NOT_VERIFIED",
            "reason": "缺少由系统可信计算且与成片文件 hash 绑定的编码质检报告",
        }

    # 将明确的致命帧级失败映射到对应检查，便于 UI 定位。
    for failure in failures:
        if "Logo" in failure:
            checks["logo"]["status"] = "FAIL"
        elif "轮廓" in failure:
            checks["contour"]["status"] = "FAIL"
        elif "核心区 MAE" in failure:
            checks["color_core"]["status"] = "FAIL"
        elif "边缘带" in failure:
            checks["edge"]["status"] = "FAIL"
        else:
            checks["frame_completeness"]["status"] = "FAIL"

    fatal = list(failures)
    for check_name, check in checks.items():
        if check.get("status") == "FAIL":
            fatal.append(f"{check_name}: {check.get('reason', 'check failed')}")
        elif check.get("status") == "NOT_VERIFIED":
            not_verified.append(f"{check_name}: {check.get('reason', 'NOT_VERIFIED')}")
    fatal = sorted(set(fatal))
    not_verified = sorted(set(not_verified))
    report_status = "FAIL" if fatal else ("NOT_VERIFIED" if not_verified else "PASS")
    return {
        "schema_version": "1.0",
        "status": report_status,
        "passed": not bool(fatal) and not bool(not_verified),
        "fatal_failures": fatal,
        "not_verified": sorted(set(not_verified)),
        "checks": checks,
        "frames": frame_report,
        "thresholds": thresholds,
        "threshold_set_id": thresholds.get("threshold_set_id"),
        "threshold_set_version": thresholds.get("threshold_set_version"),
        "policy_mode": policy_payload.get("mode"),
        "review_source_kind": review_payload.get("source_kind"),
        "frame_count": int(output_spec.frame_count),
    }
