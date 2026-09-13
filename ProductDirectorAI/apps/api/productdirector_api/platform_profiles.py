"""V6-01 Platform Profile 合同：版本化导出配置、六个种子 Profile、安全区与规格校验。

设计原则（对应主规划 12.3）：
- Profile 是**生产与导出的版本化配置**；平台账号是发布身份，两者分开。选择某个
  Profile 不代表存在对应账号，也不保证触达某地区流量。
- 本模块只做**规格层**判定（结构、比例、安全区、时长/帧率/编码、许可引用完整性），
  不声称任何平台官方硬限制：未核验的硬限制必须带 source_url + verified_at，否则阻断。
- 规则来源单独记录；未核验/过期时如实标 NOT_VERIFIED，不把产品预设说成平台事实。
"""
from __future__ import annotations

import datetime as _dt
import json
from typing import Literal

from pydantic import BaseModel, Field, model_validator

SCHEMA_VERSION = "1.0"
# 规则核验有效期：超过该天数视为过期，需要重新核验（主规划 12.3「来源」行）。
RULES_MAX_AGE_DAYS = 180
# 本产品当前 9:16 渲染输出（V1–V5 基线）；1:1 / 2:3 目标需要构图策略而非盲目中心裁切。
PRODUCT_RENDER_OUTPUTS = ((540, 960), (1080, 1920))
# 本产品成片时长区间（主规划 V1：三镜头 5–8 秒 @24fps）。
PRODUCT_DURATION_RANGE_SECONDS = (5.0, 8.0)
SUPPORTED_ASPECTS = {"9:16": (9, 16), "1:1": (1, 1), "2:3": (2, 3), "16:9": (16, 9), "4:5": (4, 5)}
ALLOWED_CODECS = {"h264", "hevc"}
ALLOWED_CONTAINERS = {"mp4", "mov"}
ALLOWED_CROP_POLICIES = {"reframe_3d", "crop_if_safe", "letterbox"}
PLATFORMS = (
    "tiktok", "youtube", "instagram", "facebook_page", "facebook_ads", "marketplace", "pinterest",
)

Severity = Literal["BLOCKING", "WARNING", "INFO"]


class Region(BaseModel):
    """归一化区域（0–1，相对画面左上角）。"""

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    def contains(self, other: "Region", tolerance: float = 1e-6) -> bool:
        return (
            other.x >= self.x - tolerance and other.y >= self.y - tolerance
            and other.right <= self.right + tolerance and other.bottom <= self.bottom + tolerance
        )


class HardLimit(BaseModel):
    """平台硬限制条目：必须带来源与核验时间，否则视为未核验。"""

    name: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=200)
    source_url: str = ""
    verified_at: str = ""


class ProfileIdentity(BaseModel):
    profile_id: str = Field(min_length=1, max_length=80)
    version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=120)
    platform: Literal[
        "tiktok", "youtube", "instagram", "facebook_page", "facebook_ads", "marketplace", "pinterest",
    ]
    purpose: Literal["organic_post", "ads_material", "marketplace_listing"] = "organic_post"


class ProfileMarket(BaseModel):
    region: str = Field(min_length=2, max_length=8, pattern=r"^[A-Za-z]{2}(-[A-Za-z0-9]{1,3})?$")
    locale: str = Field(min_length=2, max_length=20, pattern=r"^[A-Za-z]{2}(-[A-Za-z0-9]{2,8})*$")
    timezone: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z]+/[A-Za-z_+\-/]+$")


class VideoSpec(BaseModel):
    width: int = Field(ge=64, le=8192)
    height: int = Field(ge=64, le=8192)
    fps: int = Field(ge=1, le=120)
    duration_min_seconds: float = Field(gt=0, le=3600)
    duration_max_seconds: float = Field(gt=0, le=3600)
    codec: Literal["h264", "hevc"]
    container: Literal["mp4", "mov"]
    bitrate_policy: str = Field(default="", max_length=200)
    hard_limits: list[HardLimit] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def validate_range(self):
        if self.duration_min_seconds > self.duration_max_seconds:
            raise ValueError("duration_min_seconds 不能大于 duration_max_seconds")
        return self


