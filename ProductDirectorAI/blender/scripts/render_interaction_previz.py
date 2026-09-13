"""V4-02/03：交互预演渲染 + 接触几何 QA（真实 Blender 几何验证）。

职责：
- 复用 render_product 的产品导入/归一化/灯光/相机（不重复实现）；
- 按交互计划构建确定性人体 Proxy（躯干/头/手臂/手部标记），手部按
  interaction_validation 同一可达带模型运动到锚点表面并压入 4mm（<1cm 公差内）；
- 双通道输出：`previz/frame_XXXX.png`（产品 + Proxy，供界面预演）与
  `proxy_only/frame_XXXX.png`（仅 Proxy 透明底，供后续遮挡合成）；
- 真实几何接触 QA：手到锚点表面间隙 ≤ min(2cm, 锚点半径)、穿透 ≤1cm 且 ≤2 帧、
  实际接触帧与声明接触帧偏差 ≤2 帧——主规划 V4 阈值，逐帧写入 `contact_qa.json`。

用法（云端）：
    blender -b -P blender/scripts/render_interaction_previz.py -- \
      --input <product.glb> --plan <plan.json> --interaction <interaction.json> \
      --output <dir> --width 540 --height 960
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

SCRIPT_DIR = str(Path(__file__).resolve().parent)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))

from productdirector_api import interaction_geometry, interaction_validation  # noqa: E402
import render_product as rp  # noqa: E402

CONTACT_TOLERANCE_M = 0.02
PENETRATION_TOLERANCE_M = 0.01
MAX_PENETRATION_FRAMES = 2
TIMING_TOLERANCE_FRAMES = 2
PRESS_DEPTH_M = 0.004  # Proxy 压入深度（确定性，< 1cm 公差）


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--interaction", required=True, help="交互计划 JSON（含 anchor/character/action/frames）")
    parser.add_argument("--output", required=True)
    parser.add_argument("--width", type=int, default=540)
    parser.add_argument("--height", type=int, default=960)
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1:])


def product_bbox(meshes) -> tuple[tuple, tuple]:
    corners = []
    for obj in meshes:
        for corner in obj.bound_box:
            corners.append(obj.matrix_world @ Vector(corner))
    min_v = Vector((min(v.x for v in corners), min(v.y for v in corners), min(v.z for v in corners)))
    max_v = Vector((max(v.x for v in corners), max(v.y for v in corners), max(v.z for v in corners)))
    return tuple(min_v), tuple(max_v - min_v)


def _primitive_material(name: str, color: tuple) -> "bpy.types.Material":
    material = bpy.data.materials.new(name)
    material.use_nodes = False
    material.diffuse_color = (*color, 1.0)
    return material


def build_proxy(height_m: float, materials: dict) -> dict:
    """确定性人体 Proxy：躯干圆柱 + 头球 + 手臂圆柱 + 手部标记球。"""
    shoulder_z = height_m * 0.82
    hip_z = height_m * 0.48
    head_z = height_m * 0.92

    bpy.ops.mesh.primitive_cylinder_add(radius=0.12, depth=max(0.05, shoulder_z - hip_z), location=(0, 0, (hip_z + shoulder_z) / 2))
    torso = bpy.context.object
    torso.name = "ProxyTorso"
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.09, location=(0, 0, head_z))
    head = bpy.context.object
    head.name = "ProxyHead"
    bpy.ops.mesh.primitive_cylinder_add(radius=0.035, depth=1.0, location=(0, 0, shoulder_z))
    arm = bpy.context.object
    arm.name = "ProxyArm"
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.025, location=(0, 0, shoulder_z))
    hand = bpy.context.object
    hand.name = "ProxyHand"
    for obj, key in ((torso, "torso"), (head, "head"), (arm, "arm"), (hand, "hand")):
        if obj.data.materials:
            obj.data.materials[0] = materials[key]
        else:
            obj.data.materials.append(materials[key])
    return {"torso": torso, "head": head, "arm": arm, "hand": hand, "shoulder_z": shoulder_z}


def position_proxy(proxy: dict, root: Vector, hand: Vector) -> None:
    """把 Proxy 放到人物根部并把手臂从肩指向手。"""
    proxy["torso"].location = root
    proxy["head"].location = root
    shoulder = root + Vector((0, 0, proxy["shoulder_z"]))
    direction = hand - shoulder
    length = max(0.05, direction.length)
    mid = shoulder + direction * 0.5
    proxy["arm"].location = mid
    proxy["arm"].rotation_mode = "QUATERNION"
    proxy["arm"].rotation_quaternion = direction.to_track_quat("Z", "Y")
    proxy["arm"].scale = (1, 1, length)
    proxy["hand"].location = hand


def hand_trajectory(interaction: dict, anchor_world: dict, character: dict) -> list[Vector]:
    prepare = int(interaction["prepare_frame"])
    contact = int(interaction["contact_frame"])
    end = int(interaction["end_frame"])
    reach_low, reach_high = interaction_validation.character_reach_band(character["height_range_m"])
    center = Vector(anchor_world["position_m"])
    normal = Vector(anchor_world["normal"])
    radius = float(anchor_world["radius_m"])
    start = Vector((center.x, center.y - 0.7 - radius, min(max(center.z, reach_low), reach_high)))
    approach = (center - start).normalized()
    surface = center - approach * radius
    press = surface - normal * PRESS_DEPTH_M
    frames = []
    for frame in range(1, end + 1):
        if frame <= prepare:
            hand = Vector(start)
        elif frame <= contact:
            hand = start.lerp(surface, (frame - prepare) / max(1, contact - prepare))
        elif frame <= contact + 1:
            hand = surface.lerp(press, 0.5)
        else:
            hand = Vector(press)
        frames.append(hand)
    return frames


def contact_qa(interaction: dict, anchor_world: dict, trajectory: list[Vector], bbox_min: tuple, bbox_size: tuple) -> dict:
    """真实几何接触 QA。

    - 接触：手到锚点表面间隙 = max(0, |hand − center| − radius)；声明接触帧及之后
      的保持窗口内间隙必须 ≤ min(2cm, 半径)（接触帧之前是接近过程，不算失败）。
    - 实际接触帧 = 全片第一次间隙进入阈值（与声明帧偏差 ≤2 帧）。
    - 穿透 = 手部中心严格进入产品包围盒体积后的深度（到最近面的距离）≤1cm 且 ≤2 帧。
    """
    center = Vector(anchor_world["position_m"])
    radius = float(anchor_world["radius_m"])
    threshold = min(CONTACT_TOLERANCE_M, radius)
    declared_contact = int(interaction["contact_frame"])
    actual_contact = None
    penetration_frames: list[int] = []
    max_penetration = 0.0
    hold_gaps: list[float] = []
    for index, hand in enumerate(trajectory):
        frame = index + 1
        gap = max(0.0, (hand - center).length - radius)
        if actual_contact is None and gap <= threshold:
            actual_contact = frame
        if frame >= declared_contact:
            hold_gaps.append(gap)
        # 穿透：手部中心严格在产品包围盒体积内 → 到最近面的深度
        inside = all(
            bbox_min[axis] + 1e-9 <= hand[axis] <= bbox_min[axis] + bbox_size[axis] - 1e-9
            for axis in range(3)
        )
        depth = 0.0
        if inside:
            depth = min(
                min(hand[axis] - bbox_min[axis] for axis in range(3)),
                min(bbox_min[axis] + bbox_size[axis] - hand[axis] for axis in range(3)),
            )
        max_penetration = max(max_penetration, depth)
        if depth > PENETRATION_TOLERANCE_M:
            penetration_frames.append(frame)
    failures: list[str] = []
    if actual_contact is None:
        failures.append(f"全片未出现接触（手到锚点表面最小间隙超过阈值 {threshold:.4f}m）")
    else:
        deviation = abs(actual_contact - declared_contact)
        if deviation > TIMING_TOLERANCE_FRAMES:
            failures.append(f"实际接触帧 {actual_contact} 与声明接触帧 {declared_contact} 偏差 {deviation} 帧超过 ±{TIMING_TOLERANCE_FRAMES}")
    hold_max = max(hold_gaps) if hold_gaps else float("inf")
    if hold_max > threshold:
        failures.append(f"接触保持窗口最大间隙 {hold_max:.4f}m 超过阈值 {threshold:.4f}m（接触帧 {declared_contact} 起）")
    if max_penetration > PENETRATION_TOLERANCE_M:
        failures.append(f"穿透 {max_penetration:.4f}m 超过 {PENETRATION_TOLERANCE_M}m（帧: {penetration_frames[:8]}）")
    return {
        "schema_version": "1.0",
        "passed": not failures,
        "failures": failures,
        "anchor_center_m": [round(v, 4) for v in center],
        "anchor_radius_m": round(radius, 4),
        "contact_threshold_m": round(threshold, 4),
        "declared_contact_frame": declared_contact,
        "actual_contact_frame": actual_contact,
        "hold_window_max_gap_m": round(hold_max, 4) if hold_gaps else None,
        "max_penetration_m": round(max_penetration, 4),
        "penetration_frames": penetration_frames[:12],
    }


def main():
    args = parse_args()
    output = Path(args.output)
    (output / "previz").mkdir(parents=True, exist_ok=True)
    (output / "proxy_only").mkdir(parents=True, exist_ok=True)
    interaction = json.loads(Path(args.interaction).read_text(encoding="utf-8"))
    plan_payload = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    total_frames = int(
        plan_payload.get("output", {}).get("frame_count", 0)
        or sum(int(shot["duration_frames"]) for shot in plan_payload.get("shots", []))
    )
    shots = rp.load_plan(args.plan, total_frames)

    rp.clear_scene()
    meshes = rp.import_product(args.input)
    rp.normalize(meshes, {})
    rp.add_lighting({})
    bbox_min, bbox_size = product_bbox(meshes)
    anchor_world = interaction_geometry.anchor_to_world(interaction["anchor"], bbox_min, bbox_size)
    character = interaction["character"]
    trajectory = hand_trajectory(interaction, anchor_world, character)

    materials = {
        "torso": _primitive_material("ProxyTorsoMat", (0.90, 0.35, 0.10)),
        "head": _primitive_material("ProxyHeadMat", (0.95, 0.45, 0.15)),
        "arm": _primitive_material("ProxyArmMat", (0.80, 0.30, 0.08)),
        "hand": _primitive_material("ProxyHandMat", (0.10, 0.80, 0.30)),
    }
    proxy = build_proxy(character["height_range_m"][0], materials)
    center = Vector(anchor_world["position_m"])
    root = Vector((center.x, center.y - 0.7 - float(anchor_world["radius_m"]), 0))

    camera_data = bpy.data.cameras.new("Previz Camera")
    camera = bpy.data.objects.new("Previz Camera", camera_data)
    bpy.context.collection.objects.link(camera)
    bpy.context.scene.camera = camera
    rp.animate_camera(camera, shots)
    scene = bpy.context.scene
    scene.frame_start = 1
    scene.frame_end = max(int(interaction["end_frame"]), sum(s["duration_frames"] for s in shots))
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = args.width
    scene.render.resolution_y = args.height
    scene.render.resolution_percentage = 100
    scene.render.fps = 24
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "8"
    scene.render.image_settings.compression = 40

    qa_report = contact_qa(interaction, anchor_world, trajectory, bbox_min, bbox_size)
    for index, hand in enumerate(trajectory):
        frame = index + 1
        scene.frame_set(frame)
        position_proxy(proxy, root, hand)
        scene.render.film_transparent = False
        scene.render.filepath = str(output / "previz" / f"frame_{frame:04d}")
        bpy.ops.render.render(write_still=True)
        for obj in meshes:
            obj.hide_render = True
        scene.render.film_transparent = True
        scene.render.filepath = str(output / "proxy_only" / f"frame_{frame:04d}")
        bpy.ops.render.render(write_still=True)
        for obj in meshes:
            obj.hide_render = False

    (output / "contact_qa.json").write_text(json.dumps(qa_report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "trajectory.json").write_text(json.dumps({
        "schema_version": "1.0",
        "anchor_world": anchor_world,
        "bbox_min": [round(v, 4) for v in bbox_min],
        "bbox_size": [round(v, 4) for v in bbox_size],
        "frames": [{"frame": i + 1, "hand_m": [round(v, 4) for v in hand]} for i, hand in enumerate(trajectory)],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("INTERACTION_QA " + json.dumps(qa_report, ensure_ascii=False))
    print(f"INTERACTION_FRAMES previz={len(list((output / 'previz').glob('frame_*.png')))} proxy_only={len(list((output / 'proxy_only').glob('frame_*.png')))}")
    raise SystemExit(0 if qa_report["passed"] else 1)


if __name__ == "__main__":
    main()
