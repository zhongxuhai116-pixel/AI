from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sys
import time
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
    parser.add_argument("--frame-end", type=int, default=None,
                        help="校准用：只渲染到该帧；不改变 --frames 对冻结计划的完整帧数校验")
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
    return floor


ALLOWED_CAMERAS = {"dolly_in", "side_track", "hero_orbit", "static"}


def _hide_non_product_meshes(product_meshes) -> list:
    """Strict 渲染只保留产品网格；返回被隐藏的对象以便逐帧结束恢复。"""
    scene = bpy.context.scene
    product = set(product_meshes)
    hidden: list = []
    for obj in scene.objects:
        if obj in product or obj.type != "MESH":
            continue
        if not obj.hide_render:
            obj.hide_render = True
            hidden.append(obj)
    return hidden


def _restore_render_visibility(objects) -> None:
    for obj in objects:
        obj.hide_render = False


def _snapshot_protected_pass_files(pass_root: Path) -> dict[str, tuple[int, int, str]]:
    """记录第二遍渲染不得改写的五通道文件（mtime、size、sha256）。"""
    protected: dict[str, tuple[int, int, str]] = {}
    if not pass_root.exists():
        return protected
    for path in sorted(pass_root.rglob("*")):
        if not path.is_file():
            continue
        relative_parts = path.relative_to(pass_root).parts
        if "mask" in relative_parts:
            continue
        stat = path.stat()
        protected[str(path)] = (
            stat.st_mtime_ns,
            stat.st_size,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
    return protected


def _suspend_pass_output_nodes(scene) -> dict:
    """为第二遍 product/mask 渲染隔离第一遍 compositor 输出。

    Blender 5.2 现场曾出现 ``scene.use_nodes=False`` 被忽略、合成器仍继续写
    passes 的情况；因此这里显式解绑 compositing_node_group，并在渲染后恢复。
    """
    state = {"use_nodes": scene.use_nodes}
    try:
        if hasattr(scene, "compositing_node_group"):
            state["compositing_node_group"] = scene.compositing_node_group
            try:
                scene.compositing_node_group = None
            except Exception as exc:
                raise RuntimeError(f"Blender compositing_node_group 解绑失败: {exc}") from exc
        elif hasattr(scene, "node_tree"):
            state["node_tree"] = scene.node_tree
            try:
                scene.node_tree = None
            except Exception as exc:
                raise RuntimeError(f"Blender node_tree 解绑失败: {exc}") from exc
        try:
            scene.use_nodes = False
        except Exception as exc:
            raise RuntimeError(f"Blender scene.use_nodes 停用失败: {exc}") from exc

        if hasattr(scene, "compositing_node_group"):
            # Blender 5：use_nodes 标志不足以证明 compositor 已停；必须真正解绑 node group。
            if getattr(scene, "compositing_node_group", None) is not None:
                raise RuntimeError(
                    "Blender 未真正解绑 compositor 输出节点；第二遍可能重写 passes，已拒绝渲染"
                )
        elif hasattr(scene, "node_tree"):
            # Blender 4 可验证语义：node_tree 存在且 use_nodes=True 时不允许继续。
            if getattr(scene, "node_tree", None) is not None and getattr(scene, "use_nodes", False):
                raise RuntimeError(
                    "Blender 4 未停用 compositor 输出节点；第二遍可能重写 passes，已拒绝渲染"
                )
    except BaseException:
        try:
            _restore_pass_output_nodes(scene, state)
        except Exception as restore_exc:
            print(f"DIRECTOR_COMPOSITOR_SUSPEND_RESTORE_FAILED {restore_exc}", flush=True)
        raise
    return state


def _restore_pass_output_nodes(scene, state: dict) -> None:
    """渲染后恢复 compositor 节点与 use_nodes 状态。

    恢复失败会抛出，避免静默留下场景状态污染；若调用方处于渲染异常路径，
    应自行决定是否吞掉该恢复异常。
    """
    try:
        if "compositing_node_group" in state:
            scene.compositing_node_group = state["compositing_node_group"]
        elif "node_tree" in state:
            scene.node_tree = state["node_tree"]
    except Exception as exc:
        raise RuntimeError(f"Blender compositor 节点组恢复失败: {exc}") from exc
    try:
        scene.use_nodes = state.get("use_nodes", scene.use_nodes)
    except Exception as exc:
        raise RuntimeError(f"Blender scene.use_nodes 恢复失败: {exc}") from exc


def _assert_protected_pass_files_unchanged(
    pass_root: Path,
    before: dict[str, tuple[int, int, str]],
    after: dict[str, tuple[int, int, str]],
) -> None:
    changed = [
        path
        for path in sorted(set(before) | set(after))
        if before.get(path) != after.get(path)
    ]
    if changed:
        preview = "，".join(changed[:8])
        raise RuntimeError(
            f"第二遍渲染改写了五通道产物 {len(changed)} 个文件，compositor 未真正隔离: {preview}"
        )
    print(f"DIRECTOR_PASS_UNCHANGED files={len(before)}", flush=True)


def _remove_tree(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _backup_path(target: Path) -> Path:
    return target.with_name(f"{target.name}.backup.{os.getpid()}.{time.time_ns()}")


def _replace_output_dirs(stage_root: Path, output: Path) -> None:
    """把 stage 下的 product/mask/passes/mask 原子切换到正式目录，失败可回滚。

    - backup 使用进程+纳秒唯一名，不预清理，避免删除上次异常遗留的唯一备份。
    - 若回滚 rename 也失败，保留 backup 并在异常中给出可恢复路径，绝不静默删除旧产物。
    """
    pairs = [
        (stage_root / "product", output / "product"),
        (stage_root / "mask", output / "mask"),
        (stage_root / "passes" / "mask", output / "passes" / "mask"),
    ]
    backups: list[tuple[Path, Path]] = []
    recovery_hints: list[str] = []
    try:
        for stage, target in pairs:
            backup = _backup_path(target)
            if backup.exists():
                raise RuntimeError(f"备份路径已存在，拒绝覆盖: {backup}")
            target_existed = target.exists()
            if target_existed:
                target.rename(backup)
            try:
                stage.rename(target)
            except Exception as stage_exc:
                if target_existed:
                    try:
                        backup.rename(target)
                    except Exception as backup_exc:
                        recovery_hints.append(
                            f"当前 pair 双故障，旧备份保留: {target} <- {backup} "
                            f"(stage 切换失败: {stage_exc}; backup 恢复失败: {backup_exc})"
                        )
                        raise RuntimeError(
                            f"当前产物切换失败且旧备份恢复失败，备份保留: {target} -> {backup} "
                            f"(stage 切换失败: {stage_exc}; backup 恢复失败: {backup_exc})"
                        ) from stage_exc
                else:
                    _remove_tree(target)
                raise
            backups.append((target, backup))
    except Exception as original_exc:
        rollback_failures: list[str] = []
        for target, backup in reversed(backups):
            try:
                # 即使该 target 原本不存在，也要删除已经切换到目标位置的新产物，
                # 避免回滚后留下部分新文件。
                _remove_tree(target)
                if backup.exists():
                    backup.rename(target)
            except Exception as exc:
                rollback_failures.append(f"{target}: {backup} ({exc})")
        if rollback_failures:
            evidence = rollback_failures + recovery_hints
            raise RuntimeError(
                "Strict 产物原子切换回滚失败，旧备份已保留，请手动恢复: "
                + "；".join(evidence)
            ) from original_exc
        raise
    for target, backup in backups:
        _remove_tree(backup)


def _cleanup_staging(stage_root: Path, output: Path) -> None:
    # 只清理暂存目录；任何 .backup.* 都是可恢复旧产物，除非替换流程已明确确认完成，
    # 否则不能在 finally 中删除。
    _remove_tree(stage_root)

def render_strict_product_and_mask(product_meshes, output: Path, args) -> None:
    """为 Strict 合成写 product RGBA 与连续 mask。

    - product：只渲染产品网格，PNG 8-bit RGBA（sRGB 编码、直通 alpha）。
    - mask：从同一帧 product RGBA 复制而来，alpha 即连续产品遮罩，避免二次渲染造成
      Beauty/Alpha/Mask 轮廓漂移。
    - passes/mask：保留给五通道校验器使用，内容与 strict/mask 完全一致。
    - 第二遍前先解绑 compositor 输出节点组，再清理旧帧；解绑失败时不会删除旧产物，
      也不会留下隐藏对象或半切换的 scene 状态。
    """
    scene = bpy.context.scene
    product_dir = output / "product"
    mask_dir = output / "mask"
    pass_mask_dir = output / "passes" / "mask"
    pass_root = output / "passes"
    stage_root = output / ".strict_stage"
    stage_product = stage_root / "product"
    stage_mask = stage_root / "mask"
    stage_pass_mask = stage_root / "passes" / "mask"

    # 预检：若 Blender 5.2 无法真正隔离 compositor，应在清理任何旧帧前失败。
    compositor_state = _suspend_pass_output_nodes(scene)

    hidden: list = []
    previous = None
    render_error: BaseException | None = None
    try:
        _remove_tree(stage_root)
        for folder in (stage_product, stage_mask, stage_pass_mask):
            folder.mkdir(parents=True, exist_ok=True)

        hidden = _hide_non_product_meshes(product_meshes)
        pass_before = _snapshot_protected_pass_files(pass_root)

        previous = (
            scene.render.filepath,
            scene.render.image_settings.file_format,
            scene.render.image_settings.color_mode,
            scene.use_nodes,
            scene.render.film_transparent,
        )
        scene.render.filepath = str(stage_product / "frame_")
        scene.render.image_settings.file_format = "PNG"
        scene.render.image_settings.color_mode = "RGBA"
        scene.render.film_transparent = True
        try:
            bpy.ops.render.render(animation=True)
        except BaseException as exc:
            render_error = exc
            raise

        _assert_protected_pass_files_unchanged(
            pass_root,
            pass_before,
            _snapshot_protected_pass_files(pass_root),
        )

        frames = sorted(stage_product.glob("frame_*.png"))
        expected_frames = args.frame_end or args.frames
        if len(frames) != expected_frames:
            raise RuntimeError(f"Strict product/mask 帧数 {len(frames)} 与本次渲染帧数 {expected_frames} 不一致")
        for source in frames:
            shutil.copy2(source, stage_mask / source.name)
            shutil.copy2(source, stage_pass_mask / source.name)

        _replace_output_dirs(stage_root, output)
        print(
            f"DIRECTOR_STRICT_LAYERS product={product_dir} mask={mask_dir} "
            f"passes_mask={pass_mask_dir} frames={len(frames)} color=RGBA",
            flush=True,
        )
    except BaseException as exc:
        render_error = exc
        raise
    finally:
        if previous is not None:
            (
                scene.render.filepath,
                scene.render.image_settings.file_format,
                scene.render.image_settings.color_mode,
                scene.use_nodes,
                scene.render.film_transparent,
            ) = previous
        _restore_render_visibility(hidden)
        _cleanup_staging(stage_root, output)
        if render_error is None:
            _restore_pass_output_nodes(scene, compositor_state)
        else:
            try:
                _restore_pass_output_nodes(scene, compositor_state)
            except Exception as exc:
                print(f"DIRECTOR_COMPOSITOR_RESTORE_AFTER_RENDER_ERROR {exc}", flush=True)

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
    node = tree.nodes.new("CompositorNodeOutputFile")
    if hasattr(node, "base_path"):
        # Blender 4：每个通道一个输出节点，各自目录、PNG/EXR 自选。
        node.base_path = str(pass_root / "beauty")
        node.file_slots[0].path = "frame_"
        node.format.file_format = "PNG"
        node.format.color_depth = "8"
        extra = {
            "alpha": ("Alpha", "PNG", "8", "BW"),
            "depth": ("Depth", "OPEN_EXR", "32", "BW"),
            "normal": ("Normal", "OPEN_EXR", "16", "RGBA"),
            "index": ("IndexOB", "OPEN_EXR", "16", "BW"),
        }
        for name, (socket, file_format, depth, color_mode) in extra.items():
            extra_node = tree.nodes.new("CompositorNodeOutputFile")
            extra_node.base_path = str(pass_root / name)
            extra_node.file_slots[0].path = "frame_"
            extra_node.format.file_format = file_format
            extra_node.format.color_depth = depth
            extra_node.format.color_mode = color_mode
            tree.links.new(layers.outputs[socket], extra_node.inputs[0])
        layout = "per-channel"
    else:
        # Blender 5：只支持 OPEN_EXR_MULTILAYER，而且**每个通道必须各用一个输出节点**
        # （实测：单个节点挂多个 item 时只有第一个会落盘；每通道一个节点则全部写出）。
        node = None
        created: list[str] = []
        for name, socket, socket_type in [
            ("beauty", "Image", "RGBA"),
            ("alpha", "Alpha", "FLOAT"),
            ("depth", "Depth", "FLOAT"),
            ("normal", "Normal", "VECTOR"),
        ]:
            channel_node = tree.nodes.new("CompositorNodeOutputFile")
            channel_node.directory = str(pass_root / name)
            channel_node.file_name = "frame_"
            channel_node.format.file_format = "OPEN_EXR_MULTILAYER"
            try:
                channel_node.file_output_items.new(socket_type, name)
                tree.links.new(layers.outputs[socket], channel_node.inputs[0])
                created.append(name)
            except Exception as exc:  # 单个通道不支持时不影响其余通道
                print(f"DIRECTOR_PASS_SKIPPED {name}: {exc}", flush=True)
                tree.nodes.remove(channel_node)
        print(f"DIRECTOR_PASS_CHANNELS {','.join(created)}", flush=True)
        layout = "per-channel-exr"
    if node is not None:
        tree.links.new(layers.outputs["Image"], node.inputs[0])
    print(f"DIRECTOR_PASSES root={pass_root} layout={layout}", flush=True)


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
    if args.frame_end is None:
        render_frame_end = args.frames
    else:
        if args.frame_end < 1 or args.frame_end > args.frames:
            raise SystemExit("--frame-end 必须在 1 与 --frames 之间")
        render_frame_end = args.frame_end
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
    scene.frame_end = render_frame_end
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = args.width
    scene.render.resolution_y = args.height
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(output / "frame_")
    if args.passes:
        # Strict 首遍也必须只含产品网格并开启透明，确保 Beauty/Alpha、product、mask
        # 来自同一产品可见性；floor/阴影/背景不进入 Strict 多通道。
        strict_hidden = _hide_non_product_meshes(meshes)
        scene.render.film_transparent = True
        scene.render.image_settings.color_mode = "RGBA"
    else:
        # 非 passes 的 V1/V2 legacy 行为保持不变：不透明 RGB、含 Studio Floor。
        strict_hidden = []
        scene.render.film_transparent = False
        scene.render.image_settings.color_mode = "RGB"
    scene.render.image_settings.color_depth = "8"
    scene.render.image_settings.compression = 40
    scene.render.fps = 24
    bpy.ops.wm.save_as_mainfile(filepath=str(output.parent / "scene.blend"))
    try:
        bpy.ops.render.render(animation=True)
    finally:
        _restore_render_visibility(strict_hidden)
    if args.passes:
        render_strict_product_and_mask(meshes, output, args)


if __name__ == "__main__":
    main()
