"""V6-03 音频后期：BGM 处理、ducking 混音、响度测量与时长适配执行。

诚实边界（对应主规划 12.4）：
- 响度目标 **-16 LUFS ±1.5 LU、真峰值 ≤ -1 dBTP 是本产品默认**，不是任何平台的官方要求；
  测量值一律来自真实 ffmpeg ebur128，不达标就如实报告失败。
- BGM 必须有素材与许可引用；未知/非商业许可不得用于商业用途 Profile（由 V6-01 的
  Profile 校验与调用方共同拦截）。
- 配音过长只允许三种策略：在 Profile 允许范围内延长**非接触**镜头、在允许速率范围内
  调速、改文案重新生成；不截断视频或旁白，也不对人物接触镜头变速。
- 音轨开关必须真的改变输出（关闭即无该音轨的贡献），不做"开关无效"的假实现。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

FFMPEG_FALLBACK = "/usr/bin/ffmpeg"
FFPROBE_FALLBACK = "/usr/bin/ffprobe"
DEFAULT_TARGET_LUFS = -16.0
DEFAULT_TOLERANCE_LU = 1.5
DEFAULT_TRUE_PEAK_DBTP = -1.0


def ffmpeg_binary() -> str:
    return shutil.which("ffmpeg") or FFMPEG_FALLBACK


def ffprobe_binary() -> str:
    return shutil.which("ffprobe") or FFPROBE_FALLBACK


def probe_audio(path: Path) -> dict:
    """真实探测音频流（时长/声道/采样率）。"""
    result = subprocess.run(
        [ffprobe_binary(), "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_name,channels,sample_rate,duration",
         "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = {}
    streams = payload.get("streams") or []
    stream = streams[0] if streams else {}
    duration = stream.get("duration") or (payload.get("format") or {}).get("duration")
    return {
        "has_audio": bool(stream),
        "codec": stream.get("codec_name"),
        "channels": int(stream["channels"]) if stream.get("channels") else 0,
        "sample_rate": int(stream["sample_rate"]) if stream.get("sample_rate") else 0,
        "duration_s": round(float(duration), 3) if duration else 0.0,
    }


def measure_loudness(path: Path) -> dict:
    """EBU R128 实测：综合响度、响度范围、真峰值（ffmpeg ebur128, peak=true）。"""
    result = subprocess.run(
        [ffmpeg_binary(), "-v", "info", "-i", str(path),
         "-filter_complex", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return {"available": False, "reason": (result.stderr or "")[-300:]}
    text = result.stderr
    integrated = None
    lra = None
    true_peak = None
    integrated_matches = re.findall(r"I:\s*(-?\d+\.\d+)\s*LUFS", text)
    lra_matches = re.findall(r"LRA:\s*(-?\d+\.\d+)\s*LU", text)
    peak_matches = re.findall(r"Peak:\s*(-?\d+\.\d+)\s*dBFS", text)
    if integrated_matches:
        integrated = float(integrated_matches[-1])
    if lra_matches:
        lra = float(lra_matches[-1])
    if peak_matches:
        true_peak = float(peak_matches[-1])
    return {
        "available": True,
        "integrated_lufs": integrated,
        "loudness_range_lu": lra,
        "true_peak_dbtp": true_peak,
        "method": "ffmpeg ebur128(peak=true)",
    }


def loudness_verdict(
    measurement: dict, *, target_lufs: float = DEFAULT_TARGET_LUFS,
    tolerance_lu: float = DEFAULT_TOLERANCE_LU, true_peak_max_dbtp: float = DEFAULT_TRUE_PEAK_DBTP,
) -> dict:
    """按产品默认目标判定；不达标如实列出偏差（不静默"通过"）。"""
    failures: list[str] = []
    integrated = measurement.get("integrated_lufs")
    peak = measurement.get("true_peak_dbtp")
    if not measurement.get("available") or integrated is None or peak is None:
        return {"passed": False, "failures": ["响度测量不可用：" + str(measurement.get("reason"))],
                "deviation_lu": None}
    deviation = integrated - target_lufs
    if abs(deviation) > tolerance_lu:
        failures.append(f"综合响度 {integrated} LUFS 偏离目标 {target_lufs} LUFS 超过 ±{tolerance_lu} LU（偏差 {deviation:+.2f} LU）")
    if peak > true_peak_max_dbtp + 0.05:
        failures.append(f"真峰值 {peak} dBTP 高于上限 {true_peak_max_dbtp} dBTP")
    return {
        "passed": not failures,
        "failures": failures,
        "target_lufs": target_lufs,
        "tolerance_lu": tolerance_lu,
        "true_peak_max_dbtp": true_peak_max_dbtp,
        "deviation_lu": round(deviation, 2),
        "note": "响度目标为本产品默认，不代表平台官方要求",
    }


def _atempo_chain(rate: float) -> list[str]:
    """把 0.5–2.0 的速率拆成 atempo 链（单个 atempo 只支持 0.5–2.0）。"""
    filters = []
    remaining = rate
    while remaining > 2.0:
        filters.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5
    if abs(remaining - 1.0) > 1e-6:
        filters.append(f"atempo={remaining:.4f}")
    return filters


def verify_ducking(
    *, voice_path: Path, music_path: Path, duration_s: float, music_gain_db: float = -14.0,
    ducking: dict | None = None, workdir: Path | None = None,
) -> dict:
    """直接渲染"音乐分支"来验证 ducking 是否真的生效（旁白不参与测量）。

    为什么不能直接比较成品：响度归一会把不同处理后的成品拉回同一目标响度，
    跨文件或频段比较都会被抵消或污染。这里把 ducking 前后的音乐分支分别渲染出来，
    再在**语音段与停顿段**两个窗口测量同一频段电平，得到真实的压低量。
    """
    workdir = workdir or (music_path.parent / "ducking-verify")
    workdir.mkdir(parents=True, exist_ok=True)
    spec = dict(ducking or {})
    spec.setdefault("enabled", True)
    output_spec = {
        "duration_s": duration_s, "music_gain_db": music_gain_db,
        "fade_in_s": 0.0, "fade_out_s": 0.0, "loop_music": True,
        "target_lufs": 0.0, "true_peak_max_dbtp": 0.0,
    }
    ducked_path = workdir / "music-branch-ducked.wav"
    plain_path = workdir / "music-branch-plain.wav"
    ducked_report = _render_music_branch(
        voice_path=voice_path, music_path=music_path, out_path=ducked_path,
        ducking=spec, **output_spec,
    )
    _render_music_branch(
        voice_path=voice_path, music_path=music_path, out_path=plain_path,
        ducking={**spec, "enabled": False}, **output_spec,
    )
    speech = detect_speech_windows(voice_path)
    segments = speech.get("segments") or []
    gaps = speech.get("gaps") or []
    if not segments or not gaps:
        return {"verified": False, "reason": "配音中没有可用的语音段/停顿段，无法验证 ducking",
                "ducking": ducked_report}
    speech_window = (segments[0][0] + 0.05, max(0.15, min(0.4, (segments[0][1] - segments[0][0]) / 2)))
    gap_window = (gaps[0][0] + 0.05, 0.25)
    measurement = {}
    for label, path in (("ducked", ducked_path), ("plain", plain_path)):
        measurement[label] = {
            "music_in_speech_db": measure_window_level(path, start_s=speech_window[0],
                                                       duration_s=speech_window[1])["mean_volume_db"],
            "music_in_gap_db": measure_window_level(path, start_s=gap_window[0],
                                                    duration_s=gap_window[1])["mean_volume_db"],
        }
        values = measurement[label]
        values["drop_db"] = (
            round(values["music_in_gap_db"] - values["music_in_speech_db"], 2)
            if None not in (values["music_in_gap_db"], values["music_in_speech_db"]) else None
        )
    ducked_drop = measurement["ducked"]["drop_db"]
    plain_drop = measurement["plain"]["drop_db"]
    verified = (
        ducked_drop is not None and ducked_drop > 1.0
        and plain_drop is not None and abs(plain_drop) < 1.0
    )
    return {
        "verified": verified,
        "speech_window": speech_window,
        "gap_window": gap_window,
        "measurement": measurement,
        "ducking": ducked_report,
        "method": "把 ducking 前后的音乐分支分别渲染后在语音段/停顿段比较同一频段电平",
        "note": "成品响度归一会抵消跨文件差异，因此 ducking 验收在分支层测量",
    }


def _render_music_branch(
    *, voice_path: Path, music_path: Path, out_path: Path, duration_s: float,
    music_gain_db: float, ducking: dict, fade_in_s: float, fade_out_s: float, loop_music: bool,
    target_lufs: float, true_peak_max_dbtp: float,
) -> dict:
    """只输出音乐分支（含可选 ducking），用于 ducking 验收测量。"""
    ffmpeg = ffmpeg_binary()
    inputs: list[str] = []
    filters: list[str] = []
    if loop_music:
        inputs += ["-stream_loop", "-1"]
    inputs += ["-i", str(music_path)]
    steps = [f"volume={music_gain_db}dB", f"atrim=0:{duration_s}", "asetpts=N/SR/TB",
             "aformat=channel_layouts=stereo:sample_rates=48000"]
    if fade_in_s > 0:
        steps.insert(3, f"afade=t=in:st=0:d={fade_in_s}")
    if fade_out_s > 0 and duration_s > fade_out_s:
        steps.insert(-1, f"afade=t=out:st={max(0.0, duration_s - fade_out_s)}:d={fade_out_s}")
    filters.append("[0:a]" + ",".join(steps) + "[music]")
    report = {"enabled": False}
    label = "[music]"
    if ducking.get("enabled", True):
        # 仅启用 ducking 时才需要旁白输入（否则会产生未连接的输出 pad）
        inputs += ["-i", str(voice_path)]
        filters.append(
            "[1:a]apad=whole_dur=" + str(duration_s) + ",aformat=channel_layouts=stereo:sample_rates=48000[voice]"
        )
        threshold_db, source, measured = _ducking_threshold(voice_path, ducking)
        linear = 10 ** (threshold_db / 20.0)
        ratio = float(ducking.get("ratio", 4.0))
        attack = float(ducking.get("attack_ms", 20.0))
        release = float(ducking.get("release_ms", 250.0))
        filters.append(
            f"[music][voice]sidechaincompress=threshold={linear:.6f}:ratio={ratio}:"
            f"attack={attack}:release={release}:makeup=1[ducked]"
        )
        label = "[ducked]"
        report = {"enabled": True, "threshold_db": round(threshold_db, 2), "threshold_source": source,
                  "voice_mean_level_db": measured, "ratio": ratio, "attack_ms": attack,
                  "release_ms": release, "sidechain": "voice"}
    command = [ffmpeg, "-y", "-v", "error", *inputs, "-filter_complex", ";".join(filters),
               "-map", label, "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(out_path)]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0 or not out_path.exists():
        raise RuntimeError("ducking 分支渲染失败: " + (result.stderr or "")[-400:])
    return report


def _ducking_threshold(voice_path: Path, ducking: dict) -> tuple[float, str, float | None]:
    """检测阈值：显式值优先；否则从实测旁白电平自动推导（固定阈值在真实素材上不可靠）。"""
    if ducking.get("threshold_db") is not None:
        return float(ducking["threshold_db"]), "explicit", None
    voice_probe = probe_audio(voice_path)
    level = measure_window_level(voice_path, start_s=0.0, duration_s=max(0.2, voice_probe["duration_s"] or 1.0))
    base = level["mean_volume_db"]
    threshold = max(-45.0, min(-12.0, (base if base is not None else -23.0) - 6.0))
    return threshold, "auto_from_voice_level", base


def detect_speech_windows(path: Path, *, noise_db: float = -35.0, min_silence_s: float = 0.25) -> dict:
    """用 ffmpeg silencedetect 求语音段与停顿段（ducking 验收与字幕对齐共用同一实测口径）。"""
    ffmpeg = ffmpeg_binary()
    result = subprocess.run(
        [ffmpeg, "-v", "info", "-i", str(path), "-af",
         f"silencedetect=noise={noise_db}dB:d={min_silence_s}", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return {"available": False, "segments": [], "gaps": [], "reason": (result.stderr or "")[-200:]}
    duration = None
    match = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", result.stderr)
    if match:
        duration = int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))
    silences: list[tuple[float, float]] = []
    pending = None
    for line in result.stderr.splitlines():
        start_match = re.search(r"silence_start: ([0-9.]+)", line)
        if start_match:
            pending = float(start_match.group(1))
        end_match = re.search(r"silence_end: ([0-9.]+)", line)
        if end_match and pending is not None:
            silences.append((pending, float(end_match.group(1))))
            pending = None
    if pending is not None and duration:
        silences.append((pending, duration))
    segments: list[tuple[float, float]] = []
    gaps: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in silences:
        if start > cursor + 0.05:
            segments.append((cursor, start))
        if end > start:
            gaps.append((start, end))
        cursor = max(cursor, end)
    if duration and duration > cursor + 0.05:
        segments.append((cursor, duration))
    return {"available": True, "duration_s": duration, "segments": segments, "gaps": gaps,
            "method": f"ffmpeg silencedetect(noise={noise_db}dB, d={min_silence_s}s)"}


def mix_tracks(
    *,
    voice_path: Path | None,
    music_path: Path | None,
    out_path: Path,
    duration_s: float,
    voice_gain_db: float = 0.0,
    music_gain_db: float = -18.0,
    ducking: dict | None = None,
    fade_in_s: float = 1.0,
    fade_out_s: float = 1.0,
    loop_music: bool = True,
    target_lufs: float = DEFAULT_TARGET_LUFS,
    true_peak_max_dbtp: float = DEFAULT_TRUE_PEAK_DBTP,
) -> dict:
    """真实混音：voice/music 独立轨 → ducking（旁白优先）→ 响度归一 → 输出。

    开关语义：`voice_path=None` 表示配音关闭（输出无旁白），`music_path=None` 表示 BGM 关闭；
    两者都为 None 时输出静音轨并如实说明。返回实测响度与判定。
    """
    if voice_path is None and music_path is None:
        raise ValueError("至少需要一条音轨（配音或 BGM）；两者都关闭时不应生成混音")
    ffmpeg = ffmpeg_binary()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    inputs: list[str] = []
    filters: list[str] = []
    mix_inputs: list[str] = []
    music_chain = ""
    next_input_index = 0
    if music_path is not None:
        # 循环在 demuxer 层完成（-stream_loop），避免 aloop 巨量缓冲导致 filter 注入失败
        if loop_music:
            inputs += ["-stream_loop", "-1"]
        inputs += ["-i", str(music_path)]
        music_index = next_input_index
        next_input_index += 1
        steps = [f"volume={music_gain_db}dB"]
        steps.append(f"atrim=0:{duration_s}")
        steps.append("asetpts=N/SR/TB")
        if fade_in_s > 0:
            steps.append(f"afade=t=in:st=0:d={fade_in_s}")
        if fade_out_s > 0 and duration_s > fade_out_s:
            steps.append(f"afade=t=out:st={max(0.0, duration_s - fade_out_s)}:d={fade_out_s}")
        steps.append("aformat=channel_layouts=stereo:sample_rates=48000")  # sidechaincompress 需要明确声道布局
        music_chain = f"[{music_index}:a]" + ",".join(steps) + "[music]"
        filters.append(music_chain)
    if voice_path is not None:
        inputs += ["-i", str(voice_path)]
        voice_index = next_input_index
        next_input_index += 1
        filters.append(
            f"[{voice_index}:a]volume={voice_gain_db}dB,apad=whole_dur={duration_s},"
            "aformat=channel_layouts=stereo:sample_rates=48000[voice]"
        )
    duck_report = None
    if music_path is not None and voice_path is not None:
        spec = ducking or {}
        if spec.get("enabled", True):
            # 检测阈值：调用方给定时按显式值；否则从**实测旁白电平**自动推导
            # （固定阈值在真实素材上不可靠：人声 RMS 常低于 -20 dBFS）。
            if spec.get("threshold_db") is None:
                voice_probe = probe_audio(voice_path)
                voice_level = measure_window_level(
                    voice_path, start_s=0.0, duration_s=max(0.2, voice_probe["duration_s"] or 1.0)
                )
                base = voice_level["mean_volume_db"]
                threshold_db = max(-45.0, min(-12.0, (base if base is not None else -23.0) - 6.0))
                threshold_source = "auto_from_voice_level"
                measured_voice_mean_db = base
            else:
                threshold_db = float(spec["threshold_db"])
                threshold_source = "explicit"
                measured_voice_mean_db = None
            # sidechaincompress 以线性幅度作阈值：dB → 幅度
            linear = 10 ** (threshold_db / 20.0)
            ratio = float(spec.get("ratio", 4.0))
            attack = float(spec.get("attack_ms", 20.0))
            release = float(spec.get("release_ms", 250.0))
            # 旁白需要同时作为 sidechain 与混音输入：先 asplit（同一 pad 不能被消费两次）
            filters.append("[voice]asplit=2[voice_sc][voice_mix]")
            filters.append(
                f"[music][voice_sc]sidechaincompress=threshold={linear:.6f}:ratio={ratio}:"
                f"attack={attack}:release={release}:makeup=1[ducked]"
            )
            mix_inputs = ["[ducked]", "[voice_mix]"]
            duck_report = {"enabled": True, "threshold_db": round(threshold_db, 2),
                           "threshold_source": threshold_source,
                           "voice_mean_level_db": measured_voice_mean_db,
                           "ratio": ratio, "attack_ms": attack, "release_ms": release,
                           "sidechain": "voice",
                           "measurement_note": "响度归一生效后绝对电平会被拉回目标；ducking 的可见效果是"
                                               "语音段音乐/旁白能量比下降，验收按该比值测量"}
        else:
            mix_inputs = ["[music]", "[voice]"]
            duck_report = {"enabled": False}
    elif music_path is not None:
        mix_inputs = ["[music]"]
        duck_report = {"enabled": False, "reason": "无配音轨，无需 ducking"}
    else:
        mix_inputs = ["[voice]"]
        duck_report = {"enabled": False, "reason": "无 BGM 轨"}
    filters.append(
        "".join(mix_inputs)
        + f"amix=inputs={len(mix_inputs)}:duration=longest:normalize=0,"
          "aformat=channel_layouts=stereo:sample_rates=48000[mixed]"
    )
    # 响度归一 + 真峰值限制（产品默认目标）
    filters.append(
        f"[mixed]loudnorm=I={target_lufs}:TP={true_peak_max_dbtp}:LRA=11:linear=true,"
        f"alimiter=limit={10 ** (true_peak_max_dbtp / 20.0):.6f}:level=disabled[out]"
    )
    command = [ffmpeg, "-y", "-v", "error", *inputs,
               "-filter_complex", ";".join(filters), "-map", "[out]",
               "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(out_path)]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0 or not out_path.exists():
        raise RuntimeError("混音失败: " + (result.stderr or "")[-500:])
    measurement = measure_loudness(out_path)
    return {
        "path": str(out_path),
        "duration_s": duration_s,
        "voice": {"path": str(voice_path) if voice_path else None, "gain_db": voice_gain_db,
                  "enabled": voice_path is not None},
        "music": {"path": str(music_path) if music_path else None, "gain_db": music_gain_db,
                  "enabled": music_path is not None, "loop": loop_music,
                  "fade_in_s": fade_in_s, "fade_out_s": fade_out_s},
        "ducking": duck_report,
        "measurement": measurement,
        "verdict": loudness_verdict(measurement),
        "command": command,
    }


def measure_window_level(path: Path, *, start_s: float, duration_s: float) -> dict:
    """测量某时间窗的音频电平（用于验证 ducking 是否真的压低了语音段落的音乐）。"""
    ffmpeg = ffmpeg_binary()
    result = subprocess.run(
        [ffmpeg, "-v", "info", "-ss", str(max(0.0, start_s)), "-t", str(max(0.05, duration_s)),
         "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    text = result.stderr
    mean = re.search(r"mean_volume:\s*(-?\d+\.\d+) dB", text)
    peak = re.search(r"max_volume:\s*(-?\d+\.\d+) dB", text)
    return {
        "start_s": start_s, "duration_s": duration_s,
        "mean_volume_db": float(mean.group(1)) if mean else None,
        "max_volume_db": float(peak.group(1)) if peak else None,
        "method": "ffmpeg volumedetect",
    }


def measure_window_band_level(
    path: Path, *, start_s: float, duration_s: float, centre_hz: float, bandwidth_hz: float = 60.0,
) -> dict:
    """时间窗 + 频段电平：用于在响度归一之后仍然能验证 ducking（同一文件内比较）。"""
    ffmpeg = ffmpeg_binary()
    result = subprocess.run(
        [ffmpeg, "-v", "info", "-ss", str(max(0.0, start_s)), "-t", str(max(0.05, duration_s)),
         "-i", str(path), "-af", f"bandpass=f={centre_hz}:width_type=h:w={bandwidth_hz},volumedetect",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    mean = re.search(r"mean_volume:\s*(-?\d+\.\d+) dB", result.stderr)
    return {
        "start_s": start_s, "duration_s": duration_s, "centre_hz": centre_hz,
        "mean_volume_db": float(mean.group(1)) if mean else None,
        "method": "ffmpeg bandpass+volumedetect(window)",
    }


def measure_band_level(path: Path, *, centre_hz: float, bandwidth_hz: float = 60.0) -> dict:
    """测量某个频段的电平（用于在响度归一后仍能区分音轨来源）。"""
    ffmpeg = ffmpeg_binary()
    low = max(20.0, centre_hz - bandwidth_hz / 2)
    high = centre_hz + bandwidth_hz / 2
    result = subprocess.run(
        [ffmpeg, "-v", "info", "-i", str(path),
         "-af", f"bandpass=f={centre_hz}:width_type=h:w={bandwidth_hz},volumedetect",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    text = result.stderr
    mean = re.search(r"mean_volume:\s*(-?\d+\.\d+) dB", text)
    return {
        "centre_hz": centre_hz, "band_hz": [low, high],
        "mean_volume_db": float(mean.group(1)) if mean else None,
        "method": "ffmpeg bandpass+volumedetect",
    }


# ---------------------------------------------------------------------------
# 时长适配（配音过长的三种显式策略）
# ---------------------------------------------------------------------------

CONTACT_ACTIONS = {"press_button", "single_punch_target"}


def adapt_plan_duration(
    plan: dict, *, audio_duration_s: float, policy: str, fps: int = 24,
    extend_max_frames: int = 0, rate: float = 1.0, rate_range: tuple[float, float] = (0.9, 1.1),
    profile_max_seconds: float | None = None, interaction_plan: dict | None = None,
) -> dict:
    """按主规划 12.4 的三种策略处理配音过长；人物接触镜头不得变速或延长其接触时序。

    - `extend_non_contact_shot`：只在 Profile 允许范围内延长非接触镜头（接触镜头保持不变）
    - `adjust_speech_rate`：在允许速率范围内调速（配音时长按速率缩放）
    - `rewrite_copy` / `fail_and_review`：不改视频，返回需要人工/重新生成
    """
    shots = plan.get("shots") or []
    total_frames = sum(int(shot.get("duration_frames") or 0) for shot in shots)
    video_seconds = total_frames / fps if fps else 0.0
    shortfall_frames = int(round((audio_duration_s - video_seconds) * fps))
    report = {
        "policy": policy,
        "video_seconds": round(video_seconds, 3),
        "audio_seconds": round(audio_duration_s, 3),
        "shortfall_frames": shortfall_frames,
        "shortfall_seconds": round(shortfall_frames / fps, 3) if fps else 0.0,
        "contact_shots_untouched": [],
        "changes": [],
        "result": "NO_CHANGE",
        "failures": [],
    }
    if shortfall_frames <= 0:
        report["result"] = "ALREADY_FITS"
        return report
    contact_shot_ids = set()
    if interaction_plan:
        action = str(interaction_plan.get("action") or "")
        if action in CONTACT_ACTIONS:
            for shot in shots:
                if interaction_plan.get("shot_id") and shot.get("id") == interaction_plan.get("shot_id"):
                    contact_shot_ids.add(shot["id"])
            if not contact_shot_ids and shots:
                contact_shot_ids.add(shots[0]["id"])
    if policy == "adjust_speech_rate":
        low, high = rate_range
        needed_rate = audio_duration_s / video_seconds if video_seconds else 1.0
        if needed_rate > high + 1e-9:
            report["failures"].append(
                f"所需速率 {needed_rate:.3f}× 超出允许范围 {low}–{high}×：不能靠加速配音凑时长"
            )
            report["result"] = "REJECTED"
            return report
        if needed_rate < low - 1e-9:
            needed_rate = low
        report["result"] = "SPEECH_RATE"
        report["new_rate"] = round(needed_rate, 4)
        report["rate_range"] = [low, high]
        report["measurement"] = "按实际配音时长反解速率，不改变视频帧数"
        return report
    if policy != "extend_non_contact_shot":
        report["result"] = "REQUIRES_REWRITE" if policy == "rewrite_copy" else "FAIL_AND_REVIEW"
        report["failures"].append(f"策略 {policy} 不自动改视频：需重新生成文案或人工复核")
        return report
    if extend_max_frames <= 0:
        report["failures"].append("未给出 extend_max_frames 上限")
        report["result"] = "REJECTED"
        return report
    if shortfall_frames > extend_max_frames:
        report["failures"].append(
            f"需要延长 {shortfall_frames} 帧，超过上限 {extend_max_frames} 帧"
        )
        report["result"] = "REJECTED"
        return report
    extendable = [
        index for index, shot in enumerate(shots)
        if str(shot.get("id")) not in contact_shot_ids
    ]
    if not extendable:
        report["failures"].append("没有可延长的非接触镜头（全部为人物接触镜头）")
        report["result"] = "REJECTED"
        return report
    share = shortfall_frames // len(extendable)
    remainder = shortfall_frames - share * len(extendable)
    new_shots = json.loads(json.dumps(shots))
    for order, index in enumerate(extendable):
        delta = share + (1 if order < remainder else 0)
        if delta:
            new_shots[index]["duration_frames"] = int(new_shots[index]["duration_frames"]) + delta
            report["changes"].append({
                "shot_id": new_shots[index]["id"], "added_frames": delta,
                "new_duration_frames": new_shots[index]["duration_frames"],
            })
    for shot in new_shots:
        if str(shot.get("id")) in contact_shot_ids:
            report["contact_shots_untouched"].append(shot["id"])
    new_total_frames = sum(int(shot["duration_frames"]) for shot in new_shots)
    new_seconds = new_total_frames / fps if fps else 0.0
    if profile_max_seconds is not None and new_seconds > profile_max_seconds + 1e-9:
        report["failures"].append(
            f"延长后 {new_seconds:.3f}s 超出 Profile 上限 {profile_max_seconds}s"
        )
        report["result"] = "REJECTED"
        return report
    adapted = json.loads(json.dumps(plan))
    adapted["shots"] = new_shots
    output = dict(adapted.get("output") or {})
    output["frame_count"] = new_total_frames
    output["duration_seconds"] = round(new_seconds, 3)
    adapted["output"] = output
    if adapted.get("from_reference"):
        report["note"] = "参考重演计划被延长：保留时长将与冻结映射不一致，需重新生成映射后再渲染"
    report["result"] = "EXTENDED"
    report["adapted_plan"] = adapted
    report["new_total_frames"] = new_total_frames
    report["new_duration_seconds"] = round(new_seconds, 3)
    return report


# ---------------------------------------------------------------------------
# 编码与封面
# ---------------------------------------------------------------------------


def _geometry_filter(src: dict, *, width: int, height: int, fps: int, crop_policy: str) -> tuple[str, dict]:
    """按 Profile 的 crop_policy 处理比例差异，绝不盲目拉伸（主规划 12.3 构图行）。"""
    src_w = int(src.get("width") or 0)
    src_h = int(src.get("height") or 0)
    same_aspect = bool(src_w and src_h) and abs((src_w / src_h) - (width / height)) <= 0.005 * (width / height)
    if same_aspect:
        return (f"scale={width}:{height}:flags=lanczos,fps={fps}",
                {"strategy": "direct_scale", "source_size": [src_w, src_h]})
    if crop_policy == "letterbox":
        return (f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,fps={fps}",
                {"strategy": "letterbox_pad", "source_size": [src_w, src_h],
                 "note": "比例不同：等比缩放后补边，不裁掉产品"})
    if crop_policy == "crop_if_safe":
        return (f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={width}:{height},fps={fps}",
                {"strategy": "center_crop", "source_size": [src_w, src_h],
                 "note": "按 crop_if_safe 居中裁切：导出前需人工确认主体仍在安全区内"})
    raise ValueError(
        f"crop_policy={crop_policy} 需要 3D 重构图，不能用 2D 缩放/裁切替代；请改渲染相机或改用 letterbox"
    )


def encode_rendition(
    *, video_path: Path, audio_path: Path | None, out_path: Path,
    width: int, height: int, fps: int, codec: str = "h264", container: str = "mp4",
    crf: int = 18, preset: str = "medium", crop_policy: str = "letterbox",
    source_size: dict | None = None,
) -> dict:
    """按 Profile 规格编码可播放成片（可选混音轨）；返回 ffprobe 复核结果。"""
    if not video_path.exists():
        raise FileNotFoundError(f"视频不存在: {video_path}")
    ffmpeg = ffmpeg_binary()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    probe_payload = source_size or {}
    if not probe_payload.get("width") or not probe_payload.get("height"):
        probe_payload = subprocess.run(
            [ffprobe_binary(), "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "json", str(video_path)],
            capture_output=True, text=True,
        )
        try:
            streams = json.loads(probe_payload.stdout).get("streams") or []
            probe_payload = streams[0] if streams else {}
        except (json.JSONDecodeError, AttributeError):
            probe_payload = {}
    geometry, geometry_report = _geometry_filter(probe_payload, width=width, height=height, fps=fps,
                                                 crop_policy=crop_policy)
    command = [ffmpeg, "-y", "-v", "error", "-i", str(video_path)]
    if audio_path is not None:
        command += ["-i", str(audio_path)]
    command += [
        "-vf", geometry,
        "-c:v", "libx264" if codec == "h264" else "libx265",
        "-crf", str(crf), "-preset", preset, "-pix_fmt", "yuv420p",
    ]
    if audio_path is not None:
        command += ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-shortest"]
    else:
        command += ["-map", "0:v:0", "-an"]
    command += ["-movflags", "+faststart", str(out_path)]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0 or not out_path.exists():
        raise RuntimeError("编码失败: " + (result.stderr or "")[-500:])
    probe = subprocess.run(
        [ffprobe_binary(), "-v", "error", "-show_entries",
         "stream=index,codec_type,codec_name,width,height,r_frame_rate,nb_frames:format=duration,format_name,size",
         "-of", "json", str(out_path)],
        capture_output=True, text=True,
    )
    try:
        info = json.loads(probe.stdout)
    except json.JSONDecodeError:
        info = {}
    streams = info.get("streams") or []
    video_stream = next((item for item in streams if item.get("codec_type") == "video"), {})
    has_audio = any(item.get("codec_type") == "audio" for item in streams)
    failures = []
    if int(video_stream.get("width") or 0) != width or int(video_stream.get("height") or 0) != height:
        failures.append(f"输出分辨率 {video_stream.get('width')}x{video_stream.get('height')} 与规格 {width}x{height} 不一致")
    if audio_path is not None and not has_audio:
        failures.append("请求带音频但输出没有音频流")
    if audio_path is None and has_audio:
        failures.append("请求无音频但输出包含音频流")
    return {
        "path": str(out_path), "container": container, "codec": video_stream.get("codec_name"),
        "width": video_stream.get("width"), "height": video_stream.get("height"),
        "fps": video_stream.get("r_frame_rate"), "nb_frames": video_stream.get("nb_frames"),
        "duration_s": round(float((info.get("format") or {}).get("duration") or 0), 3),
        "has_audio": has_audio, "size_bytes": out_path.stat().st_size,
        "passed": not failures, "failures": failures,
        "geometry": geometry_report,
        "note": "保留渲染 Master 与发布编码之间的来源链（lineage 由调用方落库）",
    }


def make_thumbnail(video_path: Path, out_path: Path, *, at_seconds: float = 0.0) -> dict:
    """封面帧（真实抽帧，不是占位图）。"""
    ffmpeg = ffmpeg_binary()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-ss", str(max(0.0, at_seconds)), "-i", str(video_path),
         "-frames:v", "1", "-q:v", "2", str(out_path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not out_path.exists():
        raise RuntimeError("封面抽帧失败: " + (result.stderr or "")[-300:])
    return {"path": str(out_path), "at_seconds": at_seconds, "size_bytes": out_path.stat().st_size}
