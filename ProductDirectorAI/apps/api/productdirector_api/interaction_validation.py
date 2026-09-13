"""V4-03：交互计划的纯数学校验引擎（接触距离 / 穿透 / 时序 / 能力）。

Proxy 模型（确定性、可单测；真实 IK 在 Blender Proxy 渲染侧另做）：
- 产品归一化约定沿用 V4-01：锚点 position_m 为 bbox 归一化比例，归一化包围盒
  最长边 = 1.45m；本引擎按"每轴尺寸 ≤ 最长边"做保守世界换算（如实记录）。
- 人物可达带：reach_low = 0.15 × 身高中值（弯腰可及低位锚点），
  reach_high = 1.15 × 身高中值（米）。
- 接触距离：|锚点高度 − clamp(锚点高度, reach_low, reach_high)|，阈值 = min(2cm, 锚点半径)。
- 穿透：距离误差超过锚点半径的部分视为手穿过产品表面；穿透帧数按
  [contact_frame, contact_frame+2] 窗口计，>2 帧或 >1cm 即失败。
- 时序：接触帧相对准备帧的偏移与动作模板接触事件偏移的差 ≤ 2 帧。

阈值来源：主规划 V4（接触距离≤2cm 或更严格锚点半径、偏差≤2帧、穿透≤1cm 且不持续超过2帧）。
"""
from __future__ import annotations

PRODUCT_LARGEST_DIM_M = 1.45
CONTACT_TOLERANCE_M = 0.02
PENETRATION_TOLERANCE_M = 0.01
MAX_PENETRATION_FRAMES = 2
TIMING_TOLERANCE_FRAMES = 2


def character_reach_band(height_range_m: list[float]) -> tuple[float, float]:
    """人物可达带（米）：(reach_low, reach_high)。低位锚点按弯腰可及建模（0.15×身高）。"""
    mid = (float(height_range_m[0]) + float(height_range_m[1])) / 2.0
    return mid * 0.15, mid * 1.15


def anchor_world_z(position_m: list[float]) -> float:
    """锚点高度保守世界换算：z 比例 × 归一化最长边 1.45m。"""
    return float(position_m[2]) * PRODUCT_LARGEST_DIM_M


def anchor_world_radius(radius_m: float) -> float:
    return float(radius_m) * PRODUCT_LARGEST_DIM_M


