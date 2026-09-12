"""V2 AI 导演：把自然语言描述变成**合法、可编辑**的 DirectorPlan。

设计要点（对应 V2 验收"描述生成合法可编辑计划"与"所有真正入队计划必须通过
服务端校验"）：

1. 生成器只产出候选，**服务端校验与修复是必经步骤**；
2. 生成失败或字段非法时，最多修复两次（`MAX_REPAIRS`），修复会退回到模板；
3. 默认使用本地规则生成器（不调用收费 API）。若显式设置
   `PRODUCTDIRECTOR_DIRECTOR_PROVIDER=minimax` 且已保存凭证，才会走 LLM。
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from pydantic import ValidationError

MAX_REPAIRS = 2
CAMERAS = ("dolly_in", "side_track", "hero_orbit", "static")
# 三种镜头时长组合，均满足"每段 24–144 帧、合计 144 帧"。
DURATION_MIXES = ((24, 72, 48), (48, 48, 48), (36, 60, 48), (24, 96, 24), (60, 60, 24))

KEYWORD_CAMERAS = (
    (("环绕", "旋转", "转一圈", "360", "orbit"), ("dolly_in", "side_track", "hero_orbit")),
    (("细节", "特写", "纹理", "材质", "close", "macro"), ("static", "dolly_in", "side_track")),
    (("开场", "全景", "整体", "wide"), ("static", "side_track", "hero_orbit")),
    (("推近", "靠近", "zoom", "push"), ("dolly_in", "side_track", "static")),
)


@dataclass
class DirectorResult:
    plan: dict
    provider: str
    attempts: int
    repairs: list[str] = field(default_factory=list)
    rejected_candidates: int = 0

    def public(self) -> dict:
        return {
            "plan": self.plan,
            "provider": self.provider,
            "attempts": self.attempts,
            "repairs": self.repairs,
            "rejected_candidates": self.rejected_candidates,
        }


def _pick_cameras(intent: str, asset_kind: str) -> tuple[str, str, str]:
    text = intent.lower()
    for keywords, cameras in KEYWORD_CAMERAS:
        if any(keyword in text for keyword in keywords):
            chosen = cameras
            break
    else:
        chosen = ("dolly_in", "side_track", "hero_orbit")
    if asset_kind != "model":
        # 图片没有物理 3D 环绕，环绕位改用定格，避免过度声明。
        chosen = tuple("static" if camera == "hero_orbit" else camera for camera in chosen)
    return chosen  # type: ignore[return-value]


def _pick_durations(intent: str) -> tuple[int, int, int]:
    text = intent.lower()
    if any(keyword in text for keyword in ("细节", "特写", "close", "macro")):
        return (48, 48, 48)
    if any(keyword in text for keyword in ("环绕", "旋转", "orbit", "360")):
        return (24, 48, 72)
    return DURATION_MIXES[0]


def _focal_for(camera: str, index: int) -> int:
    if camera == "static":
        return 85 if index == 0 else 55
    if camera == "dolly_in":
        return 50
    if camera == "side_track":
        return 35
    return 55


def rule_based_plan(intent: str, asset_kind: str = "image", seed: int = 0) -> dict:
    """本地规则生成器：按关键词选择机位与时长组合。"""
    cameras = _pick_cameras(intent, asset_kind)
    durations = _pick_durations(intent)
    shots = []
    for index, (camera, duration) in enumerate(zip(cameras, durations), start=1):
        shots.append({
            "id": f"shot_{index:02d}",
            "name": ("正面推近", "侧向观察", "立体环绕展示")[index - 1] if asset_kind == "model"
            else ("正面推近", "侧向观察", "细节定格")[index - 1],
            "duration_frames": duration,
            "camera": camera,
            "focal_length_mm": _focal_for(camera, index - 1),
        })
    return {"intent": intent, "shots": shots}


def _repair(candidate: dict, intent: str, asset_kind: str, reason: str) -> tuple[dict, str]:
    """把非法候选修回合法形状：保留能用的镜头，其余退回模板。"""
    template = rule_based_plan(intent, asset_kind)
    shots = candidate.get("shots")
    repaired = {"intent": candidate.get("intent") or intent, "shots": []}
    if isinstance(shots, list) and len(shots) == 3:
        for index in range(3):
            shot = shots[index] if isinstance(shots[index], dict) else {}
            fallback = template["shots"][index]
            repaired["shots"].append({
                "id": shot.get("id") if isinstance(shot.get("id"), str) else fallback["id"],
                "name": shot.get("name") if isinstance(shot.get("name"), str) and shot.get("name") else fallback["name"],
                "duration_frames": shot.get("duration_frames") if isinstance(shot.get("duration_frames"), int) else fallback["duration_frames"],
                "camera": shot.get("camera") if shot.get("camera") in CAMERAS else fallback["camera"],
                "focal_length_mm": shot.get("focal_length_mm") if isinstance(shot.get("focal_length_mm"), int) else fallback["focal_length_mm"],
            })
        # 时长必须合计 144：按模板比例重分配，保持每段 ≥24。
        total = sum(shot["duration_frames"] for shot in repaired["shots"])
        if total != 144:
            for index, fallback in enumerate(template["shots"]):
                repaired["shots"][index]["duration_frames"] = fallback["duration_frames"]
    else:
        repaired["shots"] = template["shots"]
    return repaired, reason


def validate_candidate(candidate: dict, validator) -> str | None:
    """返回 None 表示合法；否则返回可读原因。"""
    try:
        validator.model_validate({
            "intent": candidate.get("intent"),
            "shots": candidate.get("shots"),
        })
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first.get("loc", ()))
        return f"{location}: {first.get('msg')}"
    except (TypeError, KeyError, AttributeError) as exc:
        return str(exc)
    return None


LLM_SYSTEM_PROMPT = (
    "你是产品广告导演。只输出 JSON：{\"intent\": string, \"shots\": [3 个镜头]}；"
    "每个镜头字段为 id, name, duration_frames(24-144), camera(dolly_in|side_track|hero_orbit|static), "
    "focal_length_mm(15-120)；三段时长合计必须为 144。"
)


def parse_llm_content(content: str) -> dict:
    """从模型回复里抽出 JSON 对象；抽不到就报错，由调用方修复。"""
    match = re.search(r"\{.*\}", content or "", re.S)
    if not match:
        raise ValueError("模型输出中没有 JSON")
    return json.loads(match.group(0))


def build_plan(intent: str, asset_kind: str = "image", validator=None, llm=None) -> DirectorResult:
    """生成 → 校验 → 最多修复两次，返回合法计划与过程记录。"""
    repairs: list[str] = []
    rejected = 0
    if llm is not None:
        producer = lambda: llm(intent, asset_kind)  # noqa: E731
        provider = "llm"
    else:
        producer = lambda: rule_based_plan(intent, asset_kind)  # noqa: E731
        provider = "rules"

    try:
        candidate = producer()
    except Exception as exc:  # Provider 失败直接退模板，不阻塞用户
        repairs.append(f"provider failed: {exc}")
        candidate = rule_based_plan(intent, asset_kind)
        provider = "rules"

    attempts = 1
    while True:
        problem = validate_candidate(candidate, validator) if validator is not None else None
        if problem is None:
            return DirectorResult(plan=candidate, provider=provider, attempts=attempts,
                                  repairs=repairs, rejected_candidates=rejected)
        rejected += 1
        if attempts > MAX_REPAIRS:
            candidate = rule_based_plan(intent, asset_kind)
            repairs.append(f"fallback to template after {attempts} attempts: {problem}")
            return DirectorResult(plan=candidate, provider="rules", attempts=attempts,
                                  repairs=repairs, rejected_candidates=rejected)
        candidate, reason = _repair(candidate, intent, asset_kind, problem)
        repairs.append(f"repair {attempts}: {reason}")
        attempts += 1


def provider_from_env() -> str:
    return os.getenv("PRODUCTDIRECTOR_DIRECTOR_PROVIDER", "rules").strip().lower()
