"""V5-02：本地硬切/渐变转场检测与 cut time 指标。

- 硬切：ffmpeg scene 检测（SAD 阈值 ≥ 0.3），输出切点秒数与帧号；
- 渐变转场：scene 分数落在 [0.15, 0.3) 且相邻帧聚拢的窗口，单独评分、不与硬切混淆；
- `evaluate_cuts`：人工标注对照（±0.2s 容差），输出 precision/recall/F1（主规划 V5 门：F1 ≥ 0.90）。

不依赖远程服务；检测结果如实带阈值与算法说明（scene SAD，非精确切镜保证）。
"""
from __future__ import annotations

import re
import subprocess
import shutil
from pathlib import Path

HARD_CUT_THRESHOLD = 0.3
GRADUAL_LOW_THRESHOLD = 0.15
CUT_TOLERANCE_S = 0.2


def _ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg 未安装或未找到")
    return ffmpeg


def detect_scene_scores(video: Path, threshold: float = GRADUAL_LOW_THRESHOLD) -> list[tuple[float, float]]:
    """返回 (时间秒, scene_score) 列表（分数 ≥ threshold 的帧）。"""
    command = [
        _ffmpeg(), "-i", str(video),
        "-vf", f"select='gt(scene,{threshold})',metadata=print",
        "-an", "-f", "null", "-",
    ]
    completed = subprocess.run(command, capture_output=True, text=True, errors="replace")
    log = completed.stderr or ""
    scores: list[tuple[float, float]] = []
    pending_time: float | None = None
    for line in log.splitlines():
        match = re.search(r"pts_time[:=]([0-9.]+)", line)
        if match:
            pending_time = float(match.group(1))
            continue
        match = re.search(r"lavfi\.scene_score=([0-9.]+)", line)
        if match and pending_time is not None:
            scores.append((pending_time, float(match.group(1))))
            pending_time = None
    return scores


def detect_hard_cuts(video: Path, threshold: float = HARD_CUT_THRESHOLD) -> list[float]:
    return [time for time, score in detect_scene_scores(video, threshold) if score >= threshold]


def detect_gradual_windows(video: Path, max_gap_s: float = 0.4) -> list[dict]:
    """渐变转场窗口：scene 分数在 [0.15, 0.3) 且相邻帧间隔 ≤ max_gap_s 的聚簇。"""
    candidates = [
        (time, score) for time, score in detect_scene_scores(video, GRADUAL_LOW_THRESHOLD)
        if score < HARD_CUT_THRESHOLD
    ]
    windows: list[dict] = []
    for time, score in candidates:
        if windows and time - windows[-1]["end_s"] <= max_gap_s:
            windows[-1]["end_s"] = time
            windows[-1]["max_score"] = max(windows[-1]["max_score"], score)
        else:
            windows.append({"start_s": time, "end_s": time, "max_score": score})
    for window in windows:
        window["start_s"] = round(window["start_s"], 3)
        window["end_s"] = round(window["end_s"], 3)
        window["max_score"] = round(window["max_score"], 3)
    return windows


def segment_reference(video: Path, fps: float | None = None) -> dict:
    """按代理视频检测切镜并切分为段；返回 cuts/gradual/segments（含代表帧）。"""
    cuts = detect_hard_cuts(video)
    gradual = detect_gradual_windows(video)
    segments: list[dict] = []
    boundaries = sorted(set([0.0] + cuts))
    for index, start in enumerate(boundaries):
        end = boundaries[index + 1] if index + 1 < len(boundaries) else None
        segments.append({
            "index": index,
            "start_s": round(start, 3),
            "end_s": round(end, 3) if end is not None else None,
            "transition_in": "hard_cut" if index > 0 else None,
            "representative_frame": int(round(start * fps)) + 1 if fps else None,
        })
    return {
        "algorithm": "ffmpeg scene SAD（本地基线，非精确切镜保证）",
        "hard_cut_threshold": HARD_CUT_THRESHOLD,
        "gradual_low_threshold": GRADUAL_LOW_THRESHOLD,
        "cuts": [round(time, 3) for time in cuts],
        "gradual": gradual,
        "segments": segments,
    }


def evaluate_cuts(detected: list[float], annotated: list[float], tolerance_s: float = CUT_TOLERANCE_S) -> dict:
    """人工标注对照：±tolerance 内一对一最近匹配，输出 precision/recall/F1。"""
    matched_annotated: list[int] = []
    matched_detected: set[int] = set()
    for detection_index, cut in enumerate(detected):
        best_index = None
        best_distance = None
        for annotation_index, truth in enumerate(annotated):
            if annotation_index in matched_annotated:
                continue
            distance = abs(cut - truth)
            if distance <= tolerance_s and (best_distance is None or distance < best_distance):
                best_index = annotation_index
                best_distance = distance
        if best_index is not None:
            matched_annotated.append(best_index)
            matched_detected.add(detection_index)
    matched = len(matched_annotated)
    precision = matched / len(detected) if detected else (1.0 if not annotated else 0.0)
    recall = matched / len(annotated) if annotated else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "tolerance_s": tolerance_s,
        "detected_count": len(detected),
        "annotated_count": len(annotated),
        "matched": matched,
        "missed_annotation_indices": [i for i in range(len(annotated)) if i not in matched_annotated],
        "extra_detection_indices": [i for i in range(len(detected)) if i not in matched_detected],
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "passed_f1_gate": bool(f1 >= 0.90),
    }