def validate_interaction(
    interaction: dict,
    anchors: list[dict],
    character: dict,
    template: dict,
    plan_frame_count: int,
) -> dict:
    """校验交互计划；anchors 为该计划绑定的已冻结锚点集内的锚点载荷。"""
    failures: list[str] = []
    metrics: dict = {"capabilities": {}, "timing": {}, "contact": {}, "penetration": {}}

    prepare = int(interaction["prepare_frame"])
    contact = int(interaction["contact_frame"])
    end = int(interaction["end_frame"])
    if not (1 <= prepare < contact <= end <= plan_frame_count):
        failures.append(
            f"帧范围无效：需 1 ≤ prepare({prepare}) < contact({contact}) ≤ end({end}) ≤ 全片帧数({plan_frame_count})"
        )
        return _report(failures, metrics, interaction, template, character)

    contact_offsets = [event["frame_offset"] for event in template.get("contact_events", [])]
    if contact_offsets:
        expected_offset = int(contact_offsets[0])
        deviation = abs((contact - prepare) - expected_offset)
        metrics["timing"] = {
            "template_contact_offset": expected_offset,
            "actual_offset": contact - prepare,
            "deviation_frames": deviation,
            "tolerance_frames": TIMING_TOLERANCE_FRAMES,
        }
        if deviation > TIMING_TOLERANCE_FRAMES:
            failures.append(f"接触时序偏差 {deviation} 帧超过 ±{TIMING_TOLERANCE_FRAMES} 帧")

    if template.get("requires_anchor"):
        if not anchors:
            failures.append(f"动作 {template['id']} 需要锚点，但交互计划未绑定任何锚点")
        else:
            anchor = anchors[0]
            action = interaction.get("action")
            if action not in anchor.get("allowed_actions", []):
                failures.append(f"锚点 {anchor.get('name')} 不允许动作 {action}")
            reach_low, reach_high = character_reach_band(character["height_range_m"])
            metrics["capabilities"] = {
                "character_height_mid_m": (character["height_range_m"][0] + character["height_range_m"][1]) / 2,
                "reach_low_m": round(reach_low, 4),
                "reach_high_m": round(reach_high, 4),
            }
            anchor_z = anchor_world_z(anchor["position_m"])
            radius = anchor_world_radius(anchor["radius_m"])
            distance = abs(anchor_z - min(max(anchor_z, reach_low), reach_high))
            threshold = min(CONTACT_TOLERANCE_M, radius)
            metrics["contact"] = {
                "anchor": anchor.get("name"),
                "anchor_z_m": round(anchor_z, 4),
                "anchor_radius_m": round(radius, 4),
                "contact_distance_m": round(distance, 4),
                "threshold_m": round(threshold, 4),
            }
            if distance > threshold:
                reason = "锚点超出人物可达范围" if anchor_z > reach_high else "锚点低于人物可达范围"
                failures.append(f"接触距离 {distance:.4f}m 超过阈值 {threshold:.4f}m（{reason}）")
            penetration = max(0.0, distance - radius)
            frames_penetrating = 0
            if penetration > PENETRATION_TOLERANCE_M:
                frames_penetrating = 1 + MAX_PENETRATION_FRAMES  # 确定性 Proxy 模型：窗口内持续穿透
            metrics["penetration"] = {
                "penetration_m": round(penetration, 4),
                "tolerance_m": PENETRATION_TOLERANCE_M,
                "frames_exceeding": frames_penetrating,
                "max_frames": MAX_PENETRATION_FRAMES,
            }
            if penetration > PENETRATION_TOLERANCE_M:
                failures.append(f"穿透 {penetration:.4f}m 超过 {PENETRATION_TOLERANCE_M}m 且持续超过 {MAX_PENETRATION_FRAMES} 帧")

    return _report(failures, metrics, interaction, template, character)


def _report(failures: list[str], metrics: dict, interaction: dict, template: dict, character: dict) -> dict:
    return {
        "schema_version": "1.0",
        "passed": not failures,
        "failures": failures,
        "metrics": metrics,
        "interaction": interaction,
        "motion_template": template["id"],
        "character": character.get("name"),
        "proxy_model": "确定性代理：可达带 0.15–1.15×身高；锚点高度按归一化最长边 1.45m 保守换算（每轴 ≤ 最长边）",
    }


def previz_timeline(interaction: dict, anchors: list[dict], character: dict) -> dict:
    """V4-02 事件时间线（数据版预演）：逐帧代理手部高度与事件，供 UI/后续 Blender Proxy 使用。"""
    prepare = int(interaction["prepare_frame"])
    contact = int(interaction["contact_frame"])
    end = int(interaction["end_frame"])
    reach_low, reach_high = character_reach_band(character["height_range_m"])
    anchor_z = anchor_world_z(anchors[0]["position_m"]) if anchors else reach_low
    target_z = min(max(anchor_z, reach_low), reach_high)
    frames = []
    for frame in range(1, int(end) + 1):
        event = None
        if frame == prepare:
            event = "prepare"
        elif frame == contact:
            event = "contact"
        elif frame == end:
            event = "end"
        if frame <= prepare:
            hand_z = reach_low
        elif frame <= contact:
            hand_z = reach_low + (target_z - reach_low) * (frame - prepare) / max(1, contact - prepare)
        else:
            hand_z = target_z
        frames.append({"frame": frame, "event": event, "hand_z_m": round(hand_z, 4)})
    return {
        "schema_version": "1.0",
        "interaction_plan_id": interaction.get("interaction_plan_id"),
        "action": interaction.get("action"),
        "events": [{"event": "prepare", "frame": prepare}, {"event": "contact", "frame": contact}, {"event": "end", "frame": end}],
        "frames": frames,
        "proxy_model": "确定性代理手部高度时间线；Blender 人体 Proxy 渲染在渲染侧接入",
    }
