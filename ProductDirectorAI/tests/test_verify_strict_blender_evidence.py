"""verify_strict_blender_evidence.py 的真实 OpenEXR/PNG 门控正负测试。"""
from __future__ import annotations

import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

try:
    import OpenEXR
except Exception:  # pragma: no cover
    OpenEXR = None


PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "scripts" / "verify_strict_blender_evidence.py"


def _load_probe_module():
    spec = importlib.util.spec_from_file_location("verify_strict_blender_evidence_probe", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load_probe_module()


class VerifyStrictBlenderEvidenceGateTests(unittest.TestCase):
    @unittest.skipIf(OpenEXR is None, "OpenEXR not available")
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pd-probe-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.beauty_dir = self.tmp / "passes" / "beauty"
        self.alpha_dir = self.tmp / "passes" / "alpha"
        self.product_dir = self.tmp / "strict" / "product"
        self.mask_dir = self.tmp / "strict" / "mask"
        self.display_dir = self.tmp / "display"
        for folder in (self.beauty_dir, self.alpha_dir, self.product_dir, self.mask_dir, self.display_dir):
            folder.mkdir(parents=True, exist_ok=True)

    def _write_frame(self, frame: int = 1, *, beauty_alpha: float = 0.75, product_rgba=(124, 149, 170, 191), display_rgb=(124, 149, 170)) -> None:
        height, width = 8, 8
        beauty = np.zeros((height, width, 4), dtype=np.float32)
        beauty[:, :, 0] = 0.2
        beauty[:, :, 1] = 0.3
        beauty[:, :, 2] = 0.4
        beauty[:, :, 3] = beauty_alpha
        OpenEXR.File({}, {"beauty": beauty}).write(str(self.beauty_dir / f"frame_{frame:04d}.exr"))
        alpha = np.full((height, width), beauty_alpha, dtype=np.float32)
        OpenEXR.File({}, {"alpha": alpha}).write(str(self.alpha_dir / f"frame_{frame:04d}.exr"))

        rgba = np.zeros((height, width, 4), dtype=np.uint8)
        rgba[:, :, 0], rgba[:, :, 1], rgba[:, :, 2], rgba[:, :, 3] = product_rgba
        Image.fromarray(rgba, mode="RGBA").save(self.product_dir / f"frame_{frame:04d}.png")
        Image.fromarray(rgba, mode="RGBA").save(self.mask_dir / f"frame_{frame:04d}.png")
        display = np.zeros((height, width, 4), dtype=np.uint8)
        display[:, :, 0], display[:, :, 1], display[:, :, 2] = display_rgb
        display[:, :, 3] = 255
        Image.fromarray(display, mode="RGBA").save(self.display_dir / f"frame_{frame:04d}.png")

    def _run(self, **overrides):
        kwargs = {
            "beauty_dir": self.beauty_dir,
            "alpha_dir": self.alpha_dir,
            "product_dir": self.product_dir,
            "mask_dir": self.mask_dir,
            "expected_frames": 1,
            "width": 8,
            "height": 8,
            "display_dir": self.display_dir,
        }
        kwargs.update(overrides)
        return MODULE.run_probe(**kwargs)

    def test_missing_display_dir_must_fail(self) -> None:
        self._write_frame()
        result = self._run(display_dir=None)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("display" in item for item in result["failures"]), result)

    def test_tampered_display_rgb_must_fail(self) -> None:
        self._write_frame(display_rgb=(230, 230, 230))
        result = self._run()
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("display PNG 与 product PNG" in item for item in result["failures"]), result)

    def test_normal_display_alpha_within_tolerance_passes(self) -> None:
        self._write_frame()
        result = self._run()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["display_product_rgb_status"], "PASS")
        self.assertEqual(result["alpha_consistency_status"], "PASS")
        self.assertEqual(result["beauty_product_rgb_status"], "NOT_VERIFIED")

    def test_alpha_out_of_tolerance_must_fail(self) -> None:
        self._write_frame(beauty_alpha=1.0, product_rgba=(124, 149, 170, 0))
        result = self._run()
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("Alpha 最大绝对差" in item or "Alpha 不是同帧一致" in item for item in result["failures"]), result)


if __name__ == "__main__":
    unittest.main()
