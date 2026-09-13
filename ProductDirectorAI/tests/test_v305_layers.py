#!/usr/bin/env python3
"""V3-05 独立层（阴影/反射/遮挡）的合成数据单测。

数据全部由 numpy/Pillow 程序化合成（无 EXR、无 Blender、无后端依赖），
本机托管 Python 即可运行：

    python -m unittest tests.test_v305_layers -v

覆盖：
- strict_composite.py --layers 的阴影乘算 / 反射加算 / 遮挡盖回与像素锁定豁免；
- 全零层（中性层）与不带 --layers 的输出逐字节一致（回归）；
- 层帧数不足时如实失败；
- build_layers.py 的底板差分数学与全零层注明。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSITE_SCRIPT = REPO_ROOT / "scripts" / "strict_composite.py"
BUILD_LAYERS_SCRIPT = REPO_ROOT / "scripts" / "build_layers.py"

FRAMES = 4
WIDTH, HEIGHT = 120, 80
# 产品真值矩形 (x, y, w, h)
PRODUCT_AT = (40, 20, 40, 30)
# 遮挡物矩形（完全落在产品掩码内，供豁免计数验证）
OCCLUDER_AT = (45, 25, 10, 10)  # 100 px
# 背景纯色（sRGB 0-1）
BG_COLOR = (0.50, 0.40, 0.30)
# 遮挡物颜色（sRGB 0-1）
OCCLUDER_COLOR = (0.90, 0.80, 0.10)
SHADOW_REGION_FACTOR = 0.5   # x < WIDTH//2 的阴影因子
REFLECTION_ADD_SRGB = 0.2    # x >= 90 的反射条带（sRGB 编码值）


def srgb_to_linear(value: np.ndarray) -> np.ndarray:
    return np.where(value <= 0.04045, value / 12.92, ((value + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(value: np.ndarray) -> np.ndarray:
    return np.where(value <= 0.0031308, value * 12.92, 1.055 * np.power(np.clip(value, 0, None), 1 / 2.4) - 0.055)


def save_rgb(array: np.ndarray, target: Path) -> None:
    Image.fromarray((np.clip(array, 0, 1) * 255 + 0.5).astype(np.uint8)).save(target)


def _product_appearance(width: int = 40, height: int = 30) -> np.ndarray:
    """有纹理的程序化产品外观（sRGB 0-1）。"""
    yy, xx = np.mgrid[0:height, 0:width]
    array = np.zeros((height, width, 3), dtype=np.float32)
    array[:, :, 0] = 0.6 + 0.2 * np.sin(xx * 0.4)
    array[:, :, 1] = 0.3 + 0.2 * np.cos(yy * 0.5)
    array[:, :, 2] = 0.2 + 0.1 * ((xx // 5) % 2)
    return np.clip(array, 0.0, 1.0)


def make_composite_fixtures(root: Path) -> dict:
    """生成 strict_composite 的全套输入：product/mask/bg + 三层独立层 + 中性零层。"""
    dirs = {
        name: root / name
        for name in ("product", "mask", "bg", "shadow", "reflection", "occlusion",
                     "shadow_neutral", "reflection_neutral", "occlusion_neutral")
    }
    for directory in dirs.values():
        directory.mkdir(parents=True, exist_ok=True)
    px, py, pw, ph = PRODUCT_AT
    ox, oy, ow, oh = OCCLUDER_AT
    appearance = _product_appearance(pw, ph)
    bg = np.zeros((HEIGHT, WIDTH, 3), dtype=np.float32)
    bg[:, :] = BG_COLOR
    for frame in range(FRAMES):
        product = np.zeros((HEIGHT, WIDTH, 4), dtype=np.uint8)
        product[py:py + ph, px:px + pw, :3] = (np.clip(appearance, 0, 1) * 255 + 0.5).astype(np.uint8)
        product[py:py + ph, px:px + pw, 3] = 255
        Image.fromarray(product, mode="RGBA").save(dirs["product"] / f"prod_{frame:04d}.png")
        mask = np.zeros((HEIGHT, WIDTH, 4), dtype=np.uint8)
        mask[:, :, :3] = 255
        mask[:, :, 3] = 0
        mask[py:py + ph, px:px + pw, 3] = 255
        Image.fromarray(mask, mode="RGBA").save(dirs["mask"] / f"frame_{frame:04d}.png")
        save_rgb(bg, dirs["bg"] / f"bg_{frame:04d}.png")
        # 阴影层：左半因子 0.5，右半 1.0（16 位灰度，线性因子 × 65535）
        factor = np.ones((HEIGHT, WIDTH), dtype=np.float32)
        factor[:, : WIDTH // 2] = SHADOW_REGION_FACTOR
        Image.fromarray((factor * 65535 + 0.5).astype(np.uint16)).save(dirs["shadow"] / f"frame_{frame:04d}.png")
        # 反射层：x>=90 条带加 sRGB 0.2 的红光
        reflection = np.zeros((HEIGHT, WIDTH, 3), dtype=np.float32)
        reflection[:, 90:, 0] = REFLECTION_ADD_SRGB
        save_rgb(reflection, dirs["reflection"] / f"frame_{frame:04d}.png")
        # 遮挡层：产品掩码内的遮挡物色块（alpha 255）
        occlusion = np.zeros((HEIGHT, WIDTH, 4), dtype=np.uint8)
        occlusion[oy:oy + oh, ox:ox + ow, 0] = int(OCCLUDER_COLOR[0] * 255 + 0.5)
        occlusion[oy:oy + oh, ox:ox + ow, 1] = int(OCCLUDER_COLOR[1] * 255 + 0.5)
        occlusion[oy:oy + oh, ox:ox + ow, 2] = int(OCCLUDER_COLOR[2] * 255 + 0.5)
        occlusion[oy:oy + oh, ox:ox + ow, 3] = 255
        Image.fromarray(occlusion, mode="RGBA").save(dirs["occlusion"] / f"frame_{frame:04d}.png")
        # 中性层：阴影全 1.0、反射全黑、遮挡全透明 —— 应与不加分层逐字节一致
        Image.fromarray(np.full((HEIGHT, WIDTH), 65535, dtype=np.uint16)).save(
            dirs["shadow_neutral"] / f"frame_{frame:04d}.png")
        save_rgb(np.zeros((HEIGHT, WIDTH, 3), dtype=np.float32), dirs["reflection_neutral"] / f"frame_{frame:04d}.png")
        Image.fromarray(np.zeros((HEIGHT, WIDTH, 4), dtype=np.uint8), mode="RGBA").save(
            dirs["occlusion_neutral"] / f"frame_{frame:04d}.png")
    return {name: str(path) for name, path in dirs.items()}


def run_composite(out_dir: Path, fixtures: dict, *extra: str) -> tuple[int, dict]:
    result = subprocess.run(
        [sys.executable, str(COMPOSITE_SCRIPT),
         "--product", fixtures["product"], "--mask", fixtures["mask"],
         "--background", fixtures["bg"], "--out", str(out_dir), "--dilate", "2",
         "--expected-frames", str(FRAMES), "--start-frame", "0", *extra],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(REPO_ROOT),
    )
    report = {}
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError:
        pass
    return result.returncode, report


def read_png(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0


class StrictCompositeLayersTest(unittest.TestCase):
    """strict_composite.py --layers 的合成数学与豁免逻辑。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="v305_layers_")
        cls.root = Path(cls._tmp.name)
        cls.fixtures = make_composite_fixtures(cls.root / "fixtures")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_baseline_without_layers(self) -> None:
        """不带 --layers：既有行为回归——像素锁定通过，掩码内外颜色符合公式。"""
        out = self.root / "out_baseline"
        code, report = run_composite(out, self.fixtures)
        self.assertEqual(code, 0, report)
        self.assertTrue(report["pixel_lock_ok"])
        self.assertEqual(report["occlusion_exempted_pixels_total"], 0)
        frame = read_png(out / "composite_0000.png")
        px, py, pw, ph = PRODUCT_AT
        appearance = _product_appearance(pw, ph)
        np.testing.assert_allclose(frame[py + 5, px + 5], appearance[5, 5], atol=2 / 255)
        np.testing.assert_allclose(frame[5, 5], BG_COLOR, atol=2 / 255)

    def test_shadow_multiplies_background(self) -> None:
        """阴影层乘算到背景（线性空间），产品区不受影响。"""
        out = self.root / "out_shadow"
        code, report = run_composite(out, self.fixtures, "--layers", f"shadow={self.fixtures['shadow']}")
        self.assertEqual(code, 0, report)
        self.assertTrue(report["pixel_lock_ok"])
        self.assertAlmostEqual(report["layers"]["shadow"]["coverage_mean"], 0.5, places=2)
        frame = read_png(out / "composite_0000.png")
        bg_linear = srgb_to_linear(np.array(BG_COLOR, dtype=np.float32))
        expected_dark = linear_to_srgb(bg_linear * (32768 / 65535))
        np.testing.assert_allclose(frame[5, 5], expected_dark, atol=2 / 255)
        np.testing.assert_allclose(frame[5, 100], BG_COLOR, atol=2 / 255)

    def test_reflection_adds_to_background(self) -> None:
        """反射层加算到背景（线性空间）。"""
        out = self.root / "out_reflection"
        code, report = run_composite(out, self.fixtures, "--layers", f"reflection={self.fixtures['reflection']}")
        self.assertEqual(code, 0, report)
        frame = read_png(out / "composite_0000.png")
        bg_linear = srgb_to_linear(np.array(BG_COLOR, dtype=np.float32))
        add_linear = srgb_to_linear(np.array([REFLECTION_ADD_SRGB, 0.0, 0.0], dtype=np.float32))
        expected = linear_to_srgb(bg_linear + add_linear)
        np.testing.assert_allclose(frame[5, 100], expected, atol=2 / 255)
        np.testing.assert_allclose(frame[5, 5], BG_COLOR, atol=2 / 255)

    def test_occlusion_over_and_exemption(self) -> None:
        """遮挡层盖回产品上方；遮挡区豁免像素锁定并逐帧计数。"""
        out = self.root / "out_occlusion"
        code, report = run_composite(out, self.fixtures, "--layers", f"occlusion={self.fixtures['occlusion']}")
        self.assertEqual(code, 0, report)
        self.assertTrue(report["pixel_lock_ok"])
        _, _, ow, oh = OCCLUDER_AT
        self.assertEqual(report["occlusion_exempted_pixels_total"], ow * oh * FRAMES)
        frame = read_png(out / "composite_0000.png")
        ox, oy = OCCLUDER_AT[0], OCCLUDER_AT[1]
        expected = np.array([int(c * 255 + 0.5) for c in OCCLUDER_COLOR], dtype=np.float32) / 255.0
        np.testing.assert_allclose(frame[oy + 2, ox + 2], expected, atol=2 / 255)
        # 掩码内未被遮挡的像素仍与可信产品一致
        px, py, pw, ph = PRODUCT_AT
        appearance = _product_appearance(pw, ph)
        np.testing.assert_allclose(frame[py + 2, px + 2], appearance[2, 2], atol=2 / 255)

    def test_neutral_layers_byte_identical_to_baseline(self) -> None:
        """中性层（阴影全 1 / 反射全 0 / 遮挡全透明）输出与不加分层逐字节一致。"""
        baseline = self.root / "out_baseline_cmp"
        layered = self.root / "out_neutral_cmp"
        code_a, _ = run_composite(baseline, self.fixtures)
        code_b, report = run_composite(
            layered, self.fixtures, "--layers",
            f"shadow={self.fixtures['shadow_neutral']}",
            f"reflection={self.fixtures['reflection_neutral']}",
            f"occlusion={self.fixtures['occlusion_neutral']}",
        )
        self.assertEqual((code_a, code_b), (0, 0), report)
        self.assertEqual(report["occlusion_exempted_pixels_total"], 0)
        for frame in range(FRAMES):
            self.assertEqual(
                (baseline / f"composite_{frame:04d}.png").read_bytes(),
                (layered / f"composite_{frame:04d}.png").read_bytes(),
            )

    def test_layer_frame_shortage_fails(self) -> None:
        """层目录帧数不足时如实失败（不静默跳过）。"""
        short = self.root / "shadow_short"
        short.mkdir()
        (short / "frame_0000.png").write_bytes(
            (Path(self.fixtures["shadow"]) / "frame_0000.png").read_bytes())
        out = self.root / "out_short"
        code, report = run_composite(out, self.fixtures, "--layers", f"shadow={short}")
        self.assertEqual(code, 1)
        self.assertIn("shadow", json.dumps(report.get("failures", []), ensure_ascii=False))

    def test_invalid_layer_name_rejected(self) -> None:
        """非法层名直接拒绝。"""
        out = self.root / "out_bad_layer"
        code, _ = run_composite(out, self.fixtures, "--layers", f"foo={self.fixtures['shadow']}")
        self.assertNotEqual(code, 0)


