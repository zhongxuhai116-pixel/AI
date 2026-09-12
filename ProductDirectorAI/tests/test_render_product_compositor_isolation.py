"""render_product.py Blender 5 compositor 解绑失败的伪场景负例。"""
from __future__ import annotations

import importlib.util
import shutil
import sys
import types
import unittest
from unittest import mock
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "blender" / "scripts" / "render_product.py"


class _FakeOps:
    def __init__(self):
        self.rendered = 0

    def render(self, animation=False):
        self.rendered += 1


class _Blender5SceneSetterIgnored:
    """Blender 5.2 风险模拟：compositing_node_group setter 被忽略，use_nodes 却能置 False。"""

    def __init__(self):
        self._group = object()
        self._use_nodes = True
        self.objects = []

    @property
    def compositing_node_group(self):
        return self._group

    @compositing_node_group.setter
    def compositing_node_group(self, value):
        # 历史现场：赋值被忽略，node group 仍绑定。
        return None

    @property
    def use_nodes(self):
        return self._use_nodes

    @use_nodes.setter
    def use_nodes(self, value):
        self._use_nodes = bool(value)


class _Blender5SceneSetterRaises:
    """Blender 5.2 另一种失败：compositing_node_group setter 直接抛异常。"""

    def __init__(self):
        self._group = object()
        self._use_nodes = True
        self.objects = []

    @property
    def compositing_node_group(self):
        return self._group

    @compositing_node_group.setter
    def compositing_node_group(self, value):
        raise RuntimeError("setter refuses unbind")

    @property
    def use_nodes(self):
        return self._use_nodes

    @use_nodes.setter
    def use_nodes(self, value):
        self._use_nodes = bool(value)


class _RestoreFailingScene:
    @property
    def compositing_node_group(self):
        return None

    @compositing_node_group.setter
    def compositing_node_group(self, value):
        raise RuntimeError("restore setter failed")

    @property
    def use_nodes(self):
        return False

    @use_nodes.setter
    def use_nodes(self, value):
        return None


