#!/usr/bin/env python3
"""V3-05：Strict 合成。

公式（规格原文）：`C = product_alpha × trusted_product + (1 − product_alpha) × generated_background`

实现要点：
- 颜色合同 A：默认输入为 display-referred 8-bit 数据。Blender AgX 产品 PNG 与
  H3 背景 sRGB PNG 都先按 sRGB EOTF 解码到统一的 display-linear 光值工作空间，
  合成后再按 sRGB OETF 编码输出。Manifest/报告明确记录 Blender AgX 已预应用，
  display-linear 不是 scene-linear。Mask/Depth 不参与颜色管理。
- 只有明确声明 `scene-linear` 且文件为 EXR 时才走 scene-linear 路径；禁止把
  display PNG 标成 scene-linear，也禁止 scene-linear 产品与 display-sRGB 背景混用。
- 掩码外扩：`--dilate N` 像素，保护产品边缘不被背景覆盖。
- 像素锁定：未外扩的原始掩码内，输出必须与可信产品逐像素完全一致。
- 可信 Alpha 契约：产品可见性只依据可信 Alpha，RGB 为全零不表示产品不存在；
  空产品帧必须显式声明才可作为合法合同。
- 冻结计划合同：完整 PASS 必须显式提供 `--plan` 或同时提供
  `--expected-frames/--start-frame`，不能从已有文件“猜出”缺尾帧。
  背景编号通过 `--background-frame-offset` 显式映射。
- 预检隔离：所有输入先整体预检；非预览失败不写任何最终 PNG。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image


def srgb_to_linear(value: np.ndarray) -> np.ndarray:
    return np.where(value <= 0.04045, value / 12.92, ((value + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(value: np.ndarray) -> np.ndarray:
    return np.where(value <= 0.0031308, value * 12.92, 1.055 * np.power(np.clip(value, 0, None), 1 / 2.4) - 0.055)


DISPLAY_SRGB = "display-srgb"
SCENE_LINEAR = "scene-linear"
VALID_COLOR_SPACES = (DISPLAY_SRGB, SCENE_LINEAR)
DEFAULT_COLOR_CONTRACT = {
    "product_color_space": DISPLAY_SRGB,
    "background_color_space": DISPLAY_SRGB,
    "layer_color_space": DISPLAY_SRGB,
    "working_space": "display-linear",
    "output_color_space": DISPLAY_SRGB,
    "blender_view_transform": "AgX",
    "input_decode": "sRGB EOTF (IEC 61966-2-1)",
    "output_encode": "sRGB OETF (IEC 61966-2-1)",
    "blender_view_transform_preapplied": True,
}


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
    return part, channels, width, height


def _exr_planes(path: Path) -> dict[str, np.ndarray]:
    """读取多层 EXR 的所有通道为 plane 字典。"""
    _, channels, width, height = _open_exr(path)
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


def _pick_plane(planes: dict[str, np.ndarray], *needles: str) -> tuple[str, np.ndarray] | None:
    for needle in needles:
        lowered = needle.lower()
        for name, plane in planes.items():
            if lowered in name.lower():
                return name, plane
    return None


def read_scene_linear_rgb(path: Path) -> np.ndarray:
    """读 EXR 的 scene-linear RGB；不接受 PNG，避免把 display-referred 当 scene-linear。"""
    if path.suffix.lower() != ".exr":
        raise ValueError("scene-linear 输入只接受 EXR 文件")
    planes = _exr_planes(path)
    red = green = blue = None
    for suffix in ("r", "g", "b"):
        match = _pick_plane(planes, f".{suffix}", f".{suffix.upper()}")
        if match is None:
            continue
        if suffix == "r":
            red = _first_component(match[1])
        elif suffix == "g":
            green = _first_component(match[1])
        else:
            blue = _first_component(match[1])
    if red is not None and green is not None and blue is not None:
        return np.clip(np.stack([red, green, blue], axis=-1), 0.0, None)
    beauty = _pick_plane(planes, "beauty", "rgb", "rgba")
    if beauty is None and planes:
        beauty = next(iter(planes.items()))
    if beauty is None:
        raise ValueError("EXR 不包含可读取的 RGB 产品层")
    array = beauty[1]
    if array.ndim == 3 and array.shape[2] >= 3:
        return np.clip(array[:, :, :3], 0.0, None)
    return np.clip(np.repeat(array[:, :, None], 3, axis=2), 0.0, None)


def read_display_linear_rgb(path: Path) -> np.ndarray:
    """读 display-referred PNG，按 sRGB EOTF 解码到 display-linear 工作空间。"""
    if path.suffix.lower() != ".png":
        raise ValueError("display-srgb 输入只接受 PNG 文件")
    with Image.open(path) as image:
        array = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    return srgb_to_linear(array)


def read_linear_rgb(path: Path) -> np.ndarray:
    """兼容旧调用：EXR 返回 scene-linear，PNG 返回 display-linear；不再泛称 linear light。"""
    if path.suffix.lower() == ".exr":
        return read_scene_linear_rgb(path)
    return read_display_linear_rgb(path)


def read_product_rgb(path: Path, color_space: str) -> np.ndarray:
    if color_space == DISPLAY_SRGB:
        return read_display_linear_rgb(path)
    if color_space == SCENE_LINEAR:
        return read_scene_linear_rgb(path)
    raise ValueError(f"不支持的色彩空间：{color_space}")


def read_background_rgb(path: Path, color_space: str) -> np.ndarray:
    if color_space != DISPLAY_SRGB:
        raise ValueError("Strict 背景当前只接受 display-srgb 合同；未提供可信 scene-linear 背景映射")
    return read_display_linear_rgb(path)


def read_mask(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return (np.asarray(image.convert("RGBA").split()[3], dtype=np.float32) / 255.0) > 0.5


def _pick_alpha_plane(planes: dict[str, np.ndarray]) -> tuple[str, np.ndarray] | None:
    """精确识别标量 Alpha 通道名，避免把 beauty 等名称按单字母 A 子串误配。"""
    for name, plane in planes.items():
        tokens = [token.lower() for token in name.split(".")]
        if any(token in {"alpha", "a"} for token in tokens):
            return name, plane
    return None


def read_product_alpha(path: Path) -> np.ndarray | None:
    """读取可信产品 Alpha；若输入没有可信 Alpha 则返回 None，不猜测可见性。"""
    if path.suffix.lower() == ".exr":
        planes = _exr_planes(path)
        alpha = _pick_alpha_plane(planes)
        if alpha is not None:
            return _first_component(alpha[1])
        beauty = _pick_plane(planes, "beauty", "rgba", "rgb")
        if beauty is not None and beauty[1].ndim == 3 and beauty[1].shape[2] >= 4:
            return beauty[1][:, :, 3]
        if planes:
            first = next(iter(planes.values()))
            if first.ndim == 3 and first.shape[2] >= 4:
                return first[:, :, 3]
        return None
    with Image.open(path) as image:
        if "A" in image.getbands():
            return np.asarray(image.getchannel("A"), dtype=np.float32) / 255.0
        return None


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_frame_index(path: Path) -> int | None:
    match = re.search(r"(?:^|[^0-9])(\d+)(?:$|[^0-9])", path.stem)
    if not match:
        return None
    return int(match.group(1))


def discover_indexed_frames(paths: Iterable[Path], *, label: str, failures: list[str]) -> dict[int, Path]:
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


def _mapped_background_frames(background_files: dict[int, Path], offset: int, failures: list[str]) -> dict[int, Path]:
    mapped: dict[int, Path] = {}
    for source_index, path in background_files.items():
        logical_index = source_index + offset
        if logical_index in mapped:
            failures.append(f"background 帧号映射后重复 {logical_index}: {mapped[logical_index].name}, {path.name}")
            continue
        mapped[logical_index] = path
    return mapped


def _resolve_plan(args: argparse.Namespace, product_frames: set[int], mask_frames: set[int],
                  background_frames: set[int], failures: list[str]) -> tuple[set[int], bool, str, int]:
    """返回 (expected_frames, plan_trusted, plan_source, background_offset)。"""
    plan_data: dict = {}
    plan_source = "inferred"
    if args.plan:
        try:
            plan_data = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        except Exception as exc:
            failures.append(f"冻结计划读取失败：{exc}")
            plan_data = {}
        if not isinstance(plan_data, dict):
            failures.append("冻结计划必须是 JSON 对象")
            plan_data = {}
        plan_source = "frozen"

    expected_count = plan_data.get("frame_count", args.expected_frames)
    if expected_count is None:
        expected_count = 0
    start_frame = plan_data.get("start_frame", args.start_frame)
    background_offset = plan_data.get("background_frame_offset", args.background_frame_offset)
    if background_offset is None:
        background_offset = 0

    if args.plan and not isinstance(expected_count, int):
        failures.append("冻结计划 frame_count 必须是整数")
        expected_count = 0
    if args.plan and not isinstance(start_frame, int):
        failures.append("冻结计划 start_frame 必须是整数")
        start_frame = None
    if args.plan and not isinstance(background_offset, int):
        failures.append("冻结计划 background_frame_offset 必须是整数")
        background_offset = 0

    all_frames = product_frames | mask_frames | background_frames
    plan_trusted = bool(expected_count > 0 and isinstance(start_frame, int))

    if expected_count > 0:
        if not isinstance(start_frame, int):
            failures.append("冻结计划缺少 start_frame；完整 PASS 不能从已有文件推断起始帧号")
            expected = set()
        else:
            expected = set(range(start_frame, start_frame + expected_count))
    elif all_frames:
        expected = set(range(min(all_frames), max(all_frames) + 1))
    else:
        expected = set()
    return expected, plan_trusted, plan_source, background_offset, plan_data


def _resolve_color_contract(args: argparse.Namespace, plan_data: dict, failures: list[str]) -> dict:
    """解析并验证颜色合同；冻结计划优先，CLI 参数其次，最后用 display-srgb 默认值。"""
    plan_contract = plan_data.get("color_contract", {}) if isinstance(plan_data, dict) else {}
    if not isinstance(plan_contract, dict):
        failures.append("冻结计划 color_contract 必须是 JSON 对象")
        plan_contract = {}
    plan_product = plan_contract.get("product_color_space")
    plan_background = plan_contract.get("background_color_space")
    plan_layer = plan_contract.get("layer_color_space")
    cli_product = args.product_color_space
    cli_background = args.background_color_space
    cli_layer = args.layer_color_space
    for label, plan_value, cli_value in (
        ("product_color_space", plan_product, cli_product),
        ("background_color_space", plan_background, cli_background),
        ("layer_color_space", plan_layer, cli_layer),
    ):
        if plan_value is not None and cli_value is not None and plan_value != cli_value:
            failures.append(
                f"color_contract.{label} CLI 参数 {cli_value} 与冻结计划 {plan_value} 冲突"
            )
    contract = {
        "product_color_space": plan_product if plan_product is not None else cli_product,
        "background_color_space": plan_background if plan_background is not None else cli_background,
        "layer_color_space": plan_layer if plan_layer is not None else cli_layer,
        "working_space": plan_contract.get("working_space", "display-linear"),
        "output_color_space": plan_contract.get("output_color_space", DISPLAY_SRGB),
        "blender_view_transform": plan_contract.get("blender_view_transform", "AgX"),
    }
    for key in ("product_color_space", "background_color_space"):
        if contract[key] is None:
            contract[key] = DISPLAY_SRGB
    if contract["layer_color_space"] is None:
        contract["layer_color_space"] = contract["product_color_space"]
    for key in ("product_color_space", "background_color_space", "layer_color_space"):
        value = contract[key]
        if value not in VALID_COLOR_SPACES:
            failures.append(f"color_contract.{key} 不受支持：{value}")
            contract[key] = DEFAULT_COLOR_CONTRACT[key]
    if contract["background_color_space"] != DISPLAY_SRGB:
        failures.append("Strict 背景当前只接受 display-srgb；scene-linear 背景需要可信映射证据")
    if contract["product_color_space"] != contract["background_color_space"]:
        failures.append("产品与背景色彩空间不一致，禁止跨色彩空间合成")
    if contract["layer_color_space"] != contract["product_color_space"]:
        failures.append("可选图层必须与产品层使用同一色彩空间，禁止隐式跨空间混合")
    if contract["working_space"] != "display-linear":
        failures.append(f"color_contract.working_space 必须为 display-linear，当前为 {contract['working_space']}")
    if contract["output_color_space"] != DISPLAY_SRGB:
        failures.append(f"color_contract.output_color_space 必须为 display-srgb，当前为 {contract['output_color_space']}")
    contract.update({
        "input_decode": DEFAULT_COLOR_CONTRACT["input_decode"],
        "output_encode": DEFAULT_COLOR_CONTRACT["output_encode"],
        "blender_view_transform_preapplied": bool(contract["product_color_space"] == DISPLAY_SRGB),
    })
    return contract


def _safe_remove_staging(staging: Path, out_dir: Path) -> None:
    resolved_staging = staging.resolve()
    expected_parent = out_dir.resolve().parent
    if resolved_staging.parent != expected_parent:
        return
    if not resolved_staging.name.startswith(f".{out_dir.name}.strict_composite_staging_"):
        return
    shutil.rmtree(resolved_staging, ignore_errors=True)


def _clear_final_outputs(out_dir: Path) -> None:
    if not out_dir.exists():
        return
    resolved = out_dir.resolve()
    if resolved == Path(resolved.anchor):
        return
    for pattern in ("composite_*.png", "composite_report.json"):
        for path in out_dir.glob(pattern):
            if path.is_file():
                path.unlink(missing_ok=True)


def _promote_staged(staging: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    _clear_final_outputs(out_dir)
    for src in sorted(staging.glob("composite_*.png")):
        dst = out_dir / src.name
        dst.unlink(missing_ok=True)
        shutil.move(str(src), str(dst))
    _safe_remove_staging(staging, out_dir)


def _write_report_file(out_dir: Path, report: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "composite_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_frame(frame: int, product_files: dict[int, Path], mask_files: dict[int, Path],
                background_files: dict[int, Path], color_contract: dict) -> tuple[np.ndarray, np.ndarray | None, np.ndarray, np.ndarray]:
    product = read_product_rgb(product_files[frame], color_contract["product_color_space"])
    product_alpha = read_product_alpha(product_files[frame])
    mask = read_mask(mask_files[frame])
    background = read_background_rgb(background_files[frame], color_contract["background_color_space"])
    return product, product_alpha, mask, background


def _read_optional_layers(frame: int, layer_files: dict[str, dict[int, Path]],
                          color_contract: dict) -> dict[str, tuple[np.ndarray, np.ndarray | None]]:
    layers: dict[str, tuple[np.ndarray, np.ndarray | None]] = {}
    for name, files in layer_files.items():
        path = files.get(frame)
        if path is None:
            continue
        rgb = read_product_rgb(path, color_contract["layer_color_space"])
        alpha = read_product_alpha(path)
        layers[name] = (rgb, alpha)
    return layers


def _validate_optional_layers(frame: int, layers: dict[str, tuple[np.ndarray, np.ndarray | None]],
                              shape: tuple[int, int], failures: list[str]) -> None:
    for name, (rgb, alpha) in layers.items():
        if rgb.shape[:2] != shape:
            failures.append(f"帧 {frame} {name} 层尺寸不一致：{rgb.shape[:2]} / {shape}")
        if not np.isfinite(rgb).all():
            failures.append(f"帧 {frame} {name} 层包含非有限 RGB")
        if alpha is None:
            failures.append(f"帧 {frame} {name} 层缺少可信 Alpha")
        else:
            if alpha.shape != shape:
                failures.append(f"帧 {frame} {name} Alpha 尺寸不一致：{alpha.shape} / {shape}")
            if not np.isfinite(alpha).all():
                failures.append(f"帧 {frame} {name} Alpha 包含非有限值")
            if np.any(alpha < -1e-4) or np.any(alpha > 1.0001):
                failures.append(f"帧 {frame} {name} Alpha 超出 [0,1]")


def _validate_frame(frame: int, product: np.ndarray, product_alpha: np.ndarray | None,
                    mask: np.ndarray, background: np.ndarray, args: argparse.Namespace,
                    failures: list[str], report: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    if product.shape[:2] != mask.shape or background.shape[:2] != mask.shape:
        failures.append(f"帧 {frame} 尺寸不一致")
        return None
    if not np.isfinite(product).all():
        failures.append(f"帧 {frame} 产品层包含非有限 RGB")
        return None
    if not np.isfinite(background).all():
        failures.append(f"帧 {frame} 背景层包含非有限 RGB")
        return None
    if product_alpha is None:
        failures.append(f"帧 {frame} 产品层缺少可信 Alpha，无法判断可见性")
        return None
    if product_alpha.shape != mask.shape:
        failures.append(f"帧 {frame} 可信 Alpha 与遮罩尺寸不一致：{product_alpha.shape} / {mask.shape}")
        return None
    if not np.isfinite(product_alpha).all():
        failures.append(f"帧 {frame} 可信 Alpha 包含非有限值")
        return None
    if np.any(product_alpha < -1e-4) or np.any(product_alpha > 1.0001):
        failures.append(f"帧 {frame} 可信 Alpha 超出 [0,1]")
        return None

    alpha_visible = product_alpha > 0.5
    mask_bool = mask > 0.5
    alpha_has_visible = bool(np.any(alpha_visible))
    mask_has_visible = bool(np.any(mask_bool))

    if alpha_has_visible and not mask_has_visible:
        failures.append(f"帧 {frame} 可信 Alpha 有可见产品但遮罩为空")
        return None
    if not alpha_has_visible and mask_has_visible:
        failures.append(f"帧 {frame} 遮罩有产品区域但可信 Alpha 不可见")
        return None
    if not alpha_has_visible and not mask_has_visible:
        if not args.allow_empty_product_frames:
            failures.append(f"帧 {frame} 产品不可见但未声明允许空产品帧")
            return None
    else:
        intersection = np.logical_and(alpha_visible, mask_bool).sum()
        union = np.logical_or(alpha_visible, mask_bool).sum()
        iou = float(intersection / union) if union else 0.0
        if iou < 0.995:
            failures.append(f"帧 {frame} 可信 Alpha 与遮罩轮廓 IoU 过低：{iou:.4f}")
            return None

    dilated_mask = dilate(mask_bool.astype(np.float32), args.dilate)
    effective_alpha = np.clip(np.maximum(product_alpha, dilated_mask), 0.0, 1.0)
    composite = effective_alpha[:, :, None] * product + (1.0 - effective_alpha[:, :, None]) * background
    if mask_has_visible:
        locked = np.allclose(composite[mask_bool], product[mask_bool], atol=1e-6, rtol=0.0, equal_nan=False)
        if not locked:
            report["pixel_lock_ok"] = False
            failures.append(f"帧 {frame} 像素锁定失败")
            return None
    return alpha_visible, mask_bool, dilated_mask


def _write_composite(frame: int, product: np.ndarray, product_alpha: np.ndarray,
                     mask: np.ndarray, background: np.ndarray, args: argparse.Namespace,
                     color_contract: dict,
                     staging: Path,
                     layers: dict[str, tuple[np.ndarray, np.ndarray | None]] | None = None) -> tuple[float, float, float, str, float | None]:
    alpha_visible = product_alpha > 0.5
    mask_bool = mask > 0.5
    dilated_mask = dilate(mask_bool.astype(np.float32), args.dilate)
    effective_alpha = np.clip(np.maximum(product_alpha, dilated_mask), 0.0, 1.0)
    composite = effective_alpha[:, :, None] * product + (1.0 - effective_alpha[:, :, None]) * background
    for name, (layer_rgb, layer_alpha) in (layers or {}).items():
        if layer_alpha is None:
            continue
        alpha = np.clip(layer_alpha, 0.0, 1.0)[:, :, None]
        composited = layer_rgb * alpha + composite * (1.0 - alpha)
        if name in {"shadow", "reflection"}:
            composited[mask_bool] = composite[mask_bool]
        composite = composited
    encoded = np.clip(linear_to_srgb(composite), 0.0, 1.0)
    target = staging / f"composite_{frame:04d}.png"
    Image.fromarray((encoded * 255 + 0.5).astype(np.uint8)).save(target)
    approved_display_diff = None
    if mask_bool.any() and color_contract["product_color_space"] == DISPLAY_SRGB:
        with Image.open(target) as written_image:
            written = np.asarray(written_image.convert("RGB"), dtype=np.float32) / 255.0
        product_display = np.clip(linear_to_srgb(product), 0.0, 1.0)
        product_display_8bit = np.round(product_display * 255.0) / 255.0
        approved_display_diff = float(np.max(np.abs(written[mask_bool] - product_display_8bit[mask_bool])))
    return (float(alpha_visible.mean()), float(mask_bool.mean()), float(dilated_mask.mean()),
            sha256_bytes(target.read_bytes()), approved_display_diff)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product", required=True, help="可信产品层目录（beauty，EXR 或 PNG）")
    parser.add_argument("--mask", required=True, help="产品遮罩目录（PNG）")
    parser.add_argument("--background", required=True, help="生成的背景帧目录（PNG）")
    parser.add_argument("--shadow", default="", help="独立阴影层目录；空字符串表示未启用")
    parser.add_argument("--reflection", default="", help="独立反射层目录；空字符串表示未启用")
    parser.add_argument("--occlusion", default="", help="独立遮挡层目录；空字符串表示未启用")
    parser.add_argument("--out", required=True)
    parser.add_argument("--dilate", type=int, default=2)
    parser.add_argument("--expected-frames", type=int, default=0, help="冻结计划总帧数；0 表示推断，推断时不会完整 PASS")
    parser.add_argument("--start-frame", type=int, default=None, help="冻结计划起始逻辑帧号")
    parser.add_argument("--background-frame-offset", type=int, default=None,
                        help="背景文件编号到逻辑帧号的偏移，例如 bg_0000 对应逻辑 1 时传 +1")
    parser.add_argument("--plan", default="", help="冻结计划 JSON：frame_count/start_frame/background_frame_offset")
    parser.add_argument("--allow-empty-product-frames", action="store_true",
                        help="显式声明允许可信 Alpha 和 Mask 均为空的合法不可见帧")
    parser.add_argument("--product-color-space", choices=VALID_COLOR_SPACES, default=None,
                        help="产品层色彩合同；默认 display-srgb（Blender AgX display PNG），与冻结计划冲突时拒绝")
    parser.add_argument("--background-color-space", choices=VALID_COLOR_SPACES, default=None,
                        help="背景层色彩合同；当前严格合成只接受 display-srgb，与冻结计划冲突时拒绝")
    parser.add_argument("--layer-color-space", choices=VALID_COLOR_SPACES, default=None,
                        help="可选 shadow/reflection/occlusion 层色彩合同；默认与产品层一致")
    parser.add_argument("--limit", type=int, default=0, help="仅处理前 N 帧（0 表示全部）；结果只能是 PREVIEW_ONLY")
    args = parser.parse_args()

    product_dir, mask_dir, bg_dir, out_dir = (Path(p) for p in (args.product, args.mask, args.background, args.out))
    failures: list[str] = []

    product_files = discover_indexed_frames(
        [p for p in product_dir.glob("*") if p.suffix.lower() in {".exr", ".png"}],
        label="product",
        failures=failures,
    )
    mask_files = discover_indexed_frames(mask_dir.glob("*.png"), label="mask", failures=failures)
    background_files = discover_indexed_frames(bg_dir.glob("*.png"), label="background", failures=failures)
    optional_layer_files: dict[str, dict[int, Path]] = {}
    for layer_name in ("shadow", "reflection", "occlusion"):
        layer_arg = getattr(args, layer_name, "")
        if not layer_arg:
            continue
        layer_dir = Path(layer_arg)
        layer_paths = [p for p in layer_dir.glob("*") if p.suffix.lower() in {".png", ".exr"}]
        optional_layer_files[layer_name] = discover_indexed_frames(
            layer_paths, label=layer_name, failures=failures
        )

    product_frames = set(product_files)
    mask_frames = set(mask_files)
    raw_background_frames = set(background_files)
    expected, plan_trusted, plan_source, background_offset, plan_data = _resolve_plan(
        args, product_frames, mask_frames, raw_background_frames, failures
    )
    color_contract = _resolve_color_contract(args, plan_data, failures)
    required_layer_frames: dict[str, set[int]] = {}
    for layer_name in ("shadow", "reflection", "occlusion"):
        layer_frames = plan_data.get("required_layers", {}).get(layer_name, [])
        if layer_frames:
            required_layer_frames[layer_name] = set(layer_frames)
    mapped_background_files = _mapped_background_frames(background_files, background_offset, failures)
    background_frames = set(mapped_background_files)

    report: dict = {
        "equation": "C = effective_alpha * trusted_product + (1 - effective_alpha) * generated_background",
        "color_space": "composited in display-linear working space from display-sRGB inputs, output encoded to sRGB",
        "color_contract": color_contract,
        "approved_display_product_mae_max": round(1 / 255, 6),
        "approved_display_product_match_ok": False,
        "approved_display_product_match_status": "NOT_VERIFIED",
        "approved_display_product_compared_frames": 0,
        "approved_display_product_visible_frames": 0,
        "dilate_pixels": args.dilate,
        "plan_source": plan_source,
        "plan_trusted": plan_trusted,
        "background_frame_offset": background_offset,
        "expected_frames": sorted(expected),
        "input_frames": {
            "product": sorted(product_frames),
            "mask": sorted(mask_frames),
            "background": sorted(background_frames),
            **{name: sorted(files) for name, files in optional_layer_files.items()},
        },
        "pixel_lock_ok": True,
        "frames_report": [],
        "failures": failures,
        "preview_only": False,
        "passed": False,
    }

    if not plan_trusted and not args.limit:
        failures.append("缺少冻结计划合同（--plan 或同时提供 --expected-frames/--start-frame）；不能从已有文件推断完整帧范围")

    for name, frame_set in {
        "product": product_frames,
        "mask": mask_frames,
        "background": background_frames,
    }.items():
        missing = sorted(expected - frame_set)
        extra = sorted(frame_set - expected)
        if missing:
            failures.append(f"{name} 缺少帧：{missing}")
        if extra:
            failures.append(f"{name} 有多余帧：{extra}")
    for name, files in optional_layer_files.items():
        layer_expected = required_layer_frames.get(name)
        present = set(files)
        out_of_range = sorted(present - expected)
        if out_of_range:
            failures.append(f"{name} 含越出冻结全片帧集合的帧：{out_of_range}")
        if layer_expected:
            missing = sorted(layer_expected - present)
        else:
            missing = []
        if missing:
            failures.append(f"{name} 缺少帧：{missing}")
    if len(expected) == 0:
        failures.append("输入帧不足（product/mask/background 至少各需 1 帧）")

    common_frames = product_frames & mask_frames & background_frames
    frame_ids = sorted(common_frames)
    preview_only = bool(args.limit and args.limit < len(frame_ids))
    if args.limit:
        frame_ids = frame_ids[: args.limit]
    report["all_frames"] = sorted(common_frames)
    report["frames"] = len(frame_ids)
    report["preview_only"] = preview_only

    if not frame_ids:
        print(json.dumps(report, ensure_ascii=False))
        return 1

    # 第一遍：只读、只校验，不写任何 PNG，也不缓存整帧像素。
    for frame in frame_ids:
        try:
            product, product_alpha, mask, background = _read_frame(frame, product_files, mask_files, mapped_background_files, color_contract)
        except Exception as exc:
            failures.append(f"帧 {frame} 读取失败：{exc}")
            continue
        layers = _read_optional_layers(frame, optional_layer_files, color_contract)
        _validate_optional_layers(frame, layers, mask.shape, failures)
        _validate_frame(frame, product, product_alpha, mask, background, args, failures, report)

    if failures or (not plan_trusted and not preview_only):
        report["output_written"] = False
        print(json.dumps(report, ensure_ascii=False))
        return 1

    # 第二遍：预检通过后重新逐帧读取并写暂存 PNG；全片通过再提升为最终输出。
    staging = out_dir.resolve().parent / f".{out_dir.name}.strict_composite_staging_{os.getpid()}_{uuid.uuid4().hex}"
    staging.mkdir(parents=True, exist_ok=False)
    for frame in frame_ids:
        try:
            product, product_alpha, mask, background = _read_frame(frame, product_files, mask_files, mapped_background_files, color_contract)
        except Exception as exc:
            failures.append(f"帧 {frame} 读取失败：{exc}")
            continue
        layers = _read_optional_layers(frame, optional_layer_files, color_contract)
        _validate_optional_layers(frame, layers, mask.shape, failures)
        if product_alpha is None:
            failures.append(f"帧 {frame} 产品层缺少可信 Alpha，无法判断可见性")
            continue
        alpha_coverage, mask_coverage, dilated_coverage, output_sha256, approved_display_diff = _write_composite(
            frame, product, product_alpha, mask, background, args, color_contract, staging, layers
        )
        if mask_coverage > 0:
            report["approved_display_product_visible_frames"] += 1
        if approved_display_diff is not None:
            report["approved_display_product_compared_frames"] += 1
        if approved_display_diff is not None and approved_display_diff > 1 / 255:
            report["approved_display_product_match_ok"] = False
            failures.append(
                f"帧 {frame} 批准 display 产品与合成输出最大差 {approved_display_diff:.6f} 超过 1/255"
            )
        if frame in {frame_ids[0], frame_ids[-1]}:
            report["frames_report"].append({
                "frame": frame,
                "mask_coverage": round(mask_coverage, 4),
                "alpha_coverage": round(alpha_coverage, 4),
                "dilated_coverage": round(dilated_coverage, 4),
                "output_sha256": output_sha256,
                "approved_display_product_max_abs_diff": (
                    round(approved_display_diff, 6) if approved_display_diff is not None else None
                ),
            })
    if report["approved_display_product_visible_frames"] == 0:
        report["approved_display_product_match_status"] = "NOT_APPLICABLE"
        report["approved_display_product_match_ok"] = True
    elif report["approved_display_product_compared_frames"] < report["approved_display_product_visible_frames"]:
        report["approved_display_product_match_status"] = "NOT_VERIFIED"
        report["approved_display_product_match_ok"] = False
        failures.append("批准 display 产品对照未覆盖全部可见帧，禁止完整 PASS")
    elif not failures:
        report["approved_display_product_match_status"] = "PASS"
        report["approved_display_product_match_ok"] = True
    else:
        report["approved_display_product_match_status"] = "FAIL"
        report["approved_display_product_match_ok"] = False
    if failures:
        _safe_remove_staging(staging, out_dir)
        report["output_written"] = False
        print(json.dumps(report, ensure_ascii=False))
        return 1

    report["passed"] = bool(plan_trusted and not preview_only and report["pixel_lock_ok"] and not failures)

    if report["passed"] or (preview_only and not failures):
        _promote_staged(staging, out_dir)
        report["output_written"] = True
        report["output_dir"] = str(out_dir)
        _write_report_file(out_dir, report)
    else:
        _safe_remove_staging(staging, out_dir)
        report["output_written"] = False

    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
