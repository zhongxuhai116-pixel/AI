from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--width", type=int, default=540)
    parser.add_argument("--height", type=int, default=960)
    parser.add_argument("--frames", type=int, default=144)
    parser.add_argument("--plan", required=True, help="Frozen DirectorPlan JSON created for this render job")
    parser.add_argument("--passes", action="store_true",
                        help="V3：额外输出 Beauty/Alpha/Depth/Normal/Index 多通道（不改动既有单帧产物）")
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])


def clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)


def import_product(path: str):
    bpy.ops.import_scene.gltf(filepath=path)
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if not meshes:
        raise RuntimeError("GLB 中没有可渲染网格")
    return meshes


def normalize(meshes, pose: dict | None = None):
    corners = []
    for obj in meshes:
        corners.extend(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
    min_v = Vector((min(v.x for v in corners), min(v.y for v in corners), min(v.z for v in corners)))
    max_v = Vector((max(v.x for v in corners), max(v.y for v in corners), max(v.z for v in corners)))
    size = max_v - min_v
    largest = max(size.x, size.y, size.z)
    if largest <= 0:
        raise RuntimeError("产品模型包围盒无效")
    scale = 1.45 / largest
    center = (min_v + max_v) / 2
    for obj in meshes:
        obj.scale *= scale
        obj.location = (obj.location - center) * scale
        obj.location.z += size.z * scale / 2
    if pose:
        # 目标 3D 合同的 product_pose：在归一化底座上再叠加位置/旋转/缩放。
        offset = Vector(pose.get("position_m") or (0, 0, 0))
        rotation = pose.get("rotation_xyz_deg") or (0, 0, 0)
        extra_scale = float(pose.get("scale", 1) or 1)
        for obj in meshes:
            obj.location = Vector(obj.location) + offset
            obj.rotation_euler = tuple(math.radians(value) for value in rotation)
            obj.scale *= extra_scale
    bpy.context.view_layer.update()


def track(camera, target):
    direction = Vector(target) - camera.location
    camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def hex_to_rgb(value: str) -> tuple[float, float, float]:
    text = str(value).lstrip("#")
    return tuple(int(text[index:index + 2], 16) / 255 for index in (0, 2, 4))


def add_lighting(scene_spec: dict | None = None):
    spec = scene_spec or {}
    world = bpy.context.scene.world or bpy.data.worlds.new("World")
    bpy.context.scene.world = world
    world.color = hex_to_rgb(spec.get("background_color") or "#0A0A0C")
    preset = spec.get("lighting_preset") or "softbox"
    if preset == "three_point":
        # 经典三点布光：主光更硬、补光更低、轮廓光在背后。
        lights = [
            ((-4.0, -4.5, 5.5), 1200, 3.0),
            ((4.5, -2.0, 2.6), 520, 2.5),
            ((0.5, 4.2, 4.8), 900, 2.0),
        ]
    else:
        lights = [
            ((-3.2, -4.0, 5.0), 950, 4.0),
            ((3.5, -1.0, 3.0), 700, 3.0),
            ((0.0, 3.0, 4.5), 850, 3.0),
        ]
    for index, (location, energy, size) in enumerate(lights):
        data = bpy.data.lights.new(f"Softbox {index + 1}", "AREA")
        data.energy = energy
        data.shape = "DISK"
        data.size = size
        light = bpy.data.objects.new(data.name, data)
        bpy.context.collection.objects.link(light)
        light.location = location
        track(light, (0, 0, 0.65))
    bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 0, 0))
    floor = bpy.context.object
    floor.name = "Studio Floor"
    material = bpy.data.materials.new("Studio Floor Material")
    material.diffuse_color = (0.055, 0.06, 0.07, 1)
    floor.data.materials.append(material)


ALLOWED_CAMERAS = {"dolly_in", "side_track", "hero_orbit", "static"}


