"""render_product.py Strict 输出契约的静态/可运行检查。

本机不安装 Blender，无法导入 bpy 执行渲染；这里用 AST 和源码文本检查真实脚本：
- --passes 首遍与第二遍都隐藏非产品 Mesh、开启 film_transparent；
- product PNG 为 RGBA，mask 与 passes/mask 由同帧 product 复制；
- 非 --passes legacy 仍保持不透明 RGB、不隐藏 Studio Floor；
- Strict 帧数与计划显式帧数一致，不足即失败。
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "blender" / "scripts" / "render_product.py"


def _function_text(name: str) -> str:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    return "\n".join(lines[function.lineno - 1: function.end_lineno])


class RenderProductStrictContractTests(unittest.TestCase):
    def test_strict_second_pass_uses_product_only_transparent_rgba(self) -> None:
        text = _function_text("render_strict_product_and_mask")
        self.assertIn("_hide_non_product_meshes(product_meshes)", text)
        self.assertIn("scene.render.film_transparent = True", text)
        self.assertIn('scene.render.image_settings.color_mode = "RGBA"', text)
        self.assertIn('scene.render.image_settings.file_format = "PNG"', text)

    def test_mask_is_copied_from_same_product_frame(self) -> None:
        text = _function_text("render_strict_product_and_mask")
        self.assertIn("frames = sorted(stage_product.glob(\"frame_*.png\"))", text)
        self.assertIn("expected_frames = args.frame_end or args.frames", text)
        self.assertIn("if len(frames) != expected_frames:", text)
        self.assertIn("shutil.copy2(source, stage_mask / source.name)", text)
        self.assertIn("shutil.copy2(source, stage_pass_mask / source.name)", text)
        self.assertIn("_replace_output_dirs(stage_root, output)", text)
        self.assertLess(text.index("shutil.copy2(source, stage_mask / source.name)"),
                        text.index("_replace_output_dirs(stage_root, output)"))

    def test_main_strict_first_pass_hides_floor_and_keeps_transparency(self) -> None:
        text = _function_text("main")
        self.assertIn("_hide_non_product_meshes(meshes)", text)
        self.assertIn("scene.render.film_transparent = True", text)
        self.assertIn('scene.render.image_settings.color_mode = "RGBA"', text)
        self.assertIn("render_strict_product_and_mask(meshes, output, args)", text)
        hide_index = text.index("_hide_non_product_meshes(meshes)")
        self.assertLess(hide_index, text.index("bpy.ops.render.render(animation=True)"))

    def test_main_legacy_non_passes_path_remains_opaque_rgb(self) -> None:
        text = _function_text("main")
        self.assertIn("scene.render.film_transparent = False", text)
        self.assertIn('scene.render.image_settings.color_mode = "RGB"', text)
        self.assertLess(text.index("scene.render.film_transparent = True"),
                        text.index("scene.render.film_transparent = False"))

    def test_second_pass_unbinds_and_verifies_pass_outputs_are_unchanged(self) -> None:
        text = _function_text("render_strict_product_and_mask")
        self.assertIn("_snapshot_protected_pass_files(pass_root)", text)
        self.assertIn("_suspend_pass_output_nodes(scene)", text)
        self.assertIn("_assert_protected_pass_files_unchanged(", text)
        self.assertIn("_restore_pass_output_nodes(scene, compositor_state)", text)
        self.assertLess(
            text.index("_suspend_pass_output_nodes(scene)"),
            text.index("bpy.ops.render.render(animation=True)"),
        )

    def test_compositor_unbind_helper_clears_blender5_node_group(self) -> None:
        text = _function_text("_suspend_pass_output_nodes")
        self.assertIn("compositing_node_group = None", text)
        self.assertIn("scene.use_nodes = False", text)
        self.assertIn("Blender 未真正解绑 compositor 输出节点", text)

    def test_lighting_returns_floor_and_hide_helper_only_hides_non_product_mesh(self) -> None:
        lighting = _function_text("add_lighting")
        self.assertIn("return floor", lighting)
        hide = _function_text("_hide_non_product_meshes")
        self.assertIn("if obj in product or obj.type != \"MESH\":", hide)
        self.assertIn("obj.hide_render = True", hide)


if __name__ == "__main__":
    unittest.main()
