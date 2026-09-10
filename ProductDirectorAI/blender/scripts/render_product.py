from __future__ import annotations

import argparse
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


def animate_camera(camera):
    camera.data.lens = 42
    camera.data.sensor_width = 36
    target = (0, 0, 0.7)
    keyframes = [
        (1, (0.0, -4.6, 1.35)),
        (48, (0.0, -3.25, 1.15)),
        (49, (-1.0, -3.45, 1.15)),
        (96, (1.0, -3.45, 1.15)),
    ]
    for frame, location in keyframes:
        camera.location = location
        track(camera, target)
        camera.keyframe_insert(data_path="location", frame=frame)
        camera.keyframe_insert(data_path="rotation_euler", frame=frame)
    radius = 3.4
    for frame in range(97, 145, 4):
        t = (frame - 97) / 47
        angle = math.radians(-22 + 62 * (t * t * (3 - 2 * t)))
        camera.location = (radius * math.sin(angle), -radius * math.cos(angle), 1.25)
        track(camera, target)
        camera.keyframe_insert(data_path="location", frame=frame)
        camera.keyframe_insert(data_path="rotation_euler", frame=frame)
    camera.location = (radius * math.sin(math.radians(40)), -radius * math.cos(math.radians(40)), 1.25)
    track(camera, target)
    camera.keyframe_insert(data_path="location", frame=144)
    camera.keyframe_insert(data_path="rotation_euler", frame=144)
    # Blender 5 uses layered actions and no longer exposes Action.fcurves.
    # The default interpolation for inserted keys is Bezier, which is the
    # intended V1 camera motion on both Blender 4 and Blender 5.


def main():
    args = parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    clear_scene()
    meshes = import_product(args.input)
    normalize(meshes)
    add_lighting()
    camera_data = bpy.data.cameras.new("Director Camera")
    camera = bpy.data.objects.new("Director Camera", camera_data)
    bpy.context.collection.objects.link(camera)
    bpy.context.scene.camera = camera
    animate_camera(camera)
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
