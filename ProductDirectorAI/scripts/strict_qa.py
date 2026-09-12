#!/usr/bin/env python3
"""V3-07：Strict QA 自动阻断门。

对一组渲染通道产物（beauty 帧 PNG/EXR、mask PNG 序列，可选 depth/normal EXR）
跑五项检查，**任一项 FAIL 即整体 BLOCKED**（出口码非 0）：

1. ``missing_frames``   缺帧：帧序号连续性，缺号即 FAIL 并列出缺失帧号；
2. ``mask_integrity``   Mask/ID 错误：掩码非二值（二值度低于阈值）、全空/全满、
   掩码覆盖率与 beauty alpha 覆盖率逐帧不一致（口径同 V3_TASK_BRIEF 10.3：
   mask 取 alpha>0.5 的覆盖率，与 beauty alpha 覆盖率逐帧比较）；
3. ``size_stability``   尺寸变化：产品掩码包围盒面积的**相对突变**检测
   （相邻帧面积变化比例超过 ``--size-tol`` 即 FAIL；默认 0.35，
   可容纳正常环绕镜头的渐进透视变化，只抓突变）；
4. ``contour_anomaly``  轮廓异常：相邻帧掩码 IoU 骤降（低于 ``--iou-min``）
   或质心位移超过 ``--centroid-shift``（按画面对角线归一化）即 FAIL；
5. ``logo_presence``    Logo 缺失：输入 ``--logo-region x,y,w,h``（归一化坐标，
   对应 V3-04 的 logo_regions）与 ``--reference <参考帧>``，把被检帧 Logo 区域
   与参考帧同区域做结构相似度（SSIM）比较，低于 ``--logo-threshold`` 即 FAIL；
   未提供 logo 参数时本项 SKIPPED 并在报告中注明。

检查器之间**解耦**：单项异常不中断其他项，报告同时列出多类故障。
每张问题帧在 ``--out`` 目录输出叠图/热图 PNG（轮廓对比、二值可视化、
掩码 vs alpha 差异、Logo 区域差异热图），报告写入 ``<out>/report.json``
（含逐帧判定、问题帧、阻断原因）。支持可选 ``--plan <director-plan.json>``：
按镜头 ``duration_frames`` 累计区间把帧号映射到 Shot（shot_id + 镜内帧号）。

出口码约定：0 = 全部通过（SKIPPED 不算失败），1 = 检出故障并阻断。

用法：
    .venv/bin/python scripts/strict_qa.py \
        --beauty <passes/beauty> --mask <passes/mask> --out <qa_dir> \
        --plan <director-plan.json> --logo-region 0.40,0.40,0.20,0.20 \
        --reference <passes/beauty/frame_0000.png>

注意：本机托管解释器没有 OpenEXR，EXR 读取在函数内**延迟导入**；
本地测试只用 PNG 合成数据，不依赖 EXR 输入。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np
from PIL import Image

IMAGE_SUFFIXES = {".png", ".exr"}

# ---------------------------------------------------------------------------
# 读取工具
# ---------------------------------------------------------------------------


def frame_index(path: Path) -> int:
    """从文件名取最后一串数字作为帧号（frame_0007.png / beauty_0007.exr → 7）。"""
    digits = re.findall(r"\d+", path.stem)
    if not digits:
        raise ValueError(f"文件名中找不到帧号: {path.name}")
    return int(digits[-1])


def index_frames(directory: Path) -> dict[int, Path]:
    """把目录里的帧文件整理成 {帧号: 路径}。"""
    files = sorted(p for p in directory.glob("*") if p.suffix.lower() in IMAGE_SUFFIXES)
    return {frame_index(p): p for p in files}


def read_mask_alpha(path: Path) -> np.ndarray:
    """读 mask PNG，取 alpha 通道为 0..1 浮点（口径：alpha>0.5 为产品）。"""
    with Image.open(path) as image:
        return np.asarray(image.convert("RGBA").split()[3], dtype=np.float32) / 255.0


def _read_beauty_exr(path: Path) -> tuple[np.ndarray, np.ndarray | None]:
    """读 beauty EXR（线性浮点），返回 (rgb, alpha)；OpenEXR 延迟导入。"""
    import OpenEXR

    part = OpenEXR.File(str(path)).parts[0]
    channels = part.channels
    names = list(channels.keys()) if hasattr(channels, "keys") else [c.name for c in channels]
    window = part.header.get("dataWindow")
    try:
        width, height = int(window.max.x) + 1, int(window.max.y) + 1
    except AttributeError:
        width, height = int(window[1][0]) + 1, int(window[1][1]) + 1
    planes = {}
    for name in names:
        data = getattr(channels[name], "pixels", None)
        if data is None:
            continue
        array = np.array(data, dtype=np.float32)
        components = max(1, array.size // (width * height))
        planes[name] = array.reshape(height, width, components)
    alpha = None
    for name, plane in planes.items():
        if "alpha" in name.lower():
            alpha = plane[:, :, 0] if plane.ndim == 3 else plane
            break
    beauty = None
    for name, plane in planes.items():
        if "beauty" in name.lower():
            beauty = plane
            break
    if beauty is None:
        beauty = planes[names[0]]
    if beauty.ndim == 2:
        beauty = beauty[:, :, None]
    rgb = np.clip(beauty[:, :, :3], 0.0, None)
    if alpha is None and beauty.shape[2] >= 4:
        alpha = beauty[:, :, 3]
    return rgb, alpha


def read_beauty(path: Path) -> tuple[np.ndarray, np.ndarray | None]:
    """读一帧 beauty，返回 (rgb 0..1 浮点, alpha 0..1 浮点或 None)。

    PNG 为 sRGB 编码（直接取 0..1），EXR 为线性浮点；Logo 相似度只在
    同一编码来源之间比较（参考帧与被检帧同格式），不做跨空间换算。
    """
    if path.suffix.lower() == ".exr":
        return _read_beauty_exr(path)
    with Image.open(path) as image:
        rgba = np.asarray(image.convert("RGBA"), dtype=np.float32) / 255.0
    return rgba[:, :, :3], rgba[:, :, 3]


# ---------------------------------------------------------------------------
# 叠图 / 热图输出
# ---------------------------------------------------------------------------


def save_mask_overlay(path: Path, reference: np.ndarray, current: np.ndarray) -> None:
    """掩码轮廓对比叠图：红=仅参考帧，绿=仅当前帧，白=两帧重叠。"""
    rgb = np.zeros((*reference.shape, 3), dtype=np.uint8)
    rgb[reference & ~current] = (255, 0, 0)
    rgb[current & ~reference] = (0, 255, 0)
    rgb[reference & current] = (255, 255, 255)
    Image.fromarray(rgb).save(path)


def save_binariness_overlay(path: Path, alpha: np.ndarray) -> None:
    """二值度可视化：白=产品，黑=背景，红=既非产品也非背景的中间值像素。"""
    rgb = np.zeros((*alpha.shape, 3), dtype=np.uint8)
    rgb[alpha > 0.95] = (255, 255, 255)
    mid = (alpha >= 0.05) & (alpha <= 0.95)
    rgb[mid] = (255, 0, 0)
    Image.fromarray(rgb).save(path)


def save_logo_heatmap(path: Path, current: np.ndarray, reference: np.ndarray) -> None:
    """Logo 区域差异热图：红=差异大，绿=差异小（差异放大 4 倍显示）。"""
    diff = np.abs(current.astype(np.float32) - reference.astype(np.float32)).mean(axis=2)
    heat = np.clip(diff * 4.0, 0.0, 1.0)
    rgb = np.stack([heat, 1.0 - heat, np.zeros_like(heat)], axis=2)
    Image.fromarray((rgb * 255 + 0.5).astype(np.uint8)).save(path)


# ---------------------------------------------------------------------------
# 指标计算
# ---------------------------------------------------------------------------


def binariness(alpha: np.ndarray) -> float:
    """二值度：alpha 接近 0 或 1 的像素占比（口径同 validate_fidelity_passes）。"""
    return float(((alpha > 0.95) | (alpha < 0.05)).mean())


def bbox_area(mask: np.ndarray) -> int:
    """产品掩码包围盒面积（像素）；空掩码返回 0。"""
    ys, xs = np.nonzero(mask)
    if ys.size == 0:
        return 0
    return int((ys.max() - ys.min() + 1) * (xs.max() - xs.min() + 1))


def mask_iou(a: np.ndarray, b: np.ndarray) -> float:
    """两个布尔掩码的交并比；两者皆空时约定为 1（无轮廓可比）。"""
    union = float((a | b).sum())
    if union == 0:
        return 1.0
    return float((a & b).sum()) / union


def centroid_shift(a: np.ndarray, b: np.ndarray) -> float:
    """质心位移，按画面对角线长度归一化；任一空掩码返回 1（视为最大异常）。"""
    def centroid(m: np.ndarray):
        ys, xs = np.nonzero(m)
        if ys.size == 0:
            return None
        return np.array([ys.mean(), xs.mean()])

    ca, cb = centroid(a), centroid(b)
    if ca is None or cb is None:
        return 1.0
    diagonal = float(np.hypot(*a.shape))
    return float(np.linalg.norm(ca - cb)) / diagonal


def ssim_block(a: np.ndarray, b: np.ndarray) -> float:
    """块级结构相似度（全局统计版 SSIM），输入为 0..1 灰度块。"""
    a64 = a.astype(np.float64)
    b64 = b.astype(np.float64)
    c1, c2 = 0.01 ** 2, 0.03 ** 2
    mu_a, mu_b = a64.mean(), b64.mean()
    var_a, var_b = a64.var(), b64.var()
    cov = float(((a64 - mu_a) * (b64 - mu_b)).mean())
    numerator = (2 * mu_a * mu_b + c1) * (2 * cov + c2)
    denominator = (mu_a ** 2 + mu_b ** 2 + c1) * (var_a + var_b + c2)
    return float(numerator / denominator) if denominator else 0.0


def to_gray(rgb: np.ndarray) -> np.ndarray:
    """Rec.601 灰度化。"""
    return 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]


def region_pixels(region: tuple[float, float, float, float], shape: tuple[int, int]) -> tuple[int, int, int, int]:
    """归一化 (x, y, w, h) → 像素 (y0, y1, x0, x1)，裁剪到画面内。"""
    height, width = shape
    x, y, w, h = region
    x0 = int(round(x * width))
    y0 = int(round(y * height))
    x1 = int(round((x + w) * width))
    y1 = int(round((y + h) * height))
    return max(0, y0), min(height, y1), max(0, x0), min(width, x1)


# ---------------------------------------------------------------------------
# Shot 定位
# ---------------------------------------------------------------------------


class ShotLocator:
    """按 director-plan 的 shots.duration_frames 累计区间做帧号 → Shot 映射。

    plan JSON 形如 {"intent": ..., "shots": [{"id","name","duration_frames"}, ...]}；
    区间从被检序列的最小帧号开始累计。不提供 plan 时只报帧号。
    """

    def __init__(self, plan_path: Path | None, first_frame: int):
        self.intervals: list[tuple[int, int, str, str]] = []
        if plan_path is None:
            return
        plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
        start = first_frame
        for shot in plan.get("shots", []):
            duration = int(shot.get("duration_frames", 0))
            self.intervals.append((start, start + duration, str(shot.get("id", "")), str(shot.get("name", ""))))
            start += duration

    def locate(self, frame: int) -> dict:
        for start, end, shot_id, name in self.intervals:
            if start <= frame < end:
                return {"shot": shot_id, "shot_name": name, "shot_frame": frame - start}
        return {"shot": None, "shot_name": None, "shot_frame": None}


# ---------------------------------------------------------------------------
# 五项检查（相互解耦，各自返回 {"status", "problems"}）
# ---------------------------------------------------------------------------


def check_missing_frames(sequences: dict[str, dict[int, Path]], expected_frames: int) -> dict:
    """缺帧：任一通道序列在 [min, max]（或 --frames 指定范围）内缺号即 FAIL。"""
    all_indices = sorted({i for seq in sequences.values() for i in seq})
    if not all_indices:
        return {"status": "FAIL", "problems": [{"frame": None, "reason": "输入帧不足（各通道目录均为空）"}]}
    start = min(all_indices)
    stop = start + expected_frames - 1 if expected_frames else max(all_indices)
    problems = []
    for frame in range(start, stop + 1):
        absent = [name for name, seq in sequences.items() if frame not in seq]
        if absent:
            problems.append({"frame": frame, "reason": f"缺帧：以下通道缺少该帧 {absent}"})
    return {"status": "FAIL" if problems else "PASS", "missing": [p["frame"] for p in problems], "problems": problems}


def check_mask_integrity(frames: list[int], masks: dict[int, Path], beauty: dict[int, Path],
                         out_dir: Path, binariness_min: float, coverage_tol: float) -> dict:
    """Mask/ID 错误：非二值、全空/全满、掩码与 beauty alpha 覆盖率不一致。"""
    problems = []
    for frame in frames:
        alpha = read_mask_alpha(masks[frame])
        _, beauty_alpha = read_beauty(beauty[frame])
        if beauty_alpha is not None and beauty_alpha.shape != alpha.shape:
            problems.append({"frame": frame, "reason": f"掩码尺寸 {alpha.shape} 与 beauty 尺寸 {beauty_alpha.shape} 不一致"})
            continue
        mask_cov = float((alpha > 0.5).mean())
        ratio = binariness(alpha)
        if ratio < binariness_min:
            artifact = f"mask_integrity_binariness_frame_{frame:04d}.png"
            save_binariness_overlay(out_dir / artifact, alpha)
            problems.append({"frame": frame, "reason": f"掩码非二值：二值度 {ratio:.4f} < {binariness_min}", "artifact": artifact})
        if mask_cov <= 0.0005:
            problems.append({"frame": frame, "reason": f"掩码全空：覆盖率 {mask_cov:.6f}"})
        elif mask_cov >= 0.9995:
            problems.append({"frame": frame, "reason": f"掩码全满：覆盖率 {mask_cov:.6f}"})
        if beauty_alpha is not None:
            alpha_cov = float((beauty_alpha > 0.5).mean())
            if abs(mask_cov - alpha_cov) > coverage_tol:
                artifact = f"mask_integrity_coverage_frame_{frame:04d}.png"
                save_mask_overlay(out_dir / artifact, beauty_alpha > 0.5, alpha > 0.5)
                problems.append({
                    "frame": frame,
                    "reason": f"掩码覆盖率 {mask_cov:.4f} 与 beauty alpha 覆盖率 {alpha_cov:.4f} 不一致（容差 {coverage_tol}）",
                    "artifact": artifact,
                })
    return {"status": "FAIL" if problems else "PASS", "problems": problems}


def check_size_stability(frames: list[int], masks_bool: dict[int, np.ndarray], size_tol: float) -> dict:
    """尺寸变化：相邻帧掩码包围盒面积的相对突变超过 size_tol 即 FAIL。"""
    problems = []
    areas = {frame: bbox_area(masks_bool[frame]) for frame in frames}
    for prev, curr in zip(frames, frames[1:]):
        area_prev, area_curr = areas[prev], areas[curr]
        if area_prev == 0 or area_curr == 0:
            continue  # 空掩码由 mask_integrity 报告
        change = abs(area_curr - area_prev) / area_prev
        if change > size_tol:
            problems.append({
                "frame": curr,
                "reason": f"掩码包围盒面积相对突变：{area_prev} → {area_curr}（变化 {change:.1%} > {size_tol:.1%}，参考帧 {prev}）",
            })
    return {"status": "FAIL" if problems else "PASS", "problems": problems}


def check_contour_anomaly(frames: list[int], masks_bool: dict[int, np.ndarray],
                          out_dir: Path, iou_min: float, shift_max: float) -> dict:
    """轮廓异常：相邻帧掩码 IoU 骤降或质心位移超阈值即 FAIL，输出轮廓对比叠图。"""
    problems = []
    for prev, curr in zip(frames, frames[1:]):
        iou = mask_iou(masks_bool[prev], masks_bool[curr])
        shift = centroid_shift(masks_bool[prev], masks_bool[curr])
        reasons = []
        if iou < iou_min:
            reasons.append(f"IoU 骤降 {iou:.4f} < {iou_min}")
        if shift > shift_max:
            reasons.append(f"质心位移 {shift:.4f} > {shift_max}（按画面对角线归一化）")
        if reasons:
            artifact = f"contour_anomaly_frame_{curr:04d}.png"
            save_mask_overlay(out_dir / artifact, masks_bool[prev], masks_bool[curr])
            problems.append({
                "frame": curr,
                "reason": f"轮廓突变（参考帧 {prev}）：" + "；".join(reasons),
                "artifact": artifact,
            })
    return {"status": "FAIL" if problems else "PASS", "problems": problems}


def check_logo_presence(frames: list[int], beauty: dict[int, Path], out_dir: Path,
                        logo_region, reference: str, threshold: float) -> dict:
    """Logo 缺失：被检帧 Logo 区域与参考帧同区域做 SSIM，低于阈值即 FAIL。

    未提供 --logo-region/--reference 时本项 SKIPPED。
    """
    if not logo_region or not reference:
        return {"status": "SKIPPED", "reason": "未提供 --logo-region / --reference，Logo 检查跳过", "problems": []}
    ref_rgb, _ = read_beauty(Path(reference))
    y0, y1, x0, x1 = region_pixels(logo_region, ref_rgb.shape[:2])
    if y1 <= y0 or x1 <= x0:
        return {"status": "FAIL", "problems": [{"frame": None, "reason": f"Logo 区域 {logo_region} 在参考帧中为空"}]}
    ref_block = to_gray(ref_rgb[y0:y1, x0:x1])
    problems = []
    scores = {}
    for frame in frames:
        rgb, _ = read_beauty(beauty[frame])
        block = to_gray(rgb[y0:y1, x0:x1])
        if block.shape != ref_block.shape:
            problems.append({"frame": frame, "reason": f"Logo 区域尺寸 {block.shape} 与参考 {ref_block.shape} 不一致"})
            continue
        score = ssim_block(block, ref_block)
        scores[frame] = round(score, 4)
        if score < threshold:
            artifact = f"logo_presence_heatmap_frame_{frame:04d}.png"
            save_logo_heatmap(out_dir / artifact, rgb[y0:y1, x0:x1], ref_rgb[y0:y1, x0:x1])
            problems.append({
                "frame": frame,
                "reason": f"Logo 区域结构相似度 {score:.4f} < {threshold}，判定 Logo 缺失",
                "artifact": artifact,
            })
    return {"status": "FAIL" if problems else "PASS", "scores": scores, "problems": problems}


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="V3-07 Strict QA 自动阻断门")
    parser.add_argument("--beauty", required=True, help="beauty 帧目录（PNG 或 EXR）")
    parser.add_argument("--mask", required=True, help="产品遮罩目录（PNG）")
    parser.add_argument("--depth", default="", help="可选 depth 帧目录（EXR，仅做连续性检查）")
    parser.add_argument("--normal", default="", help="可选 normal 帧目录（EXR，仅做连续性检查）")
    parser.add_argument("--out", required=True, help="QA 输出目录（report.json + 问题帧叠图/热图）")
    parser.add_argument("--plan", default="", help="可选 director-plan.json，用于 Shot/frame 定位")
    parser.add_argument("--frames", type=int, default=0, help="期望帧数（0 = 按实际帧范围自动推断）")
    parser.add_argument("--logo-region", default="", help="归一化 Logo 区域 x,y,w,h（对应 V3-04 logo_regions）")
    parser.add_argument("--reference", default="", help="Logo 参考帧路径（beauty 帧）")
    parser.add_argument("--size-tol", type=float, default=0.35, help="包围盒面积相对突变容差（默认 0.35）")
    parser.add_argument("--iou-min", type=float, default=0.5, help="相邻帧掩码 IoU 下限（默认 0.5）")
    parser.add_argument("--centroid-shift", type=float, default=0.25, help="质心位移上限（按画面对角线归一化，默认 0.25）")
    parser.add_argument("--binariness-min", type=float, default=0.98, help="掩码二值度下限（默认 0.98）")
    parser.add_argument("--coverage-tol", type=float, default=0.05, help="掩码与 beauty alpha 覆盖率容差（默认 0.05）")
    parser.add_argument("--logo-threshold", type=float, default=0.8, help="Logo 区域 SSIM 下限（默认 0.8）")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    sequences: dict[str, dict[int, Path]] = {
        "beauty": index_frames(Path(args.beauty)),
        "mask": index_frames(Path(args.mask)),
    }
    if args.depth:
        sequences["depth"] = index_frames(Path(args.depth))
    if args.normal:
        sequences["normal"] = index_frames(Path(args.normal))

    logo_region = None
    if args.logo_region:
        parts = [float(v) for v in args.logo_region.split(",")]
        if len(parts) != 4:
            raise SystemExit("--logo-region 必须是 x,y,w,h 四个归一化数值")
        logo_region = tuple(parts)

    report: dict = {
        "passed": False,
        "blocked": True,
        "inputs": {name: len(seq) for name, seq in sequences.items()},
        "checks": {},
        "problems": [],
        "blocked_reasons": [],
    }

    # 逐帧检查只在 beauty ∩ mask 共有的帧上进行；缺号帧由 missing_frames 报告
    frames = sorted(set(sequences["beauty"]) & set(sequences["mask"]))
    first_frame = frames[0] if frames else 0
    locator = ShotLocator(Path(args.plan) if args.plan else None, first_frame)

    masks_bool: dict[int, np.ndarray] = {frame: read_mask_alpha(sequences["mask"][frame]) > 0.5 for frame in frames}

    checks = {
        "missing_frames": check_missing_frames(sequences, args.frames),
        "mask_integrity": check_mask_integrity(frames, sequences["mask"], sequences["beauty"],
                                               out_dir, args.binariness_min, args.coverage_tol),
        "size_stability": check_size_stability(frames, masks_bool, args.size_tol),
        "contour_anomaly": check_contour_anomaly(frames, masks_bool, out_dir, args.iou_min, args.centroid_shift),
        "logo_presence": check_logo_presence(frames, sequences["beauty"], out_dir,
                                             logo_region, args.reference, args.logo_threshold),
    }
    report["checks"] = checks

    for name, result in checks.items():
        if result["status"] == "FAIL":
            report["blocked_reasons"].append(f"{name}: {len(result['problems'])} 个问题")
        for problem in result["problems"]:
            entry = {"check": name, **problem}
            if problem.get("frame") is not None:
                entry.update(locator.locate(problem["frame"]))
            report["problems"].append(entry)

    report["passed"] = all(result["status"] != "FAIL" for result in checks.values())
    report["blocked"] = not report["passed"]
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("STRICT_QA " + json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