class CompositionSpec(BaseModel):
    aspect_ratio: Literal["9:16", "1:1", "2:3", "16:9", "4:5"]
    safe_area: Region
    subject_roi: Region
    crop_policy: Literal["reframe_3d", "crop_if_safe", "letterbox"]


class CopySpec(BaseModel):
    style: str = Field(default="", max_length=200)
    max_length: int = Field(ge=1, le=10000)
    length_algorithm: Literal["unicode_codepoints", "platform_specific"] = "unicode_codepoints"
    hashtag_policy: str = Field(default="", max_length=200)
    hashtag_max_count: int = Field(default=0, ge=0, le=100)
    cta: str = Field(default="", max_length=200)
    prohibited_claims: list[str] = Field(default_factory=list, max_length=50)


class SubtitleSpec(BaseModel):
    enabled: bool = True
    burn_in: bool = False
    sidecar_formats: list[Literal["srt", "vtt"]] = Field(default_factory=lambda: ["srt"], max_length=2)
    font_ref: str = ""
    font_license: str = ""
    size: int = Field(default=36, ge=8, le=200)
    position: Literal["bottom", "center", "top"] = "bottom"
    max_lines: int = Field(default=2, ge=1, le=6)


class VoiceSpec(BaseModel):
    enabled: bool = True
    locale: str = Field(default="", max_length=20)
    voice_ref: str = ""
    rate: float = Field(default=1.0, ge=0.5, le=2.0)
    pronunciation_dictionary: list[dict] = Field(default_factory=list, max_length=200)


class DuckingSpec(BaseModel):
    enabled: bool = True
    threshold_db: float = Field(default=-18.0, ge=-60.0, le=0.0)
    ratio: float = Field(default=4.0, ge=1.0, le=20.0)
    attack_ms: float = Field(default=20.0, ge=0.0, le=1000.0)
    release_ms: float = Field(default=250.0, ge=0.0, le=5000.0)


class MusicSpec(BaseModel):
    enabled: bool = False
    music_asset_id: str = ""
    license_ref: str = ""
    commercial_use_allowed: bool = False
    gain_db: float = Field(default=-18.0, ge=-60.0, le=12.0)
    ducking: DuckingSpec = Field(default_factory=DuckingSpec)


class MixSpec(BaseModel):
    target_loudness_lufs: float = Field(default=-16.0, ge=-40.0, le=-6.0)
    loudness_tolerance_lu: float = Field(default=1.5, gt=0, le=6.0)
    true_peak_max_dbtp: float = Field(default=-1.0, ge=-6.0, le=0.0)
    channels: Literal["stereo", "mono"] = "stereo"


class PublishDefaults(BaseModel):
    """只是建议；不代替平台要求的逐次用户选择。"""

    output_target: Literal["feed", "reels", "shorts", "page_video", "ads_material", "listing", "pin"] = "feed"
    suggested_visibility: Literal["public", "private", "unlisted", "draft"] = "private"
    disclosure_flags: list[str] = Field(default_factory=list, max_length=20)
    auto_publish_default: bool = False


class RulesSource(BaseModel):
    rules_source_url: str = ""
    rules_verified_at: str = ""
    rule_revision: str = ""


class PlatformProfileSpec(BaseModel):
    """一个不可变 Profile 版本的完整配置快照。"""

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    identity: ProfileIdentity
    market: ProfileMarket
    video: VideoSpec
    composition: CompositionSpec
    copy: CopySpec
    subtitles: SubtitleSpec
    voice: VoiceSpec
    music: MusicSpec
    mix: MixSpec = Field(default_factory=MixSpec)
    publish: PublishDefaults = Field(default_factory=PublishDefaults)
    rules: RulesSource = Field(default_factory=RulesSource)
    notes: str = Field(default="", max_length=2000)


def _problem(code: str, severity: Severity, message: str, field: str = "") -> dict:
    return {"code": code, "severity": severity, "message": message, "field": field}


def declared_aspect(video: VideoSpec) -> str | None:
    """宽高比最接近的声明比例（容差 0.5%）。"""
    ratio = video.width / video.height
    for name, (w, h) in SUPPORTED_ASPECTS.items():
        if abs(ratio - w / h) <= 0.005 * (w / h):
            return name
    return None