def configure_passes(product_meshes, pass_root: Path) -> None:
    """V3-01：为每帧额外输出 Beauty / Alpha / Depth / Normal / ProductIndex。

    - 产品网格获得稳定 pass_index（1 起），不用随导入顺序变化的数字 ID；
    - Depth 以 EXR 32 位保存，单位是相机空间米（Blender Z pass 语义）；
    - Normal 以 EXR 16 位保存，坐标空间为相机空间（Blender Normal pass 语义）；
    - Beauty/Alpha 用 PNG；Alpha 来自 film_transparent 的透明产品层。
    打开该开关会启用 film_transparent，因此**默认不开启**，既有单帧产物与验收哈希不受影响。
    """
    scene = bpy.context.scene
    scene.render.film_transparent = True
    view_layer = bpy.context.view_layer
    view_layer.use_pass_combined = True
    view_layer.use_pass_z = True
    view_layer.use_pass_normal = True
    view_layer.use_pass_object_index = True
    for index, obj in enumerate(product_meshes, start=1):
        obj.pass_index = index
    pass_root.mkdir(parents=True, exist_ok=True)
    scene.use_nodes = True
    # Blender 4 用 scene.node_tree；Blender 5 改成 scene.compositing_node_group。
    if hasattr(scene, "node_tree"):
        tree = scene.node_tree
    else:
        tree = scene.compositing_node_group or bpy.data.node_groups.new("DirectorCompositor", "CompositorNodeTree")
        scene.compositing_node_group = tree
    tree.nodes.clear()
    layers = tree.nodes.new("CompositorNodeRLayers")
    # Blender 5 移除了 CompositorNodeComposite（只有 OutputFile/RLayers 等），
    # 因此仅在旧版本上连接 Composite；主帧输出仍由渲染管线写出。
    if hasattr(bpy.types, "CompositorNodeComposite"):
        composite = tree.nodes.new("CompositorNodeComposite")
        tree.links.new(layers.outputs["Image"], composite.inputs["Image"])
    channels = {
        "beauty": ("Image", "PNG", "8"),
        "alpha": ("Alpha", "PNG", "8"),
        "depth": ("Depth", "OPEN_EXR", "32"),
        "normal": ("Normal", "OPEN_EXR", "16"),
        "index": ("IndexOB", "OPEN_EXR", "16"),
    }
    for name, (socket, file_format, depth) in channels.items():
        node = tree.nodes.new("CompositorNodeOutputFile")
        node.base_path = str(pass_root / name)
        node.file_slots[0].path = "frame_"
        node.format.file_format = file_format
        node.format.color_depth = depth
        if file_format == "OPEN_EXR":
            node.format.color_mode = "RGBA" if name == "normal" else "BW"
        tree.links.new(layers.outputs[socket], node.inputs[0])
    print(f"DIRECTOR_PASSES root={pass_root} channels={','.join(channels)}", flush=True)


