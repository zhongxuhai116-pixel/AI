"""V6-02 本地化文案、字幕生成/对齐与离线 TTS 适配。

诚实边界（对应主规划 12.4 / 12.6）：
- 产品事实（名称/尺寸/功能/适龄等）与生成式宣传语**分离**输出；自动文案不得添加
  未经证实的健康/性能宣传（按 Profile 的 prohibited_claims 拦截）。
- 字幕对齐分两种来源并如实标注：`voice_audio`（基于实际配音音频的静音切分）与
  `timeline_proportional`（无配音时按时间线比例分配）。后者**不得**被称为语音精确同步。
- TTS 走能力接口：本机可用 espeak-ng（离线、开源、预览用音色）时如实标注引擎与音色；
  不可用时返回 NOT_CONFIGURED，允许上传授权配音，绝不伪造音频。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Iterable

LOCALES = ("es-MX", "en-US")
DEFAULT_MAX_CHARS_PER_LINE = 42
DEFAULT_MIN_CUE_SECONDS = 0.8
DEFAULT_MAX_CUE_SECONDS = 7.0

# 生成式宣传语模板：只描述外观/结构/使用场景，不包含健康、性能或认证承诺。
_TEMPLATES = {
    "es-MX": {
        "headline": "{name} · {feature}",
        "body": "{name} con {feature}. Diseño {style_word} para {use_case}.",
        "cta": "Descubre más detalles del producto.",
        "style_words": ["compacto", "moderno", "práctico"],
        "use_cases": ["el uso diario", "la oficina en casa", "los viajes cortos"],
        "feature_fallbacks": ["acabado mate", "líneas limpias", "construcción resistente"],
    },
    "en-US": {
        "headline": "{name} · {feature}",
        "body": "{name} with {feature}. A {style_word} design for {use_case}.",
        "cta": "See more product details.",
        "style_words": ["compact", "modern", "practical"],
        "use_cases": ["everyday use", "a home office", "short trips"],
        "feature_fallbacks": ["a matte finish", "clean lines", "sturdy construction"],
    },
}

# 关键词拦截：与 Profile 的 prohibited_claims 类别对应（不替代人工审核）。
_CLAIM_PATTERNS = [
    (r"(?i)\b(cura|cura\s+el|trata\s+el|elimina\s+el\s+dolor|medicinal|terap[eé]utic\w*)\b", "未经证实的健康/医疗功效"),
    (r"(?i)\b(100\s*%|garantizad\w*|infalible|milagros\w*)\b", "未经证实的性能或安全承诺"),
    (r"(?i)\b(certificad\w*\s+por|premiad\w*|aprobad\w*\s+por\s+la\s+FDA|FDA\s+approved)\b", "伪造认证、奖项或用户评价"),
    (r"(?i)\b(oferta\s+limitada|solo\s+hoy|últimas\s+unidades|descuento\s+exclusivo)\b", "虚假折扣或限时紧迫感"),
    (r"(?i)\b(fda|ce\s+marked|certified|guaranteed|risk[-\s]?free|clinically\s+proven)\b", "伪造认证、奖项或用户评价"),
]


def find_prohibited_claims(text: str, prohibited_claims: Iterable[str] | None = None) -> list[dict]:
    """返回命中的可疑宣传语（类别 + 片段 + 位置）。空列表表示未命中关键词。"""
    hits: list[dict] = []
    categories = set(prohibited_claims or [])
    for pattern, category in _CLAIM_PATTERNS:
        if categories and category not in categories:
            continue
        for match in re.finditer(pattern, text or ""):
            hits.append({"category": category, "match": match.group(0), "span": list(match.span())})
    return hits


def validate_hashtags(hashtags: list[str], *, max_count: int) -> list[dict]:
    """话题标签格式/数量/重复/字符校验。"""
    problems: list[dict] = []
    if len(hashtags) > max_count:
        problems.append({"code": "hashtag_count", "message": f"标签数 {len(hashtags)} 超过上限 {max_count}"})
    seen: set[str] = set()
    for tag in hashtags:
        body = tag.lstrip("#")
        if not re.fullmatch(r"[0-9A-Za-zÁÉÍÓÚÜÑáéíóúüñ_]{2,50}", body or ""):
            problems.append({"code": "hashtag_format", "message": f"标签格式不合法: {tag!r}"})
        lowered = body.lower()
        if lowered in seen:
            problems.append({"code": "hashtag_duplicate", "message": f"标签重复: {tag}"})
        seen.add(lowered)
    return problems


def build_copy(
    *,
    locale: str,
    product_name: str,
    facts: dict | None = None,
    style_index: int = 0,
    hashtag_max_count: int = 5,
) -> dict:
    """确定性文案生成：事实字段与生成式宣传语严格分离。"""
    if locale not in _TEMPLATES:
        raise ValueError(f"暂不支持的语言: {locale}（可用: {sorted(_TEMPLATES)}）")
    template = _TEMPLATES[locale]
    facts = dict(facts or {})
    feature = (
        facts.get("feature")
        or (facts.get("material") and f"{facts['material']}")
        or template["feature_fallbacks"][style_index % len(template["feature_fallbacks"])]
    )
    style_word = template["style_words"][style_index % len(template["style_words"])]
    use_case = template["use_cases"][style_index % len(template["use_cases"])]
    headline = template["headline"].format(name=product_name, feature=feature)
    body = template["body"].format(name=product_name, feature=feature, style_word=style_word, use_case=use_case)
    tags = ["#Producto", "#Diseno", "#UsoDiario"] if locale == "es-MX" else ["#Product", "#Design", "#EverydayUse"]
    return {
        "locale": locale,
        "headline": headline,
        "body": body,
        "cta": template["cta"],
        "hashtags": tags[: max(0, hashtag_max_count)],
        "facts": facts,               # 事实字段（来自批准规格），人工可核对
        "generated": {                # 生成式内容，单独标注
            "headline": True, "body": True, "cta": True,
            "template_locale": locale, "style_index": style_index,
        },
        "claims_scan": find_prohibited_claims(f"{headline}\n{body}"),
        "generator": "productdirector.local-copy.v1（本地确定性模板，不调用付费 API）",
    }


# ---------------------------------------------------------------------------
# 字幕：时间轴与文件
# ---------------------------------------------------------------------------


def _format_timestamp(seconds: float, *, comma: bool) -> str:
    seconds = max(0.0, float(seconds))
    millis = int(round(seconds * 1000))
    hours, millis = divmod(millis, 3600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    separator = "," if comma else "."
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{millis:03d}"


def milliseconds_to_timestamp(millis: int, *, comma: bool) -> str:
    return _format_timestamp(millis / 1000.0, comma=comma)


def wrap_text(text: str, *, max_chars_per_line: int, max_lines: int) -> list[str]:
    """按词换行，行数不超过 max_lines；超长时在最后一行合并（由校验器报告超限）。"""
    words = (text or "").split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars_per_line or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        head = lines[: max_lines - 1]
        head.append(" ".join(lines[max_lines - 1:]))
        lines = head
    return lines


def split_copy_into_chunks(text: str, *, max_chars: int = 84) -> list[str]:
    """按句子/逗号切分为字幕条目（保序、不丢字）。"""
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return []
    parts = re.split(r"(?<=[.!?;:])\s+", text)
    chunks: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if len(part) <= max_chars:
            chunks.append(part)
            continue
        buffer = ""
        for piece in re.split(r"(?<=,)\s+", part):
            candidate = f"{buffer} {piece}".strip()
            if len(candidate) <= max_chars or not buffer:
                buffer = candidate
            else:
                chunks.append(buffer)
                buffer = piece
        if buffer:
            chunks.append(buffer)
    return chunks


def cues_from_timeline(
    chunks: list[str], *, duration_seconds: float, max_chars_per_line: int = DEFAULT_MAX_CHARS_PER_LINE,
    max_lines: int = 2, start_offset: float = 0.0,
) -> list[dict]:
    """无配音时按时间线比例分配（诚实标注为 timeline_proportional）。"""
    if not chunks:
        return []
    span = max(0.0, duration_seconds - start_offset)
    weights = [max(1, len(chunk)) for chunk in chunks]
    total_weight = sum(weights)
    cues = []
    cursor = start_offset
    for index, chunk in enumerate(chunks):
        share = span * weights[index] / total_weight
        cues.append({
            "index": index + 1,
            "start_s": round(cursor, 3),
            "end_s": round(min(duration_seconds, cursor + share), 3),
            "text": chunk,
            "lines": wrap_text(chunk, max_chars_per_line=max_chars_per_line, max_lines=max_lines),
        })
        cursor += share
    return cues


def detect_speech_segments(audio_path: Path, *, noise_db: float = -35.0, min_silence_s: float = 0.25) -> dict:
    """用 ffmpeg silencedetect 找出实际语音段（配音对齐的真实依据）。"""
    ffmpeg = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"
    result = subprocess.run(
        [ffmpeg, "-v", "info", "-i", str(audio_path), "-af",
         f"silencedetect=noise={noise_db}dB:d={min_silence_s}", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return {"available": False, "reason": (result.stderr or "")[-300:], "segments": []}
    duration = None
    silences: list[tuple[float, float]] = []
    pending_start = None
    for line in result.stderr.splitlines():
        match = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", line)
        if match:
            hours, minutes, seconds = match.groups()
            duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        start_match = re.search(r"silence_start: ([0-9.]+)", line)
        if start_match:
            pending_start = float(start_match.group(1))
        end_match = re.search(r"silence_end: ([0-9.]+)", line)
        if end_match and pending_start is not None:
            silences.append((pending_start, float(end_match.group(1))))
            pending_start = None
    if pending_start is not None and duration:
        silences.append((pending_start, duration))
    segments: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in silences:
        if start > cursor + 0.05:
            segments.append((cursor, start))
        cursor = max(cursor, end)
    if duration and duration > cursor + 0.05:
        segments.append((cursor, duration))
    return {
        "available": True,
        "duration_s": duration,
        "silences": silences,
        "segments": segments,
        "method": f"ffmpeg silencedetect(noise={noise_db}dB, d={min_silence_s}s)",
    }


def cues_from_speech(
    chunks: list[str], speech: dict, *, duration_seconds: float,
    max_chars_per_line: int = DEFAULT_MAX_CHARS_PER_LINE, max_lines: int = 2,
    min_cue_seconds: float = DEFAULT_MIN_CUE_SECONDS, min_gap_seconds: float = 0.08,
) -> tuple[list[dict], dict]:
    """把文案条目对齐到实际语音段；语音段不足或音频不可用时回退并如实说明。

    短句（真实语音本身短于最小显示时长）按字幕惯例**延长显示**到最小可读时长，
    但不得越过下一条开始时间或视频结尾；延长情况在返回报告中如实记录。
    不在这里裁剪到视频长度：配音长于视频属于需要适配策略的情形，由调用方显式报告。
    """
    segments = [tuple(item) for item in (speech.get("segments") or [])]
    if not speech.get("available") or not segments:
        return [], {"status": "FALLBACK", "reason": speech.get("reason") or "未检测到语音段"}
    if len(segments) < len(chunks):
        return [], {
            "status": "FALLBACK",
            "reason": f"语音段数 {len(segments)} 少于文案条目 {len(chunks)}：无法逐条对齐",
        }
    cues = []
    padded: list[dict] = []
    for index, chunk in enumerate(chunks):
        start, end = segments[index]
        display_end = end
        if display_end - start < min_cue_seconds:
            next_start = segments[index + 1][0] if index + 1 < len(segments) else None
            limit = min(
                [value for value in (next_start - min_gap_seconds if next_start else None,
                                     duration_seconds if duration_seconds else None) if value is not None]
                or [start + min_cue_seconds]
            )
            display_end = min(start + min_cue_seconds, max(end, limit))
            padded.append({"cue": index + 1, "speech_seconds": round(end - start, 3),
                           "display_seconds": round(display_end - start, 3)})
        cues.append({
            "index": index + 1,
            "start_s": round(start, 3),
            "end_s": round(display_end, 3),
            "text": chunk,
            "lines": wrap_text(chunk, max_chars_per_line=max_chars_per_line, max_lines=max_lines),
        })
    return cues, {
        "status": "ALIGNED",
        "method": speech.get("method"),
        "speech_segments": len(segments),
        "cue_count": len(cues),
        "padded_short_cues": padded,
    }


def validate_cues(
    cues: list[dict], *, video_duration_s: float, max_lines: int = 2,
    max_chars_per_line: int = DEFAULT_MAX_CHARS_PER_LINE,
    min_cue_seconds: float = DEFAULT_MIN_CUE_SECONDS,
) -> list[dict]:
    """字幕时间轴合法性：顺序、重叠、越界、过短、行数与行长。"""
    problems: list[dict] = []
    previous_end = 0.0
    for cue in cues:
        index = cue.get("index")
        start, end = float(cue.get("start_s") or 0), float(cue.get("end_s") or 0)
        if end <= start:
            problems.append({"code": "cue_time_invalid", "cue": index,
                             "message": f"第 {index} 条结束时间不晚于开始（{start}→{end}）"})
        if start < -1e-9:
            problems.append({"code": "cue_negative_start", "cue": index, "message": f"第 {index} 条开始时间为负"})
        if start + 1e-6 < previous_end:
            problems.append({"code": "cue_overlap", "cue": index,
                             "message": f"第 {index} 条与上一条重叠（{start} < {previous_end}）"})
        if video_duration_s and end > video_duration_s + 1e-6:
            problems.append({"code": "cue_beyond_video", "cue": index,
                             "message": f"第 {index} 条结束 {end:.3f}s 超出视频时长 {video_duration_s:.3f}s"})
        if end - start < min_cue_seconds - 1e-9:
            problems.append({"code": "cue_too_short", "cue": index,
                             "message": f"第 {index} 条时长 {end - start:.3f}s 短于下限 {min_cue_seconds}s"})
        lines = cue.get("lines") or wrap_text(cue.get("text", ""), max_chars_per_line=max_chars_per_line,
                                              max_lines=max_lines)
        if len(lines) > max_lines:
            problems.append({"code": "cue_too_many_lines", "cue": index,
                             "message": f"第 {index} 条行数 {len(lines)} 超过 {max_lines}"})
        longest = max((len(line) for line in lines), default=0)
        if longest > max_chars_per_line:
            problems.append({"code": "cue_line_too_long", "cue": index,
                             "message": f"第 {index} 条单行 {longest} 字符超过 {max_chars_per_line}"})
        previous_end = max(previous_end, end)
    return problems


def render_srt(cues: list[dict]) -> str:
    blocks = []
    for cue in cues:
        lines = cue.get("lines") or [cue.get("text", "")]
        blocks.append(
            f"{cue['index']}\n"
            f"{_format_timestamp(cue['start_s'], comma=True)} --> {_format_timestamp(cue['end_s'], comma=True)}\n"
            + "\n".join(lines)
        )
    return "\n\n".join(blocks) + "\n"


def render_vtt(cues: list[dict]) -> str:
    blocks = ["WEBVTT", ""]
    for cue in cues:
        lines = cue.get("lines") or [cue.get("text", "")]
        blocks.append(
            f"{_format_timestamp(cue['start_s'], comma=False)} --> {_format_timestamp(cue['end_s'], comma=False)}\n"
            + "\n".join(lines)
            + "\n"
        )
    return "\n".join(blocks)


def parse_srt(text: str) -> list[dict]:
    """最小 SRT 解析器（测试与人工修订回读用）。"""
    cues: list[dict] = []
    for block in re.split(r"\n\s*\n", (text or "").strip()):
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        match = re.match(
            r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})", lines[1],
        )
        if not match:
            continue
        start = int(match.group(1)) * 3600 + int(match.group(2)) * 60 + int(match.group(3)) + int(match.group(4)) / 1000
        end = int(match.group(5)) * 3600 + int(match.group(6)) * 60 + int(match.group(7)) + int(match.group(8)) / 1000
        cues.append({"index": int(lines[0]), "start_s": start, "end_s": end, "lines": lines[2:]})
    return cues


# ---------------------------------------------------------------------------
# TTS 能力接口（离线预览引擎；无引擎时 NOT_CONFIGURED，绝不伪造音频）
# ---------------------------------------------------------------------------

ESPEAK_VOICE_BY_LOCALE = {"es-MX": "es-419", "es-419": "es-419", "es-ES": "es", "en-US": "en-us"}


def tts_capability() -> dict:
    binary = shutil.which("espeak-ng")
    if not binary:
        return {
            "status": "NOT_CONFIGURED",
            "engine": None,
            "reason": "未安装任何本地 TTS 引擎；可上传授权配音或显式关闭配音（主规划 12.4）",
            "engines": [],
        }
    voices = subprocess.run([binary, "--voices=es"], capture_output=True, text=True).stdout
    return {
        "status": "AVAILABLE",
        "engine": "espeak-ng",
        "kind": "offline_preview",  # 预览用离线合成，不是商业音色
        "binary": binary,
        "locales": sorted(ESPEAK_VOICE_BY_LOCALE),
        "reason": "",
        "note": "离线开源合成引擎，用作试听与对齐证据；正式发行音色需接入已配置的 TTS Provider",
        "spanish_voices": [line.split()[-2] for line in voices.splitlines()[1:] if line.strip()],
    }


def apply_pronunciation(text: str, dictionary: list[dict] | None) -> tuple[str, list[dict]]:
    """应用发音词典（term → say_as），返回替换后文本与命中记录。"""
    applied: list[dict] = []
    output = text or ""
    for entry in dictionary or []:
        term = str(entry.get("term") or "").strip()
        say_as = str(entry.get("say_as") or entry.get("replacement") or "").strip()
        if not term or not say_as:
            continue
        pattern = re.compile(re.escape(term), re.IGNORECASE)
        if pattern.search(output):
            output = pattern.sub(say_as, output)
            applied.append({"term": term, "say_as": say_as})
    return output, applied


def synthesize(
    text: str, *, locale: str, out_path: Path, rate: float = 1.0,
    pronunciation_dictionary: list[dict] | None = None, voice_ref: str = "",
) -> dict:
    """用离线引擎合成配音；返回真实产物信息（不可用时抛 NOT_CONFIGURED）。"""
    capability = tts_capability()
    if capability["status"] != "AVAILABLE":
        raise RuntimeError(json.dumps({"code": "NOT_CONFIGURED", "detail": capability["reason"]}, ensure_ascii=False))
    if locale not in ESPEAK_VOICE_BY_LOCALE:
        raise RuntimeError(json.dumps(
            {"code": "LOCALE_UNSUPPORTED", "detail": f"引擎不支持语言 {locale}"}, ensure_ascii=False))
    spoken, applied = apply_pronunciation(text, pronunciation_dictionary)
    if not spoken.strip():
        raise RuntimeError(json.dumps({"code": "EMPTY_TEXT", "detail": "配音文本为空"}, ensure_ascii=False))
    voice = voice_ref or ESPEAK_VOICE_BY_LOCALE[locale]
    speed = max(80, min(450, int(round(175 * float(rate)))))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [capability["binary"], "-v", voice, "-s", str(speed), "-p", "50", "-w", str(out_path), spoken],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not out_path.exists():
        raise RuntimeError(json.dumps(
            {"code": "TTS_FAILED", "detail": (result.stderr or "")[-300:]}, ensure_ascii=False))
    probe = subprocess.run(
        [shutil.which("ffprobe") or "/usr/bin/ffprobe", "-v", "error", "-show_entries",
         "format=duration", "-of", "json", str(out_path)],
        capture_output=True, text=True,
    )
    try:
        duration = float(json.loads(probe.stdout)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError):
        duration = 0.0
    return {
        "engine": capability["engine"],
        "engine_kind": capability["kind"],
        "voice": voice,
        "speed": speed,
        "rate": rate,
        "path": str(out_path),
        "duration_s": round(duration, 3),
        "characters": len(spoken),
        "pronunciation_applied": applied,
        "spoken_text": spoken,
    }