def _parse_date(value: str) -> _dt.date | None:
    try:
        return _dt.date.fromisoformat(value[:10])
    except (TypeError, ValueError):
        return None


def rules_status(spec: PlatformProfileSpec, today: _dt.date | None = None) -> dict:
    """规则来源状态：NOT_VERIFIED（未记录核验）/ STALE（超过有效期）/ CURRENT。"""
    today = today or _dt.date.today()
    verified = _parse_date(spec.rules.rules_verified_at)
    if not spec.rules.rules_source_url or verified is None:
        return {"status": "NOT_VERIFIED", "age_days": None, "max_age_days": RULES_MAX_AGE_DAYS}
    age = (today - verified).days
    return {
        "status": "CURRENT" if age <= RULES_MAX_AGE_DAYS else "STALE",
        "age_days": age,
        "max_age_days": RULES_MAX_AGE_DAYS,
    }


def validate_spec(spec: PlatformProfileSpec, *, today: _dt.date | None = None) -> list[dict]:
    """规格层校验：结构、比例、安全区、时长/帧率/编码、许可引用完整性。"""
    problems: list[dict] = []

    # 1) 比例与分辨率一致
    aspect = declared_aspect(spec.video)
    if aspect is None:
        problems.append(_problem(
            "unsupported_aspect", "BLOCKING",
            f"{spec.video.width}x{spec.video.height} 不属于支持比例 {sorted(SUPPORTED_ASPECTS)}",
            "video",
        ))
    elif aspect != spec.composition.aspect_ratio:
        problems.append(_problem(
            "aspect_mismatch", "BLOCKING",
            f"声明比例 {spec.composition.aspect_ratio} 与实际 {spec.video.width}x{spec.video.height}（{aspect}）不一致",
            "composition.aspect_ratio",
        ))

    # 2) 安全区与主体 ROI 必须落在画面内，主体必须落在安全区内（否则可能裁掉产品）
    for label, region in (("composition.safe_area", spec.composition.safe_area),
                          ("composition.subject_roi", spec.composition.subject_roi)):
        if region.right > 1 + 1e-6 or region.bottom > 1 + 1e-6:
            problems.append(_problem(
                "region_out_of_frame", "BLOCKING",
                f"{label} 超出画面（右 {region.right:.3f} / 下 {region.bottom:.3f}）", label,
            ))
    if not spec.composition.safe_area.contains(spec.composition.subject_roi):
        problems.append(_problem(
            "subject_outside_safe_area", "BLOCKING",
            "subject_roi 不在 safe_area 内：按该配置导出可能裁掉产品主体",
            "composition.subject_roi",
        ))
    if spec.composition.crop_policy == "crop_if_safe" and not spec.composition.safe_area.contains(spec.composition.subject_roi):
        problems.append(_problem(
            "crop_if_safe_without_margin", "WARNING",
            "crop_if_safe 策略下安全区未覆盖主体，导出前需人工确认构图",
            "composition.crop_policy",
        ))

    # 3) 时长 / 帧率 / 编码
    if spec.video.duration_min_seconds > spec.video.duration_max_seconds:
        problems.append(_problem("duration_range", "BLOCKING", "duration_min 大于 duration_max", "video"))
    product_min, product_max = PRODUCT_DURATION_RANGE_SECONDS
    product_fits = (
        spec.video.duration_min_seconds <= product_min and product_max <= spec.video.duration_max_seconds
    )
    if not product_fits:
        problems.append(_problem(
            "product_duration_out_of_range", "WARNING",
            f"本产品默认成片时长 {product_min}–{product_max} 秒不完整落在该 Profile 允许范围 "
            f"{spec.video.duration_min_seconds}–{spec.video.duration_max_seconds} 秒内",
            "video.duration_min_seconds",
        ))
    if spec.video.fps <= 0:
        problems.append(_problem("fps_invalid", "BLOCKING", "fps 必须为正", "video.fps"))
    elif spec.video.fps != 24:
        problems.append(_problem(
            "fps_not_pipeline_default", "WARNING",
            f"Profile fps={spec.video.fps} 与当前渲染基线 24fps 不同：需要转码或改帧率输出",
            "video.fps",
        ))
    if spec.video.codec not in ALLOWED_CODECS:
        problems.append(_problem("codec_unsupported", "BLOCKING", f"不支持的编码 {spec.video.codec}", "video.codec"))
    if spec.video.container not in ALLOWED_CONTAINERS:
        problems.append(_problem("container_unsupported", "BLOCKING", f"不支持的封装 {spec.video.container}", "video.container"))

    # 4) 硬限制必须可核验（未核验不得当作平台事实）
    for index, limit in enumerate(spec.video.hard_limits):
        if not limit.source_url or _parse_date(limit.verified_at) is None:
            problems.append(_problem(
                "hard_limit_unverified", "BLOCKING",
                f"硬限制「{limit.name}」缺少 source_url 或 verified_at，不能作为平台事实使用",
                f"video.hard_limits[{index}]",
            ))

    # 5) 规则来源
    status = rules_status(spec, today=today)
    if status["status"] == "NOT_VERIFIED":
        problems.append(_problem(
            "rules_not_verified", "WARNING",
            "未记录规则来源与核验时间：发布前必须按官方资料重新核验（V6-C 阻断条件）",
            "rules",
        ))
    elif status["status"] == "STALE":
        problems.append(_problem(
            "rules_stale", "WARNING",
            f"规则核验已 {status['age_days']} 天（上限 {RULES_MAX_AGE_DAYS} 天），需要重新核验",
            "rules.rules_verified_at",
        ))

    # 6) 文案
    if spec.copy.max_length <= 0:
        problems.append(_problem("copy_max_length", "BLOCKING", "copy.max_length 必须为正", "copy.max_length"))
    if spec.copy.length_algorithm == "platform_specific" and not spec.copy.style:
        problems.append(_problem(
            "copy_algorithm_undocumented", "WARNING",
            "声明按平台字符算法计数但未记录来源说明",
            "copy.length_algorithm",
        ))
    if not spec.copy.prohibited_claims:
        problems.append(_problem(
            "no_prohibited_claims", "WARNING",
            "未声明禁止宣传语清单：自动生成文案不得添加未经证实的健康/性能宣传",
            "copy.prohibited_claims",
        ))

    # 7) 字幕：SRT 必需，字体许可必须明确
    if spec.subtitles.enabled:
        if "srt" not in spec.subtitles.sidecar_formats:
            problems.append(_problem("subtitle_srt_required", "BLOCKING", "字幕开启时必须包含 SRT", "subtitles.sidecar_formats"))
        if not spec.subtitles.font_ref or not spec.subtitles.font_license:
            problems.append(_problem(
                "subtitle_font_license_missing", "BLOCKING",
                "字幕字体缺少 font_ref 或 font_license，不能打包分发",
                "subtitles.font_ref",
            ))

    # 8) 配音语言必须与市场语言一致（不一致要提示）
    if spec.voice.enabled:
        if not spec.voice.voice_ref:
            problems.append(_problem("voice_ref_missing", "BLOCKING", "配音开启但未指定 voice_ref", "voice.voice_ref"))
        if spec.voice.locale and spec.voice.locale.lower() != spec.market.locale.lower():
            problems.append(_problem(
                "voice_locale_mismatch", "WARNING",
                f"配音语言 {spec.voice.locale} 与市场语言 {spec.market.locale} 不一致：需要显式确认",
                "voice.locale",
            ))

    # 9) BGM：商业发布必须有明确许可
    if spec.music.enabled:
        if not spec.music.music_asset_id or not spec.music.license_ref:
            problems.append(_problem(
                "music_license_missing", "BLOCKING",
                "BGM 开启但缺少 music_asset_id 或 license_ref（未知商业许可不得默认用于商业发布）",
                "music.license_ref",
            ))
        commercial_targets = {"ads_material", "listing"}
        if not spec.music.commercial_use_allowed and spec.publish.output_target in commercial_targets:
            problems.append(_problem(
                "music_not_commercial", "BLOCKING",
                "BGM 未标注允许商业使用，但该 Profile 用于商业发布",
                "music.commercial_use_allowed",
            ))
    return problems


