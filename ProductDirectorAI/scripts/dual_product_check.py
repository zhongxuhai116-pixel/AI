#!/usr/bin/env python3
"""V3-06：双产品检测。

任务书规格：背景（合成输出中产品掩码以外的区域）里出现多余的产品影像时必须被检出并阻断。
退出证据：构造含双产品的样例被阻断并给出热图与问题帧。

检测算法：
- **模板来源**：从可信产品层（beauty）参考帧的产品掩码包围盒内裁出产品外观，
  生成多尺度模板（默认 0.5 / 0.75 / 1.0 三个尺度，可用 `--scales` 调整），
  并取首帧与中间帧两个参考位姿，覆盖产品外观随镜头的变化。
- **匹配**：numpy 实现归一化互相关（NCC）滑窗——窗口和/平方和用积分图，
  互相关用 FFT 卷积；采用**粗到细**两级金字塔：先在半分辨率上做低门槛粗筛，
  只有粗筛命中的邻域才在全分辨率上精算，避免整图多尺度滑窗的开销。
- **误报门槛**（三重门）：
  1. NCC 得分 ≥ `--ncc-threshold`（默认 0.75）；
  2. 候选窗与模板的 RGB 直方图交集 ≥ `--hist-threshold`（默认 0.60）；
  3. 候选窗边缘能量 ≥ 模板边缘能量 × 0.25（拒绝平坦/纯色区域的巧合匹配）。
- **检测区域**：只在外扩掩码（`--dilate`，语义与 strict_composite 一致）以外、
  且窗口内掩码覆盖率 < 20% 的位置计入检出，保证原产品位置不误报。
- **平坦区保护**：窗口标准差过低（近似纯色）时 NCC 记为无效，避免除零伪峰。

判定与输出：
- 任一帧检出超过阈值的产品样区域 → 该帧 FAIL，整体结论 BLOCKED，退出码 1；
  全部通过退出码 0。
- `--out` 目录写 `report.json`（逐帧 verdict、每个检出的 bbox+得分、阈值参数）
  与每张问题帧的热图叠图 PNG（背景上高亮检出区域，红框标注）。
- `--plan <director-plan.json>` 可选：按 shots 的 duration_frames 帧区间把帧号
  映射到 Shot（shot_id / shot_name）写入报告；不提供则只报帧号。
- `--self-test-fixtures <dir>`：程序化合成测试夹具（干净背景正例 + 粘贴产品的
  负例 + 示例 plan.json），供单测与云端复验复用，生成后退出。

本机托管 Python 无 OpenEXR：beauty 为 EXR 时在函数内延迟导入，测试只用 PNG。

用法：
    .venv/bin/python scripts/dual_product_check.py \
        --frames <composite_or_bg_dir> --product <passes/beauty> \
        --mask <passes/mask> --out <dir> --dilate 2 [--plan plan.json]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

# 多尺度模板的默认尺度；过小的模板在粗筛层不足 6 像素会被自动跳过
DEFAULT_SCALES = (0.5, 0.75, 1.0)
# 模板参考帧：首帧与中间帧两个位姿
REFERENCE_POSITIONS = (0.0, 0.5)
# 粗筛门槛 = 精筛门槛 - 该值（粗筛宽松，精筛严格）
COARSE_MARGIN = 0.15
# 候选窗内允许的外扩掩码覆盖率上限
MASK_OVERLAP_MAX = 0.20
# 边缘能量门槛：候选窗边缘能量 ≥ 模板 × 该比例
EDGE_RATIO_MIN = 0.25
# 窗口标准差下限（0-1 灰度），低于则视为平坦区不参与匹配
WINDOW_STD_MIN = 0.015
# 非极大值抑制的 IoU 上限
NMS_IOU_MAX = 0.30


# ---------------------------------------------------------------------------
# 基础读写（与 strict_composite.py 语义保持一致：线性/sRGB 换算、掩码 alpha>0.5）
# ---------------------------------------------------------------------------

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


def read_product_srgb(path: Path) -> np.ndarray:
    """读可信产品层并归一到 sRGB 0-1（EXR 线性浮点延迟导入 OpenEXR，PNG 按 sRGB 读）。"""
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
        linear = np.clip(array.reshape(height, width, components)[:, :, :3], 0.0, None)
        return np.clip(linear_to_srgb(linear), 0.0, 1.0)
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0


def read_frame_srgb(path: Path) -> np.ndarray:
    """读一帧待检画面（合成帧或背景帧，PNG，sRGB 0-1）。"""
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0


def read_mask(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return (np.asarray(image.convert("RGBA").split()[3], dtype=np.float32) / 255.0) > 0.5


def to_gray(rgb: np.ndarray) -> np.ndarray:
    return (0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]).astype(np.float32)


# ---------------------------------------------------------------------------
# NCC 滑窗（积分图 + FFT 互相关）
# ---------------------------------------------------------------------------

def _window_sums(integral: np.ndarray, h: int, w: int) -> np.ndarray:
    """由积分图计算所有 h×w 窗口的和，返回 (H-h+1, W-w+1)。"""
    height, width = integral.shape
    padded = np.zeros((height + 1, width + 1), dtype=np.float64)
    padded[1:, 1:] = integral
    return padded[h:, w:] - padded[:-h, w:] - padded[h:, :-w] + padded[:-h, :-w]


def ncc_map(image: np.ndarray, template: np.ndarray) -> np.ndarray | None:
    """归一化互相关滑窗，锚点为窗口左上角；平坦窗与无效区记 0。

    image / template 为 2D 浮点灰度；image 不得小于 template。
    """
    height, width = image.shape
    th, tw = template.shape
    if th > height or tw > width or th < 2 or tw < 2:
        return None
    count = th * tw
    t_mean = float(template.mean())
    t_centered = template - t_mean
    t_energy = float(np.sqrt((t_centered ** 2).sum()))
    if t_energy < 1e-6:
        return None  # 模板本身无纹理，无法匹配
    integral = image.astype(np.float64).cumsum(0).cumsum(1)
    integral2 = (image.astype(np.float64) ** 2).cumsum(0).cumsum(1)
    sums = _window_sums(integral, th, tw)
    sums2 = _window_sums(integral2, th, tw)
    # 互相关：image 与翻转模板的卷积，锚点对应卷积索引 (x+th-1, y+tw-1)
    shape = (height + th - 1, width + tw - 1)
    spectrum = np.fft.rfft2(image.astype(np.float64), shape) * np.fft.rfft2(template[::-1, ::-1].astype(np.float64), shape)
    corr = np.fft.irfft2(spectrum, shape)[th - 1: th - 1 + sums.shape[0], tw - 1: tw - 1 + sums.shape[1]]
    numerator = corr - sums * t_mean
    variance = np.maximum(sums2 - sums ** 2 / count, 0.0)
    denominator = np.sqrt(variance) * t_energy
    score = np.zeros(sums.shape, dtype=np.float64)
    valid = (variance > (WINDOW_STD_MIN ** 2) * count) & (denominator > 1e-12)
    score[valid] = numerator[valid] / denominator[valid]
    return np.clip(score, -1.0, 1.0)


def _resize_gray(array: np.ndarray, width: int, height: int) -> np.ndarray:
    """灰度浮点图的缩放（PIL mode=F 双线性）。"""
    image = Image.fromarray(array.astype(np.float32), mode="F")
    return np.asarray(image.resize((width, height), Image.BILINEAR), dtype=np.float32)


def _resize_rgb(array: np.ndarray, width: int, height: int) -> np.ndarray:
    image = Image.fromarray((np.clip(array, 0, 1) * 255 + 0.5).astype(np.uint8))
    return np.asarray(image.resize((width, height), Image.BILINEAR), dtype=np.float32) / 255.0


def _dilate_bool(mask: np.ndarray, radius: int) -> np.ndarray:
    return dilate(mask.astype(np.float32), radius) > 0.5


# ---------------------------------------------------------------------------
# 直方图与边缘能量门槛
# ---------------------------------------------------------------------------

def _histogram(rgb: np.ndarray, bins: int = 8) -> np.ndarray:
    """RGB 联合直方图（归一化），用于候选窗与模板的颜色比对。"""
    index = (np.clip(rgb, 0, 0.999) * bins).astype(np.int32)
    hist = np.zeros((bins, bins, bins), dtype=np.float64)
    np.add.at(hist, (index[:, :, 0].ravel(), index[:, :, 1].ravel(), index[:, :, 2].ravel()), 1.0)
    total = hist.sum()
    return hist / total if total > 0 else hist


def _histogram_intersection(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.minimum(a, b).sum())


def _edge_energy(gray: np.ndarray) -> float:
    """灰度梯度模长的均值，作为纹理/边缘强度度量。"""
    if gray.shape[0] < 3 or gray.shape[1] < 3:
        return 0.0
    gy, gx = np.gradient(gray.astype(np.float64))
    return float(np.sqrt(gx ** 2 + gy ** 2).mean())


# ---------------------------------------------------------------------------
# 模板提取与多尺度匹配
# ---------------------------------------------------------------------------

def mask_bbox(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    """掩码包围盒 (x, y, w, h)；空掩码返回 None。"""
    ys, xs = np.nonzero(mask)
    if ys.size == 0:
        return None
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    return x0, y0, x1 - x0 + 1, y1 - y0 + 1


def build_templates(product_files: list[Path], mask_files: list[Path], scales: tuple[float, ...]) -> list[dict]:
    """从参考帧（首帧与中间帧）的产品掩码包围盒裁出模板，并按尺度缩放。"""
    templates: list[dict] = []
    count = len(product_files)
    seen: set[tuple[int, int]] = set()
    for position in REFERENCE_POSITIONS:
        index = min(count - 1, int(round(position * (count - 1))))
        product = read_product_srgb(product_files[index])
        mask = read_mask(mask_files[index])
        bbox = mask_bbox(mask)
        if bbox is None:
            continue
        x, y, w, h = bbox
        crop_rgb = product[y: y + h, x: x + w]
        crop_gray = to_gray(crop_rgb)
        for scale in scales:
            tw = max(2, int(round(w * scale)))
            th = max(2, int(round(h * scale)))
            if (th, tw) in seen:
                continue
            seen.add((th, tw))
            t_rgb = _resize_rgb(crop_rgb, tw, th) if (th, tw) != (h, w) else crop_rgb
            t_gray = to_gray(t_rgb)
            if float(t_gray.std()) < 0.01:
                continue  # 模板无纹理，跳过
            templates.append({
                "reference_frame": index,
                "scale": scale,
                "width": tw,
                "height": th,
                "rgb": t_rgb,
                "gray": t_gray,
                "hist": _histogram(t_rgb),
                "edge": _edge_energy(t_gray),
            })
    return templates


def match_template(gray: np.ndarray, template: dict, valid_anchor: np.ndarray, ncc_threshold: float) -> tuple[np.ndarray, np.ndarray | None]:
    """单模板粗到细匹配。

    返回 (候选锚点分数图, 全分辨率 NCC 图或 None)。候选图已按有效锚点掩蔽。
    粗筛在半分辨率进行，只有粗筛命中邻域才在全分辨率精算。
    """
    t_gray = template["gray"]
    th, tw = t_gray.shape
    height, width = gray.shape
    if th > height or tw > width:
        return np.zeros((0, 0)), None
    fine_shape = (height - th + 1, width - tw + 1)
    coarse_threshold = ncc_threshold - COARSE_MARGIN
    candidate_region: np.ndarray | None = None
    # 粗筛：模板与画面都足够大时走半分辨率金字塔
    if min(th, tw) >= 12 and min(height, width) >= 64:
        gray_c = _resize_gray(gray, max(1, width // 2), max(1, height // 2))
        t_c = _resize_gray(t_gray, max(2, tw // 2), max(2, th // 2))
        score_c = ncc_map(gray_c, t_c)
        if score_c is not None:
            hits = score_c >= coarse_threshold
            if not hits.any():
                return np.zeros(fine_shape), None  # 粗筛无命中，跳过精算
            hits = _dilate_bool(hits, 2)
            up = np.kron(hits, np.ones((2, 2), dtype=bool))
            candidate_region = np.zeros(fine_shape, dtype=bool)
            h = min(up.shape[0], fine_shape[0])
            w = min(up.shape[1], fine_shape[1])
            candidate_region[:h, :w] = up[:h, :w]
    score = ncc_map(gray, t_gray)
    if score is None:
        return np.zeros(fine_shape), None
    masked = np.zeros(fine_shape)
    region = valid_anchor if candidate_region is None else (valid_anchor & candidate_region)
    masked[region] = score[region]
    return masked, score


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix0, iy0 = max(ax, bx), max(ay, by)
    ix1, iy1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def detect_frame(frame_rgb: np.ndarray, mask: np.ndarray, templates: list[dict], dilate_pixels: int,
                 ncc_threshold: float, hist_threshold: float) -> tuple[list[dict], np.ndarray]:
    """单帧检测：返回 (检出列表, 热度图 0-1)。

    检出项含 bbox / score / histogram / scale / reference_frame。
    热度图为所有模板 NCC 分数在背景区的外扩叠加，用于叠图可视化。
    """
    height, width = frame_rgb.shape[:2]
    gray = to_gray(frame_rgb)
    dilated = dilate(mask.astype(np.float32), dilate_pixels) > 0.5
    heat = np.zeros((height, width), dtype=np.float64)
    detections: list[dict] = []
    for template in templates:
        th, tw = template["height"], template["width"]
        if th > height or tw > width:
            continue
        # 有效锚点：窗口内掩码覆盖率低于上限（只查背景区）
        coverage = _window_sums(dilated.astype(np.float64).cumsum(0).cumsum(1), th, tw) / float(th * tw)
        valid_anchor = coverage < MASK_OVERLAP_MAX
        candidate_map, _ = match_template(gray, template, valid_anchor, ncc_threshold)
        if candidate_map.size == 0:
            continue
        # 热度叠加：粗筛以上即入图，便于观察接近阈值的区域
        ys, xs = np.nonzero(candidate_map >= ncc_threshold - COARSE_MARGIN)
        for y, x in zip(ys.tolist(), xs.tolist()):
            value = candidate_map[y, x]
            if value > 0:
                heat[y: y + th, x: x + tw] = np.maximum(heat[y: y + th, x: x + tw], value)
        # 精筛 + 三重门 + 贪心 NMS
        positions = [(float(candidate_map[y, x]), x, y) for y, x in zip(ys.tolist(), xs.tolist())
                     if candidate_map[y, x] >= ncc_threshold]
        positions.sort(reverse=True)
        kept: list[dict] = []
        for score, x, y in positions:
            window_rgb = frame_rgb[y: y + th, x: x + tw]
            hist_sim = _histogram_intersection(_histogram(window_rgb), template["hist"])
            if hist_sim < hist_threshold:
                continue
            if template["edge"] > 1e-4 and _edge_energy(to_gray(window_rgb)) < template["edge"] * EDGE_RATIO_MIN:
                continue
            bbox = (x, y, tw, th)
            if any(_iou(bbox, tuple(item["bbox"])) > NMS_IOU_MAX for item in kept):
                continue
            kept.append({
                "bbox": [x, y, tw, th],
                "score": round(score, 4),
                "histogram": round(hist_sim, 4),
                "scale": template["scale"],
                "reference_frame": template["reference_frame"],
            })
        detections.extend(kept)
    detections.sort(key=lambda item: item["score"], reverse=True)
    return detections, np.clip(heat, 0.0, 1.0)


def render_heatmap(frame_rgb: np.ndarray, heat: np.ndarray, detections: list[dict], target: Path) -> None:
    """热图叠图：背景上按 NCC 热度叠加红色高亮，检出区域画红框与得分。"""
    alpha = np.clip(heat * 1.2, 0.0, 0.85)[:, :, None]
    highlight = np.zeros_like(frame_rgb)
    highlight[:, :, 0] = 1.0
    highlight[:, :, 1] = 0.15
    highlight[:, :, 2] = 0.10
    overlay = np.clip(frame_rgb * (1.0 - alpha) + highlight * alpha, 0.0, 1.0)
    image = Image.fromarray((overlay * 255 + 0.5).astype(np.uint8))
    draw = ImageDraw.Draw(image)
    for item in detections:
        x, y, w, h = item["bbox"]
        draw.rectangle([x, y, x + w - 1, y + h - 1], outline=(255, 32, 16), width=2)
        draw.text((x + 2, max(0, y - 10)), f"{item['score']:.2f}", fill=(255, 32, 16))
    image.save(target)


# ---------------------------------------------------------------------------
# plan.json 帧号 → Shot 映射
# ---------------------------------------------------------------------------

def load_plan_shots(path: Path) -> list[dict]:
    """读 director-plan.json，返回带帧区间的 shot 列表（按 duration_frames 累计）。"""
    plan = json.loads(path.read_text(encoding="utf-8"))
    shots: list[dict] = []
    start = 0
    for shot in plan.get("shots", []):
        duration = int(shot.get("duration_frames", 0))
        shots.append({
            "shot_id": shot.get("id", f"shot_{len(shots) + 1:02d}"),
            "shot_name": shot.get("name", ""),
            "frame_start": start,
            "frame_end": start + duration - 1,
        })
        start += duration
    return shots


def frame_to_shot(shots: list[dict], frame: int) -> dict | None:
    for shot in shots:
        if shot["frame_start"] <= frame <= shot["frame_end"]:
            return shot
    return None


# ---------------------------------------------------------------------------
# 自测夹具生成（供 tests/ 与云端复验复用）
# ---------------------------------------------------------------------------

def _draw_product(width: int = 56, height: int = 40) -> np.ndarray:
    """程序化产品外观：红机身 + 白圆 Logo + 深色条纹，纹理丰富便于 NCC。"""
    array = np.zeros((height, width, 3), dtype=np.float32)
    array[:, :] = (0.78, 0.20, 0.12)                      # 机身底色
    array[:4, :] = (0.55, 0.10, 0.06)                     # 顶部深边
    array[-4:, :] = (0.95, 0.45, 0.30)                    # 底部亮边
    yy, xx = np.mgrid[0:height, 0:width]
    cx, cy, r = width // 2, height // 2, min(width, height) // 4
    circle = (xx - cx) ** 2 + (yy - cy) ** 2 <= r ** 2
    array[circle] = (0.96, 0.96, 0.94)                    # 圆形 Logo
    inner = (xx - cx) ** 2 + (yy - cy) ** 2 <= (r // 2) ** 2
    array[inner] = (0.15, 0.15, 0.18)                     # Logo 内芯
    stripe = ((xx[0] // 6) % 2 == 0)                      # 一维横向条纹
    array[:6][:, stripe] = (0.20, 0.05, 0.05)             # 顶部条纹
    return array


def _draw_clean_background(frame: int, width: int, height: int) -> np.ndarray:
    """干净背景：平滑竖向渐变 + 低幅值确定性噪声（蓝绿调，与产品色差大）。"""
    yy, xx = np.mgrid[0:height, 0:width]
    phase = frame * 0.05
    array = np.zeros((height, width, 3), dtype=np.float32)
    array[:, :, 0] = 0.10 + 0.10 * (yy / max(1, height - 1))
    array[:, :, 1] = 0.25 + 0.20 * (xx / max(1, width - 1)) + 0.05 * np.sin(xx * 0.08 + phase)
    array[:, :, 2] = 0.35 + 0.15 * (yy / max(1, height - 1)) + 0.05 * np.cos(yy * 0.06 + phase)
    rng = np.random.default_rng(20260912 + frame)  # 确定性种子，可复现
    array += rng.normal(0.0, 0.012, array.shape).astype(np.float32)
    return np.clip(array, 0.0, 1.0)


def make_self_test_fixtures(root: Path, frames: int = 8, width: int = 240, height: int = 160) -> dict:
    """生成自测夹具：

    - `product/`：可信产品层 PNG（黑底 + 产品外观，产品位置固定）；
    - `mask/`：产品掩码 `frame_XXXX.png`（alpha>0.5 语义）；
    - `bg_clean/`：干净背景正例；
    - `bg_dirty/`：负例——`DIRTY_FRAMES` 帧把产品外观粘贴到背景远处；
    - `plan.json`：两个 Shot 的示例导演计划（帧区间覆盖全部帧）。
    """
    dirty_frames = (2, 5)
    product_dir = root / "product"
    mask_dir = root / "mask"
    clean_dir = root / "bg_clean"
    dirty_dir = root / "bg_dirty"
    for directory in (product_dir, mask_dir, clean_dir, dirty_dir):
        directory.mkdir(parents=True, exist_ok=True)
    appearance = _draw_product()
    ph, pw = appearance.shape[:2]
    px, py = 30, 60  # 产品真值位置（掩码位置）
    dx, dy = 170, 20  # 负例中多余产品的粘贴位置（远离真值位置）
    for frame in range(frames):
        canvas = np.zeros((height, width, 3), dtype=np.float32)
        canvas[py: py + ph, px: px + pw] = appearance
        Image.fromarray((canvas * 255 + 0.5).astype(np.uint8)).save(product_dir / f"prod_{frame:04d}.png")
        mask = np.zeros((height, width, 4), dtype=np.uint8)
        mask[:, :, 3] = 255
        mask[py: py + ph, px: px + pw, 3] = 255
        mask[:, :, :3] = 255
        outside = np.ones((height, width), dtype=bool)
        outside[py: py + ph, px: px + pw] = False
        mask[outside, 3] = 0
        Image.fromarray(mask, mode="RGBA").save(mask_dir / f"frame_{frame:04d}.png")
        background = _draw_clean_background(frame, width, height)
        Image.fromarray((background * 255 + 0.5).astype(np.uint8)).save(clean_dir / f"bg_{frame:04d}.png")
        dirty = background.copy()
        if frame in dirty_frames:
            dirty[dy: dy + ph, dx: dx + pw] = appearance
        Image.fromarray((dirty * 255 + 0.5).astype(np.uint8)).save(dirty_dir / f"bg_{frame:04d}.png")
    plan = {
        "schema_version": "director-plan/1",
        "shots": [
            {"id": "shot_01", "name": "开场展示", "duration_frames": frames // 2},
            {"id": "shot_02", "name": "细节收尾", "duration_frames": frames - frames // 2},
        ],
    }
    (root / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "root": str(root),
        "frames": frames,
        "size": [width, height],
        "product_at": [px, py, pw, ph],
        "intruder_at": [dx, dy, pw, ph],
        "dirty_frames": list(dirty_frames),
        "plan": str(root / "plan.json"),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="V3-06 双产品检测：背景区多余产品影像必须被检出并阻断")
    parser.add_argument("--frames", help="待检帧目录（合成帧或背景帧，PNG）")
    parser.add_argument("--product", help="可信产品层目录（beauty，PNG 或 EXR 延迟导入）")
    parser.add_argument("--mask", help="产品掩码目录（frame_*.png，取 alpha>0.5）")
    parser.add_argument("--out", help="输出目录（report.json 与问题帧热图）")
    parser.add_argument("--dilate", type=int, default=2, help="掩码外扩像素数（与 strict_composite 语义一致）")
    parser.add_argument("--ncc-threshold", type=float, default=0.75, help="NCC 判定阈值")
    parser.add_argument("--hist-threshold", type=float, default=0.60, help="颜色直方图交集阈值")
    parser.add_argument("--scales", default=",".join(str(s) for s in DEFAULT_SCALES), help="模板尺度，逗号分隔")
    parser.add_argument("--plan", default="", help="可选 director-plan.json，把帧号映射到 Shot")
    parser.add_argument("--limit", type=int, default=0, help="仅处理前 N 帧（0 表示全部）")
    parser.add_argument("--self-test-fixtures", default="", help="生成自测夹具到该目录后退出")
    args = parser.parse_args()

    if args.self_test_fixtures:
        summary = make_self_test_fixtures(Path(args.self_test_fixtures))
        print(json.dumps({"fixtures": summary}, ensure_ascii=False))
        return 0

    if not (args.frames and args.product and args.mask and args.out):
        parser.error("--frames / --product / --mask / --out 均为必需（或用 --self-test-fixtures）")

    frames_dir, product_dir, mask_dir, out_dir = (Path(p) for p in (args.frames, args.product, args.mask, args.out))
    out_dir.mkdir(parents=True, exist_ok=True)
    frame_files = sorted(frames_dir.glob("*.png"))
    product_files = sorted([p for p in product_dir.glob("*") if p.suffix.lower() in {".exr", ".png"}])
    mask_files = sorted(mask_dir.glob("frame_*.png")) or sorted(mask_dir.glob("*.png"))
    count = min(len(frame_files), len(product_files), len(mask_files))
    if args.limit:
        count = min(count, args.limit)

    scales = tuple(float(s) for s in args.scales.split(",") if s.strip())
    report: dict = {
        "task": "V3-06 双产品检测",
        "dilate_pixels": args.dilate,
        "thresholds": {
            "ncc": args.ncc_threshold,
            "histogram": args.hist_threshold,
            "edge_ratio": EDGE_RATIO_MIN,
            "mask_overlap_max": MASK_OVERLAP_MAX,
            "coarse_margin": COARSE_MARGIN,
        },
        "scales": list(scales),
        "frames": count,
        "frames_report": [],
        "problem_frames": [],
        "heatmaps": {},
    }
    if count == 0:
        report["verdict"] = "BLOCKED"
        report["passed"] = False
        report["blocked_reason"] = "输入帧不足（frames/product/mask 至少各需 1 帧）"
        (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
        return 1

    templates = build_templates(product_files[:count], mask_files[:count], scales)
    report["templates"] = [
        {"reference_frame": t["reference_frame"], "scale": t["scale"], "size": [t["width"], t["height"]]}
        for t in templates
    ]
    if not templates:
        report["verdict"] = "BLOCKED"
        report["passed"] = False
        report["blocked_reason"] = "无法从可信产品层提取有效模板（掩码为空或产品无纹理）"
        (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
        return 1

    shots = load_plan_shots(Path(args.plan)) if args.plan else None

    for index in range(count):
        frame_rgb = read_frame_srgb(frame_files[index])
        mask = read_mask(mask_files[index])
        if frame_rgb.shape[:2] != mask.shape:
            report["frames_report"].append({"frame": index, "verdict": "ERROR", "reason": "帧与掩码尺寸不一致"})
            report["problem_frames"].append(index)
            continue
        detections, heat = detect_frame(frame_rgb, mask, templates, args.dilate, args.ncc_threshold, args.hist_threshold)
        entry: dict = {"frame": index, "verdict": "FAIL" if detections else "PASS", "detections": detections}
        if shots is not None:
            shot = frame_to_shot(shots, index)
            if shot is not None:
                entry["shot_id"] = shot["shot_id"]
                entry["shot_name"] = shot["shot_name"]
        if detections:
            heatmap_name = f"heatmap_{index:04d}.png"
            render_heatmap(frame_rgb, heat, detections, out_dir / heatmap_name)
            entry["heatmap"] = heatmap_name
            report["heatmaps"][str(index)] = heatmap_name
            report["problem_frames"].append(index)
        report["frames_report"].append(entry)

    if report["problem_frames"]:
        report["verdict"] = "BLOCKED"
        report["passed"] = False
        report["blocked_reason"] = f"{len(report['problem_frames'])} 帧背景区检出多余产品影像：{report['problem_frames']}"
    else:
        report["verdict"] = "PASS"
        report["passed"] = True
        report["blocked_reason"] = ""
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