def load_plan(path: str, total_frames: int) -> list[dict]:
    """Read the API-frozen plan and reject malformed timing before rendering."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法读取 DirectorPlan 快照: {exc}") from exc
    shots = payload.get("shots")
    if not isinstance(shots, list) or len(shots) != 3:
        raise RuntimeError("DirectorPlan 必须恰好包含 3 个镜头")
    normalized = []
    for index, shot in enumerate(shots, start=1):
        if not isinstance(shot, dict):
            raise RuntimeError(f"第 {index} 个镜头格式无效")
        camera = shot.get("camera")
        duration = shot.get("duration_frames")
        focal = shot.get("focal_length_mm")
        if camera not in ALLOWED_CAMERAS:
            raise RuntimeError(f"第 {index} 个镜头的相机模板无效: {camera!r}")
        if not isinstance(duration, int) or duration < 24:
            raise RuntimeError(f"第 {index} 个镜头时长必须至少为 24 帧")
        if not isinstance(focal, int) or not 15 <= focal <= 120:
            raise RuntimeError(f"第 {index} 个镜头焦距必须在 15–120mm")
        path = shot.get("camera_path")
        if path is not None and not isinstance(path, dict):
            raise RuntimeError(f"第 {index} 个镜头的 camera_path 格式无效")
        target = shot.get("camera_target_m")
        if target is not None and (not isinstance(target, (list, tuple)) or len(target) != 3):
            raise RuntimeError(f"第 {index} 个镜头的 camera_target_m 必须是三个数字")
        normalized.append({
            "id": str(shot.get("id") or f"shot_{index:02d}"),
            "camera": camera,
            "duration_frames": duration,
            "focal_length_mm": focal,
            "sensor_width_mm": float(shot.get("sensor_width_mm", 36) or 36),
            "camera_target_m": tuple(target) if target is not None else None,
            "camera_path": path,
        })
    if sum(shot["duration_frames"] for shot in normalized) != total_frames:
        raise RuntimeError(f"DirectorPlan 镜头总帧数必须等于 {total_frames}")
    return normalized


def insert_pose(camera, target, frame: int, location, focal_length_mm: int):
    camera.location = location
    camera.data.lens = focal_length_mm
    track(camera, target)
    camera.keyframe_insert(data_path="location", frame=frame)
    camera.keyframe_insert(data_path="rotation_euler", frame=frame)
    camera.data.keyframe_insert(data_path="lens", frame=frame)


def shot_positions(camera_type: str, start_frame: int, end_frame: int, path: dict | None = None):
    """把镜头编译成关键帧位置；给了 camera_path 就用合同里的数值。"""
    spec = path if isinstance(path, dict) and path.get("type") == camera_type else {}
    if camera_type == "dolly_in":
        return [
            (start_frame, tuple(spec.get("start_m") or (0.0, -4.8, 1.35))),
            (end_frame, tuple(spec.get("end_m") or (0.0, -3.0, 1.12))),
        ]
    if camera_type == "side_track":
        return [
            (start_frame, tuple(spec.get("start_m") or (-1.35, -3.65, 1.15))),
            (end_frame, tuple(spec.get("end_m") or (1.35, -3.65, 1.15))),
        ]
    if camera_type == "static":
        position = tuple(spec.get("position_m") or (0.0, -3.55, 1.2))
        return [(start_frame, position), (end_frame, position)]
    radius = float(spec.get("radius_m") or 3.45)
    height = float(spec.get("height_m") or 1.28)
    start_angle = float(spec.get("start_angle_deg", -34))
    end_angle = float(spec.get("end_angle_deg", 40))
    steps = max(2, min(8, end_frame - start_frame + 1))
    positions = []
    for index in range(steps):
        t = index / (steps - 1)
        frame = round(start_frame + (end_frame - start_frame) * t)
        angle = math.radians(start_angle + (end_angle - start_angle) * (t * t * (3 - 2 * t)))
        positions.append((frame, (radius * math.sin(angle), -radius * math.cos(angle), height)))
    return positions


def animate_camera(camera, shots: list[dict]):
    """Compile every editable V1 shot field into the Blender action."""
    frame = 1
    for shot in shots:
        end_frame = frame + shot["duration_frames"] - 1
        camera.data.sensor_width = shot.get("sensor_width_mm") or 36
        target = tuple(shot.get("camera_target_m") or (0, 0, 0.7))
        for keyframe, location in shot_positions(shot["camera"], frame, end_frame, shot.get("camera_path")):
            insert_pose(camera, target, keyframe, location, shot["focal_length_mm"])
        print(
            "DIRECTOR_SHOT "
            f"id={shot['id']} camera={shot['camera']} "
            f"focal_length_mm={shot['focal_length_mm']} "
            f"sensor_width_mm={camera.data.sensor_width} target={target} "
            f"frames={frame}-{end_frame}",
            flush=True,
        )
        frame = end_frame + 1

    # Blender 5 uses layered actions and no longer exposes Action.fcurves.
    # The default interpolation for inserted keys is Bezier, which is the
    # intended V1 camera motion on both Blender 4 and Blender 5.


def main():
    args = parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    shots = load_plan(args.plan, args.frames)
    plan_payload = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    clear_scene()
    meshes = import_product(args.input)
    normalize(meshes, plan_payload.get("product_pose"))
    add_lighting(plan_payload.get("scene"))
    if args.passes:
        configure_passes(meshes, output / "passes")
    camera_data = bpy.data.cameras.new("Director Camera")
    camera = bpy.data.objects.new("Director Camera", camera_data)
    bpy.context.collection.objects.link(camera)
    bpy.context.scene.camera = camera
    animate_camera(camera, shots)
    scene = bpy.context.scene
    scene.frame_start = 1
    scene.frame_end = args.frames
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = args.width
    scene.render.resolution_y = args.height
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(output / "frame_")
    scene.render.film_transparent = False
    scene.render.image_settings.color_mode = "RGB"
    scene.render.image_settings.color_depth = "8"
    scene.render.image_settings.compression = 40
    scene.render.fps = 24
    bpy.ops.wm.save_as_mainfile(filepath=str(output.parent / "scene.blend"))
    bpy.ops.render.render(animation=True)


if __name__ == "__main__":
    main()