def output_compatibility(spec: PlatformProfileSpec, output: dict) -> list[dict]:
    """把**实际成片**规格与 Profile 规格对照（V6-05 打包前的真实校验）。"""
    problems: list[dict] = []
    width = int(output.get("width") or 0)
    height = int(output.get("height") or 0)
    fps = int(output.get("fps") or 0)
    frames = int(output.get("frame_count") or 0)
    duration = frames / fps if fps else float(output.get("duration_seconds") or 0)
    if not width or not height:
        problems.append(_problem("output_missing_size", "BLOCKING", "成片缺少宽高信息", "output"))
        return problems
    out_aspect = declared_aspect(VideoSpec(
        width=width, height=height, fps=24, duration_min_seconds=1, duration_max_seconds=10,
        codec="h264", container="mp4",
    ))
    if out_aspect != spec.composition.aspect_ratio:
        problems.append(_problem(
            "output_aspect_mismatch", "BLOCKING",
            f"成片比例 {out_aspect or f'{width}x{height}'} 与 Profile 要求 {spec.composition.aspect_ratio} 不同；"
            f"需按 crop_policy={spec.composition.crop_policy} 处理，不得盲目中心裁切",
            "output",
        ))
    if fps and fps != spec.video.fps:
        problems.append(_problem(
            "output_fps_mismatch", "WARNING",
            f"成片 {fps}fps 与 Profile {spec.video.fps}fps 不同：需要转码",
            "output",
        ))
    if duration and not (spec.video.duration_min_seconds <= duration <= spec.video.duration_max_seconds):
        problems.append(_problem(
            "output_duration_out_of_range", "BLOCKING",
            f"成片时长 {duration:.2f}s 不在 Profile 允许范围 "
            f"{spec.video.duration_min_seconds}–{spec.video.duration_max_seconds}s",
            "output",
        ))
    if width < spec.video.width or height < spec.video.height:
        problems.append(_problem(
            "output_resolution_below_spec", "WARNING",
            f"成片 {width}x{height} 低于 Profile 目标 {spec.video.width}x{spec.video.height}",
            "output",
        ))
    return problems