class BuildLayersTest(unittest.TestCase):
    """build_layers.py 的底板差分数学。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="v305_build_layers_")
        cls.root = Path(cls._tmp.name)
        fixtures = cls.root / "fixtures"
        cls.dirs = {name: fixtures / name for name in ("beauty", "plate", "mask", "occlusion")}
        for directory in cls.dirs.values():
            directory.mkdir(parents=True, exist_ok=True)
        px, py, pw, ph = PRODUCT_AT
        cls.plate_linear = np.full((HEIGHT, WIDTH, 3), 0.20, dtype=np.float32)
        appearance = _product_appearance(pw, ph)
        for frame in range(FRAMES):
            beauty_linear = cls.plate_linear.copy()
            # 左半阴影：亮度减半；右侧条带反射：加 0.05 线性能量
            beauty_linear[:, : WIDTH // 2] *= 0.5
            beauty_linear[:, 90:] += 0.05
            beauty_linear[py:py + ph, px:px + pw] = srgb_to_linear(appearance)
            save_rgb(linear_to_srgb(beauty_linear), cls.dirs["beauty"] / f"frame_{frame:04d}.png")
            save_rgb(linear_to_srgb(cls.plate_linear), cls.dirs["plate"] / f"frame_{frame:04d}.png")
            mask = np.zeros((HEIGHT, WIDTH, 4), dtype=np.uint8)
            mask[:, :, :3] = 255
            mask[py:py + ph, px:px + pw, 3] = 255
            Image.fromarray(mask, mode="RGBA").save(cls.dirs["mask"] / f"frame_{frame:04d}.png")
            Image.fromarray(np.zeros((HEIGHT, WIDTH, 4), dtype=np.uint8), mode="RGBA").save(
                cls.dirs["occlusion"] / f"frame_{frame:04d}.png")
        cls.out = cls.root / "layers_out"
        result = subprocess.run(
            [sys.executable, str(BUILD_LAYERS_SCRIPT),
             "--beauty", str(cls.dirs["beauty"]), "--plate", str(cls.dirs["plate"]),
             "--mask", str(cls.dirs["mask"]), "--occlusion", str(cls.dirs["occlusion"]),
             "--out", str(cls.out)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(REPO_ROOT),
        )
        cls.returncode = result.returncode
        cls.report = json.loads((cls.out / "layers_report.json").read_text(encoding="utf-8")) \
            if (cls.out / "layers_report.json").exists() else {}

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_build_passes(self) -> None:
        self.assertEqual(self.returncode, 0)
        self.assertTrue(self.report["passed"])
        self.assertEqual(self.report["frames"], FRAMES)

    def test_shadow_factor_math(self) -> None:
        """左半因子≈0.5、右半≈1.0；产品掩码内恒为 1.0。"""
        with Image.open(self.out / "shadow" / "frame_0000.png") as image:
            factor = np.asarray(image, dtype=np.float32) / 65535.0
        self.assertAlmostEqual(float(factor[5, 5]), 0.5, delta=0.03)
        self.assertAlmostEqual(float(factor[5, 80]), 1.0, delta=0.03)
        px, py = PRODUCT_AT[0], PRODUCT_AT[1]
        self.assertEqual(float(factor[py + 5, px + 5]), 1.0)
        self.assertGreater(self.report["shadow"]["coverage_mean"], 0.3)

    def test_reflection_energy_math(self) -> None:
        """右侧条带反射能量>0，其余区域≈0；产品掩码内恒为 0。"""
        frame = read_png(self.out / "reflection" / "frame_0000.png")
        expected = linear_to_srgb(np.float32(0.05))
        self.assertAlmostEqual(float(frame[5, 100, 0]), float(expected), delta=0.02)
        self.assertLess(float(frame[5, 80, 0]), 0.02)
        px, py = PRODUCT_AT[0], PRODUCT_AT[1]
        self.assertLess(float(frame[py + 5, px + 5].max()), 0.02)
        self.assertGreater(self.report["reflection"]["coverage_mean"], 0.05)

    def test_zero_occlusion_noted(self) -> None:
        """全零遮挡层如实注明（场景无遮挡物）。"""
        self.assertEqual(self.report["occlusion"]["coverage_mean"], 0.0)
        self.assertTrue(any("遮挡层全零" in note for note in self.report["notes"]))

    def test_missing_inputs_fail(self) -> None:
        """输入帧不足时如实失败。"""
        empty = self.root / "empty"
        empty.mkdir()
        result = subprocess.run(
            [sys.executable, str(BUILD_LAYERS_SCRIPT),
             "--beauty", str(empty), "--plate", str(self.dirs["plate"]),
             "--mask", str(self.dirs["mask"]), "--out", str(self.root / "out_empty")],
            capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(REPO_ROOT),
        )
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stdout)["passed"])


if __name__ == "__main__":
    unittest.main()
