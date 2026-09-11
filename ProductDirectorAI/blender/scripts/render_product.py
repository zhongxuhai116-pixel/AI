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


def normalize(meshes):
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
    bpy.context.view_layer.update()


def track(camera, target):
    direction = Vector(target) - camera.location
    camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def add_lighting():
    world = bpy.context.scene.world or bpy.data.worlds.new("World")
    bpy.context.scene.world = world
    world.color = (0.035, 0.035, 0.045)
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
        normalized.append({
            "id": str(shot.get("id") or f"shot_{index:02d}"),
            "camera": camera,
            "duration_frames": duration,
            "focal_length_mm": focal,
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


def shot_positions(camera_type: str, start_frame: int, end_frame: int):
    if camera_type == "dolly_in":
        return [
            (start_frame, (0.0, -4.8, 1.35)),
            (end_frame, (0.0, -3.0, 1.12)),
        ]
    if camera_type == "side_track":
        return [
            (start_frame, (-1.35, -3.65, 1.15)),
            (end_frame, (1.35, -3.65, 1.15)),
        ]
    if camera_type == "static":
        return [
            (start_frame, (0.0, -3.55, 1.2)),
            (end_frame, (0.0, -3.55, 1.2)),
        ]
    radius = 3.45
    steps = max(2, min(8, end_frame - start_frame + 1))
    positions = []
    for index in range(steps):
        t = index / (steps - 1)
        frame = round(start_frame + (end_frame - start_frame) * t)
        angle = math.radians(-34 + 74 * (t * t * (3 - 2 * t)))
        positions.append((frame, (radius * math.sin(angle), -radius * math.cos(angle), 1.28)))
    return positions


def animate_camera(camera, shots: list[dict]):
    """Compile every editable V1 shot field into the Blender action."""
    camera.data.sensor_width = 36
    target = (0, 0, 0.7)
    frame = 1
    for shot in shots:
        end_frame = frame + shot["duration_frames"] - 1
        for keyframe, location in shot_positions(shot["camera"], frame, end_frame):
            insert_pose(camera, target, keyframe, location, shot["focal_length_mm"])
        print(
            "DIRECTOR_SHOT "
            f"id={shot['id']} camera={shot['camera']} "
            f"focal_length_mm={shot['focal_length_mm']} "
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
    clear_scene()
    meshes = import_product(args.input)
    normalize(meshes)
    add_lighting()
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