def summarize(problems: list[dict]) -> dict:
    blocking = [item for item in problems if item["severity"] == "BLOCKING"]
    warnings = [item for item in problems if item["severity"] == "WARNING"]
    return {
        "valid": not blocking,
        "blocking_count": len(blocking),
        "warning_count": len(warnings),
        "blocking": blocking,
        "warnings": warnings,
    }


def profile_payload_json(spec: PlatformProfileSpec) -> str:
    return json.dumps(spec.model_dump(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def spec_from_payload(payload: dict) -> PlatformProfileSpec:
    return PlatformProfileSpec.model_validate(payload)


# ---------------------------------------------------------------------------
# 六个首批导出 Profile（主规划 12.3）。都是**产品预设**：不代表账号存在、不代表平台
# 只支持该比例、也不代表存在官方发布 API。平台硬限制一律留空（未核验），规则来源
# 未记录 → 校验时如实报 rules_not_verified。
# ---------------------------------------------------------------------------
_SEED_NOTES = (
    "产品预设，非平台事实：硬限制未核验（hard_limits 为空），rules 未记录核验时间；"
    "文案上限为本产品默认策略值。发布前必须按官方资料重新核验（V6-C）。"
)
_COMMON_FONT = {"font_ref": "DejaVu Sans", "font_license": "Bitstream Vera Fonts License（可再分发）"}


def _seed(
    profile_id: str, name: str, platform: str, purpose: str, output_target: str,
    aspect: str, width: int, height: int, region: str, locale: str, timezone: str,
    duration_min: float, duration_max: float, safe_area: tuple[float, float, float, float],
    subject_roi: tuple[float, float, float, float], crop_policy: str,
    max_length: int, hashtag_max: int, visibility: str, extra_notes: str = "",
) -> dict:
    region_dict = lambda rect: {"x": rect[0], "y": rect[1], "width": rect[2], "height": rect[3]}  # noqa: E731
    return {
        "identity": {
            "profile_id": profile_id, "version": 1, "name": name,
            "platform": platform, "purpose": purpose,
        },
        "market": {"region": region, "locale": locale, "timezone": timezone},
        "video": {
            "width": width, "height": height, "fps": 24,
            "duration_min_seconds": duration_min, "duration_max_seconds": duration_max,
            "codec": "h264", "container": "mp4",
            "bitrate_policy": "产品默认（未核验平台码率上限）", "hard_limits": [],
        },
        "composition": {
            "aspect_ratio": aspect, "safe_area": region_dict(safe_area),
            "subject_roi": region_dict(subject_roi), "crop_policy": crop_policy,
        },
        "copy": {
            "style": "简洁产品说明；事实字段与生成式宣传语分离",
            "max_length": max_length, "length_algorithm": "unicode_codepoints",
            "hashtag_policy": f"建议标签不超过 {hashtag_max} 个，去重且格式校验",
            "hashtag_max_count": hashtag_max, "cta": "",
            "prohibited_claims": [
                "未经证实的健康/医疗功效", "未经证实的性能或安全承诺",
                "伪造认证、奖项或用户评价", "虚假折扣或限时紧迫感",
            ],
        },
        "subtitles": {
            "enabled": True, "burn_in": False, "sidecar_formats": ["srt", "vtt"],
            "size": 36, "position": "bottom", "max_lines": 2, **_COMMON_FONT,
        },
        "voice": {
            "enabled": False, "locale": "", "voice_ref": "", "rate": 1.0,
            "pronunciation_dictionary": [],
        },
        "music": {
            "enabled": False, "music_asset_id": "", "license_ref": "",
            "commercial_use_allowed": False, "gain_db": -18.0,
            "ducking": {"enabled": True, "threshold_db": -18.0, "ratio": 4.0, "attack_ms": 20.0, "release_ms": 250.0},
        },
        "mix": {
            "target_loudness_lufs": -16.0, "loudness_tolerance_lu": 1.5,
            "true_peak_max_dbtp": -1.0, "channels": "stereo",
        },
        "publish": {
            "output_target": output_target, "suggested_visibility": visibility,
            "disclosure_flags": [], "auto_publish_default": False,
        },
        "rules": {"rules_source_url": "", "rules_verified_at": "", "rule_revision": ""},
        "notes": _SEED_NOTES + ((" " + extra_notes) if extra_notes else ""),
    }


class PostproductionPresetSpec(BaseModel):
    """V6-02/03 的后期模板：字幕 / 配音 / BGM / 混音 + 配音过长时的显式适配策略。

    主规划 12.4：后期顺序固定（锁定剪辑与文案 → 配音 → 实际音频长度 → 允许的适配
    策略 → 锁定时间线 → 字幕对齐 → 混音 → 编码 → QA → 包）。配音过长只能在
    Profile 允许范围内延长非接触镜头、在允许速率内调速、或改文案重新生成；
    不得截断视频/旁白，也不得对人物接触镜头变速破坏 V4 时序。
    """

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    name: str = Field(min_length=1, max_length=120)
    locale: str = Field(default="", max_length=20)
    subtitles: SubtitleSpec
    voice: VoiceSpec
    music: MusicSpec
    mix: MixSpec = Field(default_factory=MixSpec)
    adaptation_policy: Literal[
        "extend_non_contact_shot", "adjust_speech_rate", "rewrite_copy", "fail_and_review",
    ] = "rewrite_copy"
    extend_max_frames: int = Field(default=0, ge=0, le=240)
    subprocess_order: list[str] = Field(
        default_factory=lambda: [
            "lock_edit_and_copy", "generate_voice", "measure_audio", "apply_adaptation",
            "lock_timeline", "align_subtitles", "mix_audio", "encode", "qa", "package",
        ],
        max_length=16,
    )
    notes: str = Field(default="", max_length=2000)


def validate_preset_spec(spec: PostproductionPresetSpec) -> list[dict]:
    """后期模板规格校验（与 Profile 一致的诚实边界）。"""
    problems: list[dict] = []
    if spec.subtitles.enabled:
        if "srt" not in spec.subtitles.sidecar_formats:
            problems.append(_problem("subtitle_srt_required", "BLOCKING", "字幕开启时必须包含 SRT", "subtitles.sidecar_formats"))
        if not spec.subtitles.font_ref or not spec.subtitles.font_license:
            problems.append(_problem(
                "subtitle_font_license_missing", "BLOCKING",
                "字幕字体缺少 font_ref 或 font_license，不能打包分发", "subtitles.font_ref",
            ))
    if spec.voice.enabled:
        if not spec.voice.voice_ref:
            problems.append(_problem("voice_ref_missing", "BLOCKING", "配音开启但未指定 voice_ref", "voice.voice_ref"))
        if not spec.locale:
            problems.append(_problem("voice_locale_missing", "BLOCKING", "配音开启但模板未声明 locale", "locale"))
    if spec.music.enabled:
        if not spec.music.music_asset_id or not spec.music.license_ref:
            problems.append(_problem(
                "music_license_missing", "BLOCKING",
                "BGM 开启但缺少素材或许可引用", "music.license_ref",
            ))
    if spec.adaptation_policy == "extend_non_contact_shot" and spec.extend_max_frames <= 0:
        problems.append(_problem(
            "extend_frames_missing", "BLOCKING",
            "选择延长非接触镜头策略时必须给出 extend_max_frames 上限", "extend_max_frames",
        ))
    if spec.adaptation_policy == "adjust_speech_rate" and spec.voice.enabled and not (0.5 <= spec.voice.rate <= 2.0):
        problems.append(_problem("speech_rate_out_of_range", "BLOCKING", "语速超出允许范围", "voice.rate"))
    if spec.mix.true_peak_max_dbtp > -1.0:
        problems.append(_problem(
            "true_peak_above_target", "WARNING",
            f"真峰值上限 {spec.mix.true_peak_max_dbtp} dBTP 高于产品默认 -1 dBTP", "mix.true_peak_max_dbtp",
        ))
    required_steps = {"lock_edit_and_copy", "generate_voice", "measure_audio", "lock_timeline", "align_subtitles", "mix_audio", "encode"}
    missing = sorted(required_steps - set(spec.subprocess_order))
    if missing:
        problems.append(_problem(
            "subprocess_order_incomplete", "BLOCKING",
            f"后期顺序缺少必需步骤 {missing}", "subprocess_order",
        ))
    return problems


SEED_PRESETS: list[dict] = [
    {
        "preset_key": "clean-9x16-nosub",
        "name": "干净竖版 · 无字幕无配音",
        "spec": {
            "name": "干净竖版 · 无字幕无配音", "locale": "",
            "subtitles": {"enabled": False, "burn_in": False, "sidecar_formats": ["srt"],
                          "font_ref": "", "font_license": "", "size": 36, "position": "bottom", "max_lines": 2},
            "voice": {"enabled": False, "locale": "", "voice_ref": "", "rate": 1.0, "pronunciation_dictionary": []},
            "music": {"enabled": False, "music_asset_id": "", "license_ref": "", "commercial_use_allowed": False,
                      "gain_db": -18.0, "ducking": {"enabled": True, "threshold_db": -18.0, "ratio": 4.0,
                                                    "attack_ms": 20.0, "release_ms": 250.0}},
            "mix": {"target_loudness_lufs": -16.0, "loudness_tolerance_lu": 1.5,
                    "true_peak_max_dbtp": -1.0, "channels": "stereo"},
            "adaptation_policy": "rewrite_copy", "extend_max_frames": 0,
            "notes": "无字幕/无配音基线模板；Manifest 必须标明字幕与配音为 disabled。",
        },
    },
    {
        "preset_key": "es-mx-subtitled",
        "name": "es-MX 字幕版 · 无配音",
        "spec": {
            "name": "es-MX 字幕版 · 无配音", "locale": "es-MX",
            "subtitles": {"enabled": True, "burn_in": False, "sidecar_formats": ["srt", "vtt"],
                          "font_ref": "DejaVu Sans", "font_license": "Bitstream Vera Fonts License（可再分发）",
                          "size": 36, "position": "bottom", "max_lines": 2},
            "voice": {"enabled": False, "locale": "es-MX", "voice_ref": "", "rate": 1.0, "pronunciation_dictionary": []},
            "music": {"enabled": False, "music_asset_id": "", "license_ref": "", "commercial_use_allowed": False,
                      "gain_db": -18.0, "ducking": {"enabled": True, "threshold_db": -18.0, "ratio": 4.0,
                                                    "attack_ms": 20.0, "release_ms": 250.0}},
            "mix": {"target_loudness_lufs": -16.0, "loudness_tolerance_lu": 1.5,
                    "true_peak_max_dbtp": -1.0, "channels": "stereo"},
            "adaptation_policy": "rewrite_copy", "extend_max_frames": 0,
            "notes": "es-MX 无配音路线：字幕侧车文件；配音未配置 Provider 时显式关闭。",
        },
    },
]


def preset_payload_json(spec: PostproductionPresetSpec) -> str:
    return json.dumps(spec.model_dump(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


SEED_PROFILES: list[dict] = [
    _seed(
        "tiktok-mx-9x16-esmx", "TikTok Mexico · 9:16 · es-MX", "tiktok", "organic_post", "feed",
        "9:16", 1080, 1920, "MX", "es-MX", "America/Mexico_City", 3.0, 60.0,
        (0.06, 0.10, 0.88, 0.74), (0.25, 0.25, 0.50, 0.45), "crop_if_safe", 2200, 8, "private",
        "首发市场 Profile；选择它不会创建或登录墨西哥账号，也不保证触达该地区流量。",
    ),
    _seed(
        "youtube-shorts-9x16-en", "YouTube Shorts · 9:16 · en", "youtube", "organic_post", "shorts",
        "9:16", 1080, 1920, "US", "en-US", "America/Los_Angeles", 3.0, 60.0,
        (0.06, 0.08, 0.88, 0.84), (0.25, 0.25, 0.50, 0.45), "crop_if_safe", 5000, 15, "private",
    ),
    _seed(
        "instagram-reels-9x16-en", "Instagram Reels · 9:16 · en", "instagram", "organic_post", "reels",
        "9:16", 1080, 1920, "US", "en-US", "America/Los_Angeles", 3.0, 90.0,
        (0.06, 0.12, 0.88, 0.72), (0.25, 0.25, 0.50, 0.45), "crop_if_safe", 2200, 10, "private",
        "实际可用性取决于账号类型与授权能力，需现场验证（V6-13）。",
    ),
    _seed(
        "facebook-ads-1x1-esmx", "Facebook Ads 素材 · 1:1 · es-MX", "facebook_ads", "ads_material", "ads_material",
        "1:1", 1080, 1080, "MX", "es-MX", "America/Mexico_City", 3.0, 60.0,
        (0.05, 0.05, 0.90, 0.90), (0.25, 0.25, 0.50, 0.50), "letterbox", 500, 5, "private",
        "仅产广告素材：V6 不创建广告系列、不花费广告预算；发布到页面与广告投放是两个业务动作。",
    ),
    _seed(
        "marketplace-1x1-esmx", "Marketplace 产品展示 · 1:1 · es-MX", "marketplace", "marketplace_listing", "listing",
        "1:1", 1080, 1080, "MX", "es-MX", "America/Mexico_City", 3.0, 60.0,
        (0.04, 0.04, 0.92, 0.92), (0.22, 0.22, 0.56, 0.56), "letterbox", 1000, 5, "private",
        "人工导入指引路线：不宣称拥有通用「一键发布」能力（V6-C）。",
    ),
    _seed(
        "pinterest-2x3-en", "Pinterest · 2:3 · en", "pinterest", "organic_post", "pin",
        "2:3", 1000, 1500, "US", "en-US", "America/Los_Angeles", 3.0, 60.0,
        (0.06, 0.06, 0.88, 0.88), (0.25, 0.28, 0.50, 0.50), "letterbox", 500, 8, "private",
        "原生连接器列为 V6 可选扩展；启用前需独立验收。",
    ),
]
