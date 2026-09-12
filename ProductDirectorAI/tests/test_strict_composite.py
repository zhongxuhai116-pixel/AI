from __future__ import annotations

import importlib.util
import io
import json
import shutil
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import numpy as np
import OpenEXR
from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]
STRICT_PATH = REPO_ROOT / "scripts" / "strict_composite.py"

spec = importlib.util.spec_from_file_location("strict_composite", STRICT_PATH)
strict = importlib.util.module_from_spec(spec)
spec.loader.exec_module(strict)  # type: ignore[union-attr]


class StrictCompositeRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = (REPO_ROOT / "var" / "test-strict-composite").resolve()
        self.base.mkdir(parents=True, exist_ok=True)
        self.workdir = (self.base / f"case-{uuid.uuid4().hex}").resolve()
        self.workdir.mkdir()
        self.product = self.workdir / "product"
        self.mask = self.workdir / "mask"
        self.background = self.workdir / "bg"
        self.out = self.workdir / "out"
        for folder in (self.product, self.mask, self.background):
            folder.mkdir(parents=True)

    def tearDown(self) -> None:
        resolved = self.workdir.resolve()
        if resolved.parent == self.base and resolved.name.startswith("case-"):
            shutil.rmtree(resolved, ignore_errors=True)

    def _write_background(self, path: Path, color: tuple[int, int, int], size: tuple[int, int] = (8, 8)) -> None:
        image = np.zeros((size[1], size[0], 3), dtype=np.uint8)
        image[:] = color
        Image.fromarray(image, mode="RGB").save(path)

    def _write_product(self, path: Path, color: tuple[int, int, int], *,
                       size: tuple[int, int] = (8, 8), alpha_rect=(1, 1, 5, 5)) -> None:
        image = Image.new("RGBA", size, (0, 0, 0, 0))
        for y in range(size[1]):
            for x in range(size[0]):
                image.putpixel((x, y), (color[0], color[1], color[2], 0))
        x0, y0, x1, y1 = alpha_rect
        for y in range(y0, y1):
            for x in range(x0, x1):
                image.putpixel((x, y), (color[0], color[1], color[2], 255))
        image.save(path)

    def _write_rgb_product_without_alpha(self, path: Path, color: tuple[int, int, int], size: tuple[int, int] = (8, 8)) -> None:
        image = np.zeros((size[1], size[0], 3), dtype=np.uint8)
        image[:] = color
        Image.fromarray(image, mode="RGB").save(path)

    def _write_mask(self, path: Path, *, on: bool, size: tuple[int, int] = (8, 8), rect=(1, 1, 5, 5)) -> None:
        image = Image.new("RGBA", size, (0, 0, 0, 0))
        if on:
            x0, y0, x1, y1 = rect
            for y in range(y0, y1):
                for x in range(x0, x1):
                    image.putpixel((x, y), (0, 0, 0, 255))
        image.save(path)

    def _write_continuous_alpha_product(self, path: Path, color: tuple[int, int, int],
                                        alpha_values: list[int], size: tuple[int, int] = (2, 2)) -> None:
        image = Image.new("RGBA", size, (0, 0, 0, 0))
        for y in range(size[1]):
            for x in range(size[0]):
                alpha = alpha_values[y * size[0] + x]
                image.putpixel((x, y), (color[0], color[1], color[2], alpha))
        image.save(path)

    def _run(self, product: Path, mask: Path, background: Path, out: Path, *,
             limit: int | None = None, expected_frames: int | None = None,
             start_frame: int | None = None, background_offset: int | None = None,
             allow_empty: bool = False, extra_args: list[str] | None = None):
        args = [
            "strict_composite",
            "--product", str(product),
            "--mask", str(mask),
            "--background", str(background),
            "--out", str(out),
            "--dilate", "0",
        ]
        if limit is not None:
            args.extend(["--limit", str(limit)])
        if expected_frames is not None:
            args.extend(["--expected-frames", str(expected_frames)])
        if start_frame is not None:
            args.extend(["--start-frame", str(start_frame)])
        if background_offset is not None:
            args.extend(["--background-frame-offset", str(background_offset)])
        if allow_empty:
            args.append("--allow-empty-product-frames")
        if extra_args:
            args.extend(extra_args)
        with io.StringIO() as buf, mock.patch("sys.argv", args), redirect_stdout(buf):
            code = strict.main()
            payload = buf.getvalue()
        return code, json.loads(payload.strip())

    def test_composite_valid(self) -> None:
        for index in (1, 2, 3):
            name = f"frame_{index:04d}.png"
            self._write_product(self.product / name, (20 * index, 10 * index, 30 * index))
            self._write_mask(self.mask / name, on=True)
            self._write_background(self.background / name, (10, 10, 10))

        code, report = self._run(self.product, self.mask, self.background, self.out,
                                 expected_frames=3, start_frame=1)

        self.assertEqual(code, 0)
        self.assertTrue(report["passed"])
        self.assertEqual(report["frames"], 3)
        self.assertEqual(report.get("failures"), [])
        self.assertTrue((self.out / "composite_report.json").exists())
        self.assertEqual(len(list(self.out.glob("composite_*.png"))), 3)

    def test_composite_allowed_not_required_partial_optional_layer_passes(self) -> None:
        for index in (1, 2, 3):
            name = f"frame_{index:04d}.png"
            self._write_product(self.product / name, (20 * index, 10 * index, 30 * index))
            self._write_mask(self.mask / name, on=True)
            self._write_background(self.background / name, (10, 10, 10))
        shadow = self.workdir / "shadow"
        shadow.mkdir()
        self._write_product(shadow / "frame_0001.png", (30, 30, 30), alpha_rect=(1, 1, 5, 5))
        plan = self.workdir / "plan.json"
        plan.write_text(json.dumps({"frame_count": 3, "start_frame": 1, "background_frame_offset": 0, "required_layers": {}}), encoding="utf-8")

        code, report = self._run(self.product, self.mask, self.background, self.out,
                                 extra_args=["--plan", str(plan), "--shadow", str(shadow)])

        self.assertEqual(code, 0, report)
        self.assertTrue(report["passed"])
        self.assertEqual(report["frames"], 3)
        self.assertEqual(report.get("failures"), [])
        self.assertEqual(len(list(self.out.glob("composite_*.png"))), 3)

    def test_composite_optional_layer_out_of_range_rejected(self) -> None:
        for index in (1, 2, 3):
            name = f"frame_{index:04d}.png"
            self._write_product(self.product / name, (20 * index, 10 * index, 30 * index))
            self._write_mask(self.mask / name, on=True)
            self._write_background(self.background / name, (10, 10, 10))
        shadow = self.workdir / "shadow"
        shadow.mkdir()
        self._write_product(shadow / "frame_0004.png", (30, 30, 30), alpha_rect=(1, 1, 5, 5))
        plan = self.workdir / "plan.json"
        plan.write_text(json.dumps({"frame_count": 3, "start_frame": 1, "background_frame_offset": 0, "required_layers": {}}), encoding="utf-8")

        code, report = self._run(self.product, self.mask, self.background, self.out,
                                 extra_args=["--plan", str(plan), "--shadow", str(shadow)])

        self.assertEqual(code, 1)
        self.assertFalse(report["passed"])
        self.assertTrue(any("越出" in item for item in report["failures"]), report["failures"])

    def test_composite_partial_optional_layer_file_is_still_validated(self) -> None:
        for index in (1, 2, 3):
            name = f"frame_{index:04d}.png"
            self._write_product(self.product / name, (20 * index, 10 * index, 30 * index))
            self._write_mask(self.mask / name, on=True)
            self._write_background(self.background / name, (10, 10, 10))
        shadow = self.workdir / "shadow"
        shadow.mkdir()
        self._write_rgb_product_without_alpha(shadow / "frame_0001.png", (30, 30, 30))
        plan = self.workdir / "plan.json"
        plan.write_text(json.dumps({"frame_count": 3, "start_frame": 1, "background_frame_offset": 0, "required_layers": {}}), encoding="utf-8")

        code, report = self._run(self.product, self.mask, self.background, self.out,
                                 extra_args=["--plan", str(plan), "--shadow", str(shadow)])

        self.assertEqual(code, 1)
        self.assertFalse(report["passed"])
        self.assertTrue(any("缺少可信 Alpha" in item for item in report["failures"]), report["failures"])

    def test_composite_missing_tail_rejected(self) -> None:
        for index in (1, 2, 3):
            name = f"frame_{index:04d}.png"
            self._write_product(self.product / name, (20 * index, 10 * index, 30 * index))
            self._write_mask(self.mask / name, on=True)
            self._write_background(self.background / name, (10, 10, 10))
        (self.background / "frame_0003.png").unlink()

        code, report = self._run(self.product, self.mask, self.background, self.out,
                                 expected_frames=3, start_frame=1)

        self.assertEqual(code, 1)
        self.assertFalse(report["passed"])
        self.assertTrue(any("background 缺少帧" in item for item in report["failures"]))

    def test_composite_shifted_frame_rejected(self) -> None:
        for index in (1, 2, 3):
            name = f"frame_{index:04d}.png"
            self._write_product(self.product / name, (40, 20, 10))
            self._write_mask(self.mask / name, on=True)
            self._write_background(self.background / name, (10, 10, 10))
        (self.mask / "frame_0002.png").replace(self.mask / "frame_0004.png")

        code, report = self._run(self.product, self.mask, self.background, self.out,
                                 expected_frames=3, start_frame=1)

        self.assertEqual(code, 1)
        self.assertFalse(report["passed"])
        self.assertTrue(any("mask 缺少帧" in item for item in report["failures"]))
        self.assertTrue(any("mask 有多余帧" in item for item in report["failures"]))

    def test_composite_empty_mask_rejected(self) -> None:
        name = "frame_0001.png"
        self._write_product(self.product / name, (255, 255, 255))
        self._write_mask(self.mask / name, on=False)
        self._write_background(self.background / name, (0, 0, 0))

        code, report = self._run(self.product, self.mask, self.background, self.out,
                                 expected_frames=1, start_frame=1)

        self.assertEqual(code, 1)
        self.assertFalse(report["passed"])
        self.assertTrue(any("可信 Alpha 有可见产品但遮罩为空" in item for item in report["failures"]))

    def test_composite_black_product_with_empty_mask_rejected(self) -> None:
        name = "frame_0001.png"
        self._write_product(self.product / name, (0, 0, 0))
        self._write_mask(self.mask / name, on=False)
        self._write_background(self.background / name, (128, 128, 128))

        code, report = self._run(self.product, self.mask, self.background, self.out,
                                 expected_frames=1, start_frame=1)

        self.assertEqual(code, 1)
        self.assertFalse(report["passed"])
        self.assertTrue(any("可信 Alpha 有可见产品但遮罩为空" in item for item in report["failures"]))

    def test_composite_preview_only_not_full_pass(self) -> None:
        for index in (1, 2, 3):
            name = f"frame_{index:04d}.png"
            self._write_product(self.product / name, (20 * index, 10 * index, 30 * index))
            self._write_mask(self.mask / name, on=True)
            self._write_background(self.background / name, (10, 10, 10))

        code, report = self._run(self.product, self.mask, self.background, self.out,
                                 expected_frames=3, start_frame=1, limit=1)

        self.assertEqual(code, 1)
        self.assertFalse(report["passed"])
        self.assertTrue(report.get("preview_only", False))

    def test_composite_common_middle_gap_rejected(self) -> None:
        for index in (1, 3):
            name = f"frame_{index:04d}.png"
            self._write_product(self.product / name, (20 * index, 10 * index, 30 * index))
            self._write_mask(self.mask / name, on=True)
            self._write_background(self.background / name, (10, 10, 10))

        code, report = self._run(self.product, self.mask, self.background, self.out,
                                 expected_frames=3, start_frame=1)

        self.assertEqual(code, 1)
        self.assertFalse(report["passed"])
        self.assertTrue(any("缺少帧" in item for item in report["failures"]))

    def test_composite_background_offset_mapping_passes(self) -> None:
        for index in (1, 2, 3):
            self._write_product(self.product / f"frame_{index:04d}.png", (20 * index, 10 * index, 30 * index))
            self._write_mask(self.mask / f"frame_{index:04d}.png", on=True)
        for index in (0, 1, 2):
            self._write_background(self.background / f"bg_{index:04d}.png", (10, 10, 10))

        code, report = self._run(self.product, self.mask, self.background, self.out,
                                 expected_frames=3, start_frame=1, background_offset=1)

        self.assertEqual(code, 0)
        self.assertTrue(report["passed"])
        self.assertEqual(report["background_frame_offset"], 1)

    def test_composite_without_plan_not_full_pass(self) -> None:
        name = "frame_0001.png"
        self._write_product(self.product / name, (255, 255, 255))
        self._write_mask(self.mask / name, on=True)
        self._write_background(self.background / name, (0, 0, 0))

        code, report = self._run(self.product, self.mask, self.background, self.out)

        self.assertEqual(code, 1)
        self.assertFalse(report["passed"])
        self.assertTrue(any("冻结计划" in item for item in report["failures"]))

    def test_composite_missing_alpha_rejected(self) -> None:
        name = "frame_0001.png"
        self._write_rgb_product_without_alpha(self.product / name, (255, 255, 255))
        self._write_mask(self.mask / name, on=True)
        self._write_background(self.background / name, (0, 0, 0))

        code, report = self._run(self.product, self.mask, self.background, self.out,
                                 expected_frames=1, start_frame=1)

        self.assertEqual(code, 1)
        self.assertFalse(report["passed"])
        self.assertTrue(any("缺少可信 Alpha" in item for item in report["failures"]))

    def test_composite_empty_product_frame_requires_explicit_contract(self) -> None:
        name = "frame_0001.png"
        self._write_product(self.product / name, (0, 0, 0), alpha_rect=(0, 0, 0, 0))
        self._write_mask(self.mask / name, on=False)
        self._write_background(self.background / name, (0, 0, 0))

        code, report = self._run(self.product, self.mask, self.background, self.out,
                                 expected_frames=1, start_frame=1)

        self.assertEqual(code, 1)
        self.assertFalse(report["passed"])
        self.assertTrue(any("允许空产品帧" in item for item in report["failures"]))

    def test_composite_empty_product_frame_allowed_with_contract(self) -> None:
        name = "frame_0001.png"
        self._write_product(self.product / name, (0, 0, 0), alpha_rect=(0, 0, 0, 0))
        self._write_mask(self.mask / name, on=False)
        self._write_background(self.background / name, (0, 0, 0))

        code, report = self._run(self.product, self.mask, self.background, self.out,
                                 expected_frames=1, start_frame=1, allow_empty=True)

        self.assertEqual(code, 0)
        self.assertTrue(report["passed"])
        self.assertTrue((self.out / "composite_report.json").exists())

    def test_report_records_display_linear_contract_not_scene_linear(self) -> None:
        name = "frame_0001.png"
        self._write_product(self.product / name, (124, 149, 170))
        self._write_mask(self.mask / name, on=True)
        self._write_background(self.background / name, (10, 10, 10))

        code, report = self._run(self.product, self.mask, self.background, self.out,
                                 expected_frames=1, start_frame=1)

        self.assertEqual(code, 0, report)
        self.assertTrue(report["passed"])
        contract = report["color_contract"]
        self.assertEqual(contract["working_space"], "display-linear")
        self.assertEqual(contract["product_color_space"], "display-srgb")
        self.assertEqual(contract["background_color_space"], "display-srgb")
        self.assertEqual(contract["blender_view_transform"], "AgX")
        self.assertTrue(contract["blender_view_transform_preapplied"])
        self.assertNotEqual(contract["working_space"], "scene-linear")
        self.assertTrue(report["approved_display_product_match_ok"])
        self.assertEqual(report["approved_display_product_match_status"], "PASS")
        self.assertLessEqual(report["frames_report"][0]["approved_display_product_max_abs_diff"], 1 / 255)

    def test_background_color_is_decoded_to_display_linear(self) -> None:
        path = self.background / "bg_0000.png"
        self._write_background(path, (128, 128, 128))

        decoded = strict.read_background_rgb(path, "display-srgb")

        expected = np.full((8, 8, 3), strict.srgb_to_linear(np.array(128, dtype=np.float32) / 255.0), dtype=np.float32)
        self.assertTrue(np.allclose(decoded, expected))

    def test_continuous_edge_alpha_composites_in_display_linear(self) -> None:
        product_color = (255, 128, 0)
        background_color = (0, 64, 255)
        name = "frame_0001.png"
        self._write_continuous_alpha_product(self.product / name, product_color, [255, 64, 255, 64], size=(2, 2))
        self._write_mask(self.mask / name, on=True, size=(2, 2), rect=(0, 0, 1, 2))
        self._write_background(self.background / name, background_color, size=(2, 2))

        code, report = self._run(self.product, self.mask, self.background, self.out,
                                 expected_frames=1, start_frame=1)

        self.assertEqual(code, 0, report)
        self.assertTrue(report["passed"])
        product_linear = strict.srgb_to_linear(np.asarray(product_color, dtype=np.float32) / 255.0)
        background_linear = strict.srgb_to_linear(np.asarray(background_color, dtype=np.float32) / 255.0)
        alpha = 64 / 255.0
        expected_linear = alpha * product_linear + (1.0 - alpha) * background_linear
        expected = (strict.linear_to_srgb(expected_linear) * 255 + 0.5).astype(np.uint8)
        with Image.open(self.out / "composite_0001.png") as image:
            actual = np.asarray(image.convert("RGB"), dtype=np.uint8)
        self.assertTrue(np.allclose(actual[0, 1].astype(np.float32), expected, atol=1.5), (actual[0, 1], expected))

    def test_scene_linear_png_product_rejected_not_implicitly_upgraded(self) -> None:
        name = "frame_0001.png"
        self._write_product(self.product / name, (124, 149, 170))
        self._write_mask(self.mask / name, on=True)
        self._write_background(self.background / name, (10, 10, 10))

        code, report = self._run(self.product, self.mask, self.background, self.out,
                                 expected_frames=1, start_frame=1,
                                 extra_args=["--product-color-space", "scene-linear"])

        self.assertEqual(code, 1)
        self.assertFalse(report["passed"])
        self.assertTrue(any("只接受 EXR" in item for item in report["failures"]), report["failures"])
        self.assertFalse((self.out / "composite_0001.png").exists())

    def test_scene_linear_background_rejected_without_trusted_mapping(self) -> None:
        name = "frame_0001.png"
        self._write_product(self.product / name, (124, 149, 170))
        self._write_mask(self.mask / name, on=True)
        self._write_background(self.background / name, (10, 10, 10))

        code, report = self._run(self.product, self.mask, self.background, self.out,
                                 expected_frames=1, start_frame=1,
                                 extra_args=["--background-color-space", "scene-linear"])

        self.assertEqual(code, 1)
        self.assertFalse(report["passed"])
        self.assertTrue(any("display-srgb" in item or "只接受 PNG" in item for item in report["failures"]), report["failures"])
        self.assertFalse((self.out / "composite_0001.png").exists())

    def test_invalid_plan_color_contract_rejected(self) -> None:
        name = "frame_0001.png"
        self._write_product(self.product / name, (124, 149, 170))
        self._write_mask(self.mask / name, on=True)
        self._write_background(self.background / name, (10, 10, 10))
        plan = self.workdir / "plan.json"
        plan.write_text(json.dumps({
            "frame_count": 1,
            "start_frame": 1,
            "background_frame_offset": 0,
            "color_contract": {"product_color_space": "unknown", "background_color_space": "display-srgb"},
        }), encoding="utf-8")

        code, report = self._run(self.product, self.mask, self.background, self.out,
                                 extra_args=["--plan", str(plan)])

        self.assertEqual(code, 1)
        self.assertFalse(report["passed"])
        self.assertTrue(any("color_contract" in item for item in report["failures"]), report["failures"])
        self.assertFalse((self.out / "composite_0001.png").exists())

    def test_cli_plan_color_contract_conflict_rejected(self) -> None:
        name = "frame_0001.png"
        self._write_product(self.product / name, (124, 149, 170))
        self._write_mask(self.mask / name, on=True)
        self._write_background(self.background / name, (10, 10, 10))
        plan = self.workdir / "plan.json"
        plan.write_text(json.dumps({
            "frame_count": 1,
            "start_frame": 1,
            "background_frame_offset": 0,
            "color_contract": {
                "product_color_space": "display-srgb",
                "background_color_space": "display-srgb",
            },
        }), encoding="utf-8")

        code, report = self._run(self.product, self.mask, self.background, self.out,
                                 extra_args=["--plan", str(plan), "--product-color-space", "scene-linear"])

        self.assertEqual(code, 1)
        self.assertFalse(report["passed"])
        self.assertTrue(any("冲突" in item for item in report["failures"]), report["failures"])
        self.assertFalse((self.out / "composite_0001.png").exists())

    def test_real_exr_beauty_rgba_alpha_not_matched_by_letter_a(self) -> None:
        path = self.product / "beauty_rgba.exr"
        width, height = 4, 4
        data = {
            "beauty.R": np.full((height, width), 0.2, dtype=np.float32),
            "beauty.G": np.full((height, width), 0.2, dtype=np.float32),
            "beauty.B": np.full((height, width), 0.2, dtype=np.float32),
            "beauty.A": np.ones((height, width), dtype=np.float32),
        }
        OpenEXR.File({}, data).write(str(path))

        alpha = strict.read_product_alpha(path)
        rgb = strict.read_linear_rgb(path)

        self.assertTrue(np.allclose(alpha, 1.0), f"alpha={alpha}")
        self.assertTrue(np.allclose(rgb, 0.2), f"rgb={rgb}")


if __name__ == "__main__":
    unittest.main()