def _load_script(scene=None):
    fake_bpy = types.ModuleType("bpy")
    fake_bpy.context = types.SimpleNamespace(scene=scene if scene is not None else _Blender5SceneSetterIgnored())
    fake_bpy.ops = _FakeOps()
    fake_mathutils = types.ModuleType("mathutils")
    fake_mathutils.Vector = lambda *args: args
    sys.modules["bpy"] = fake_bpy
    sys.modules["mathutils"] = fake_mathutils
    spec = importlib.util.spec_from_file_location("render_product_probe_module", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, fake_bpy, fake_mathutils


class RenderProductCompositorIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = PROJECT / "var" / "_unittest_tmp" / "compositor-isolation"
        if self.tmp.exists():
            shutil.rmtree(self.tmp, ignore_errors=True)
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.addCleanup(lambda: sys.modules.pop("bpy", None))
        self.addCleanup(lambda: sys.modules.pop("mathutils", None))

    def _args(self, frames=1):
        return types.SimpleNamespace(frames=frames)

    def test_blender5_setter_ignored_use_nodes_false_still_fails_before_render(self) -> None:
        module, fake_bpy, _ = _load_script()
        output = self.tmp / "output"
        output.mkdir(parents=True, exist_ok=True)
        (output / "passes").mkdir(parents=True, exist_ok=True)
        with self.assertRaisesRegex(RuntimeError, "未真正解绑 compositor"):
            module.render_strict_product_and_mask([], output, self._args())
        self.assertEqual(fake_bpy.ops.rendered, 0)

    def test_failed_suspend_restores_scene_visibility_and_keeps_old_artifacts(self) -> None:
        scene = _Blender5SceneSetterIgnored()
        mesh = types.SimpleNamespace(hide_render=False)
        scene.objects = [mesh]
        original_group = scene.compositing_node_group
        module, fake_bpy, _ = _load_script(scene=scene)
        output = self.tmp / "output"
        product_file = output / "product" / "frame_0001.png"
        mask_file = output / "mask" / "frame_0001.png"
        pass_mask_file = output / "passes" / "mask" / "frame_0001.png"
        for path in (product_file, mask_file, pass_mask_file):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"old-artifact")

        with self.assertRaisesRegex(RuntimeError, "未真正解绑 compositor"):
            module.render_strict_product_and_mask([], output, self._args())

        self.assertEqual(fake_bpy.ops.rendered, 0)
        self.assertTrue(scene.use_nodes)
        self.assertIs(scene.compositing_node_group, original_group)
        self.assertFalse(mesh.hide_render)
        self.assertEqual(product_file.read_bytes(), b"old-artifact")
        self.assertEqual(mask_file.read_bytes(), b"old-artifact")
        self.assertEqual(pass_mask_file.read_bytes(), b"old-artifact")

    def test_suspend_setter_exception_is_exception_safe(self) -> None:
        module = _load_script()[0]
        scene = _Blender5SceneSetterRaises()
        original_group = scene.compositing_node_group
        with self.assertRaisesRegex(RuntimeError, "compositing_node_group 解绑失败"):
            module._suspend_pass_output_nodes(scene)
        self.assertIs(scene.compositing_node_group, original_group)
        self.assertTrue(scene.use_nodes)

    def test_replace_output_dirs_keeps_backup_when_rollback_rename_fails(self) -> None:
        module = _load_script()[0]
        output = self.tmp / "output"
        stage = self.tmp / "stage"
        (stage / "product").mkdir(parents=True, exist_ok=True)
        (stage / "product" / "frame_0001.png").write_bytes(b"new-product")
        (stage / "mask").mkdir(parents=True, exist_ok=True)
        (stage / "mask" / "frame_0001.png").write_bytes(b"new-mask")
        # passes/mask 缺失，第二对 stage.rename 会失败，触发已成功第一对的回滚。
        old_product = output / "product" / "frame_0001.png"
        old_product.parent.mkdir(parents=True, exist_ok=True)
        old_product.write_bytes(b"old-product")

        real_rename = Path.rename

        def failing_rename(self, target):
            if ".backup." in str(self) and ".backup." not in str(target):
                raise OSError("injected rollback rename failure")
            return real_rename(self, target)

        with mock.patch.object(Path, "rename", failing_rename):
            with self.assertRaisesRegex(RuntimeError, "回滚失败"):
                module._replace_output_dirs(stage, output)

        backups = list(self.tmp.glob("**/*.backup.*"))
        self.assertTrue(backups, "旧产物备份必须保留")
        product_backups = [path for path in backups if path.name.startswith("product")]
        self.assertTrue(product_backups, backups)
        self.assertTrue(any((path / "frame_0001.png").read_bytes() == b"old-product" for path in product_backups))

    def test_current_pair_double_failure_reports_and_keeps_backup(self) -> None:
        module = _load_script()[0]
        output = self.tmp / "output"
        stage = self.tmp / "stage"
        old_product = output / "product" / "frame_0001.png"
        old_product.parent.mkdir(parents=True, exist_ok=True)
        old_product.write_bytes(b"old-product")

        real_rename = Path.rename

        def failing_rename(self, target):
            if ".backup." in str(self) and ".backup." not in str(target):
                raise OSError("injected backup restore failure")
            return real_rename(self, target)

        with mock.patch.object(Path, "rename", failing_rename):
            with self.assertRaisesRegex(RuntimeError, "当前产物切换失败且旧备份恢复失败"):
                module._replace_output_dirs(stage, output)

        backups = list(self.tmp.glob("**/*.backup.*"))
        product_backups = [path for path in backups if path.name.startswith("product")]
        self.assertTrue(product_backups, "当前 pair 的旧备份必须保留")
        self.assertTrue(any((path / "frame_0001.png").read_bytes() == b"old-product" for path in product_backups))

        self.assertTrue(any(".backup." in str(path) for path in backups))

    def test_current_pair_double_failure_is_in_top_level_error_when_prior_rollback_also_fails(self) -> None:
        module = _load_script()[0]
        output = self.tmp / "output"
        stage = self.tmp / "stage"
        (stage / "product").mkdir(parents=True, exist_ok=True)
        (stage / "product" / "frame_0001.png").write_bytes(b"new-product")
        # 第二对 mask 故意缺失 stage，用于触发当前 pair 双故障。
        old_product = output / "product" / "frame_0001.png"
        old_product.parent.mkdir(parents=True, exist_ok=True)
        old_product.write_bytes(b"old-product")
        old_mask = output / "mask" / "frame_0001.png"
        old_mask.parent.mkdir(parents=True, exist_ok=True)
        old_mask.write_bytes(b"old-mask")

        real_rename = Path.rename

        def failing_rename(self, target):
            if str(self) == str(stage / "mask"):
                raise OSError("injected stage mask failure")
            if ".backup." in str(self) and ".backup." not in str(target):
                raise OSError("injected backup restore failure")
            return real_rename(self, target)

        with mock.patch.object(Path, "rename", failing_rename):
            with self.assertRaisesRegex(RuntimeError, "当前 pair 双故障"):
                module._replace_output_dirs(stage, output)

        backups = list(self.tmp.glob("**/*.backup.*"))
        mask_backups = [path for path in backups if path.name.startswith("mask")]
        self.assertTrue(mask_backups, "当前 pair 的旧 mask 备份必须纳入顶层回滚证据")
        self.assertTrue(any((path / "frame_0001.png").read_bytes() == b"old-mask" for path in mask_backups))

    def test_rollback_removes_new_target_when_no_old_backup_existed(self) -> None:
        module = _load_script()[0]
        output = self.tmp / "output"
        stage = self.tmp / "stage"
        (stage / "product").mkdir(parents=True, exist_ok=True)
        (stage / "product" / "frame_0001.png").write_bytes(b"new-product")
        # mask 也建立，第三对 passes/mask 缺失导致失败。
        (stage / "mask").mkdir(parents=True, exist_ok=True)
        (stage / "mask" / "frame_0001.png").write_bytes(b"new-mask")

        with self.assertRaises(Exception):
            module._replace_output_dirs(stage, output)

        self.assertFalse((output / "product" / "frame_0001.png").exists())
        self.assertFalse((output / "mask" / "frame_0001.png").exists())

    def test_restore_failure_raises_without_original_render_error(self) -> None:
        module = _load_script()[0]
        scene = _RestoreFailingScene()
        with self.assertRaisesRegex(RuntimeError, "compositor 节点组恢复失败"):
            module._restore_pass_output_nodes(scene, {"compositing_node_group": object(), "use_nodes": False})


if __name__ == "__main__":
    unittest.main()