MOTION_STATIC_ENERGY = 0.02
OBSERVATION_SAMPLE_FPS = 2
OBSERVATION_SIZE = (160, 120)


def _extract_observation_frames(video: Path, workdir: Path) -> list[Path]:
    """抽帧（2fps，160×120）供确定性观察计算。"""
    import numpy as np  # noqa: F401 延迟导入，仅观察计算需要

    frames_dir = workdir / "observation_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [_ffmpeg(), "-y", "-v", "error", "-i", str(video),
         "-vf", f"fps={OBSERVATION_SAMPLE_FPS},scale={OBSERVATION_SIZE[0]}:{OBSERVATION_SIZE[1]}",
         str(frames_dir / "obs_%04d.png")],
        check=True, capture_output=True,
    )
    return sorted(frames_dir.glob("obs_*.png"))


def segment_observations(video: Path, segments: list[dict], workdir: Path) -> dict[int, dict]:
    """确定性逐段观察（观察事实与推断分离，未知值如实为 null）。

    - 测量事实：亮度均值/标准差、运动能量（相邻抽帧差），evidence 为帧索引；
    - 推断：运动类型 static/moving（阈值启发式，置信度来自能量距离）；
    - 不可推断（单目视频/无主体检测模型）：shot_size / 焦距 / 相机路径 / 主体位置 = null。
    """
    import numpy as np
    from PIL import Image

    frames = _extract_observation_frames(video, workdir)
    arrays = []
    for path in frames:
        with Image.open(path) as image:
            arrays.append(np.asarray(image.convert("L"), dtype=np.float32) / 255.0)
    observations: dict[int, dict] = {}
    sample_interval = 1.0 / OBSERVATION_SAMPLE_FPS
    for segment in segments:
        start = float(segment["start_s"])
        end = float(segment["end_s"]) if segment["end_s"] is not None else (len(frames) - 1) * sample_interval
        start_index = int(round(start * OBSERVATION_SAMPLE_FPS))
        # 窗口上界不含下一段首帧，避免把跨切点的颜色跳变误判为本段运动
        end_index = min(len(frames), int(round(end * OBSERVATION_SAMPLE_FPS)))
        if end_index <= start_index:
            end_index = min(len(frames), start_index + 1)
        window = arrays[max(0, start_index):max(1, end_index)]
        if not window:
            window = arrays[:1]
        stacked = np.stack(window)
        brightness = float(stacked.mean())
        brightness_std = float(stacked.std())
        motion_energy = 0.0
        if len(window) > 1:
            motion_energy = float(np.mean(np.abs(np.diff(stacked, axis=0))))
        if motion_energy < MOTION_STATIC_ENERGY:
            motion_type = "static"
            confidence = 1.0 - motion_energy / MOTION_STATIC_ENERGY
        else:
            motion_type = "moving"
            confidence = min(0.9, (motion_energy - MOTION_STATIC_ENERGY) / 0.15)
        observations[segment["index"]] = {
            "brightness_mean": {"value": round(brightness, 4), "confidence": 1.0,
                                "evidence_frame": start_index, "method": "像素测量（抽帧 2fps）"},
            "brightness_std": {"value": round(brightness_std, 4), "confidence": 1.0,
                               "evidence_frame": start_index, "method": "像素测量（抽帧 2fps）"},
            "motion_energy": {"value": round(motion_energy, 4), "confidence": 1.0,
                              "evidence_frame": start_index, "method": "相邻抽帧平均绝对差"},
            "motion_type": {"value": motion_type, "confidence": round(max(0.0, min(1.0, confidence)), 4),
                            "evidence_frame": start_index, "method": "运动能量阈值启发式（非精确分类）"},
            "shot_size": {"value": None, "confidence": None, "evidence_frame": None,
                          "method": "无主体检测模型（未配置），景别不可推断"},
            "focal_length_mm": {"value": None, "confidence": None, "evidence_frame": None,
                                "method": "单目视频不恢复真实焦距（如实不可推断）"},
            "camera_path": {"value": None, "confidence": None, "evidence_frame": None,
                            "method": "单目视频不恢复相机路径（如实不可推断）"},
            "subject_position": {"value": None, "confidence": None, "evidence_frame": None,
                                 "method": "无主体检测模型（未配置）"},
        }
    return observations
