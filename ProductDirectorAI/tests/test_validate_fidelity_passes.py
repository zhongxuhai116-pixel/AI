from __future__ import annotations

import importlib.util
import io
import json
import shutil
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from typing import Dict
from unittest import mock

import numpy as np
import OpenEXR
from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATE_PATH = REPO_ROOT / "blender" / "scripts" / "validate_fidelity_passes.py"

spec = importlib.util.spec_from_file_location("validate_fidelity_passes", VALIDATE_PATH)
validate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validate)  # type: ignore[union-attr]


class ValidateFidelityRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = (REPO_ROOT / "var" / "test-validate-fidelity").resolve()
        self.base.mkdir(parents=True, exist_ok=True)
        self.workdir = (self.base / f"case-{uuid.uuid4().hex}").resolve()
        self.workdir.mkdir()
        self.passes = self.workdir / "passes"
        self.passes.mkdir()
        self.payloads: Dict[Path, dict] = {}
        self.corrupt: set[Path] = set()

    def tearDown(self) -> None:
        resolved = self.workdir.resolve()
        if resolved.parent == self.base and resolved.name.startswith("case-"):
            shutil.rmtree(resolved, ignore_errors=True)

    def _make_frame_payload(
        self,
        *,
        width: int = 8,
        height: int = 8,
        alpha_rect=(1, 1, 5, 5),
        missing_normal: bool = False,
        zero_normal: bool = False,
        missing_alpha: bool = False,
        missing_depth: bool = False,
        nonfinite_depth: bool = False,
        nonfinite_normal: bool = False,
    ) -> Dict[str, object]:
        x0, y0, x1, y1 = alpha_rect
        alpha = np.zeros((height, width), dtype=np.float32)
        alpha[y0:y1, x0:x1] = 1.0

        depth = np.zeros((height, width), dtype=np.float32)
        depth[y0:y1, x0:x1] = 3.0
        if nonfinite_depth:
            depth[y0, x0] = np.nan

        normal_x = np.zeros((height, width), dtype=np.float32)
        normal_y = np.zeros((height, width), dtype=np.float32)
        normal_z = np.ones((height, width), dtype=np.float32)
        if zero_normal:
            normal_x[:] = 0.0
            normal_y[:] = 0.0
            normal_z[:] = 0.0
        if nonfinite_normal:
            normal_x[y0, x0] = np.nan

        beauty = np.zeros((height, width, 4), dtype=np.float32)
        beauty[:, :, :3] = 0.2
        beauty[:, :, 3] = 1.0

        planes = {
            "beauty": beauty,
            "alpha": alpha[:, :, None],
            "depth": depth[:, :, None],
            "normal.X": normal_x[:, :, None],
            "normal.Y": normal_y[:, :, None],
            "normal.Z": normal_z[:, :, None],
            "unrelated.V": np.zeros((height, width, 1), dtype=np.float32),
        }

        names = ["beauty", "alpha", "depth", "normal.X", "normal.Y", "normal.Z"]
        if missing_normal:
            names = ["beauty", "alpha", "depth", "unrelated.V"]
        if missing_alpha:
            names = [name for name in names if name != "alpha"]
        if missing_depth:
            names = [name for name in names if name != "depth"]
        return {"names": names, "planes": {name: planes[name] for name in names}, "width": width, "height": height}

    def _register_exr(self, path: Path, payload: dict) -> None:
        payload_data = {
            "names": payload["names"],
            "planes": payload["planes"],
            "width": payload["width"],
            "height": payload["height"],
        }
        path.write_bytes(b"EXR")
        self.payloads[path] = payload_data

    def _write_mask(self, path: Path, *, rect=(1, 1, 5, 5), size=(8, 8)) -> None:
        image = Image.new("RGBA", size, (0, 0, 0, 0))
        x0, y0, x1, y1 = rect
        for y in range(y0, y1):
            for x in range(x0, x1):
                image.putpixel((x, y), (0, 0, 0, 255))
        image.save(path)

    def _build_dataset(self, *, frames: int, start_frame: int = 1,
                       alpha_rect=(1, 1, 5, 5), mask_rect=(1, 1, 5, 5),
                       missing_normal=False, zero_normal=False,
                       missing_alpha=False, missing_depth=False,
                       nonfinite_depth=False, nonfinite_normal=False) -> None:
        for index in range(start_frame, start_frame + frames):
            exr = self.passes / f"frame_{index:04d}.exr"
            payload = self._make_frame_payload(
                alpha_rect=alpha_rect,
                missing_normal=missing_normal,
                zero_normal=zero_normal,
                missing_alpha=missing_alpha,
                missing_depth=missing_depth,
                nonfinite_depth=nonfinite_depth,
                nonfinite_normal=nonfinite_normal,
            )
            self._register_exr(exr, payload)
            mask_file = self.passes / "mask" / f"frame_{index:04d}.png"
            mask_file.parent.mkdir(parents=True, exist_ok=True)
            self._write_mask(mask_file, rect=mask_rect)

    def _build_gap_dataset(self, indices=(1, 2, 4, 5)) -> None:
        for index in indices:
            exr = self.passes / f"frame_{index:04d}.exr"
            payload = self._make_frame_payload()
            self._register_exr(exr, payload)
            mask_file = self.passes / "mask" / f"frame_{index:04d}.png"
            mask_file.parent.mkdir(parents=True, exist_ok=True)
            self._write_mask(mask_file)

    def _build_misaligned_dataset(self, *, frames: int) -> None:
        for index in range(1, frames + 1):
            exr = self.passes / f"frame_{index:04d}.exr"
            payload = self._make_frame_payload(alpha_rect=(1, 1, 5, 5))
            self._register_exr(exr, payload)
            mask_file = self.passes / "mask" / f"frame_{index:04d}.png"
            mask_file.parent.mkdir(parents=True, exist_ok=True)
            self._write_mask(mask_file, rect=(3, 3, 7, 7))

    def _build_real_exr_dataset(self, *, frames: int = 2) -> None:
        width, height = 8, 8
        for index in range(1, frames + 1):
            alpha = np.zeros((height, width), dtype=np.float32)
            alpha[1:5, 1:5] = 1.0
            depth = np.zeros((height, width), dtype=np.float32)
            depth[1:5, 1:5] = 3.0
            beauty = {
                "beauty.R": np.full((height, width), 0.2, dtype=np.float32),
                "beauty.G": np.full((height, width), 0.3, dtype=np.float32),
                "beauty.B": np.full((height, width), 0.4, dtype=np.float32),
                "beauty.A": np.ones((height, width), dtype=np.float32),
            }
            for channel, data in {
                "beauty": beauty,
                "alpha": {"alpha.V": alpha},
                "depth": {"depth.V": depth},
                "normal": {
                    "normal.X": np.zeros((height, width), dtype=np.float32),
                    "normal.Y": np.zeros((height, width), dtype=np.float32),
                    "normal.Z": np.ones((height, width), dtype=np.float32),
                },
            }.items():
                exr = self.passes / channel / f"frame_{index:04d}.exr"
                exr.parent.mkdir(parents=True, exist_ok=True)
                OpenEXR.File({}, data).write(str(exr))
            mask_file = self.passes / "mask" / f"frame_{index:04d}.png"
            mask_file.parent.mkdir(parents=True, exist_ok=True)
            self._write_mask(mask_file)

    def _run_real(self, frames: int, start_frame: int = 1) -> tuple[int, dict]:
        with io.StringIO() as buf, mock.patch(
            "sys.argv",
            [
                "validate_fidelity_passes",
                "--passes",
                str(self.passes),
                "--frames",
                str(frames),
                "--start-frame",
                str(start_frame),
            ],
        ), redirect_stdout(buf):
            code = validate.main()
            payload = buf.getvalue()
        report = json.loads(payload.split("FIDELITY_PASSES ", 1)[1])
        return code, report

    def _run(self, frames: int, start_frame: int = 1) -> tuple[int, dict]:
        def fake_read_exr(path: Path):
            if path in self.corrupt:
                raise RuntimeError("corrupt exr")
            return self.payloads[path]

        with mock.patch.object(validate, "read_exr", side_effect=fake_read_exr):
            with io.StringIO() as buf, mock.patch(
                "sys.argv",
                [
                    "validate_fidelity_passes",
                    "--passes",
                    str(self.passes),
                    "--frames",
                    str(frames),
                    "--start-frame",
                    str(start_frame),
                ],
            ), redirect_stdout(buf):
                code = validate.main()
                payload = buf.getvalue()

        report = json.loads(payload.split("FIDELITY_PASSES ", 1)[1])
        return code, report

    def test_real_openexr_dataset_should_pass(self) -> None:
        self._build_real_exr_dataset(frames=2)
        code, report = self._run_real(2)
        self.assertEqual(code, 0, report["failures"])
        self.assertTrue(report["passed"])
        self.assertEqual(len(report["frames"]), 2)

    def test_valid_dataset_should_pass(self) -> None:
        self._build_dataset(frames=5)
        code, report = self._run(5)
        self.assertEqual(code, 0)
        self.assertTrue(report["passed"])
        self.assertEqual(len(report["frames"]), 5)

    def test_q04_shifted_mask_rejected(self) -> None:
        self._build_misaligned_dataset(frames=4)
        code, report = self._run(4)
        self.assertEqual(code, 1)
        self.assertFalse(report["passed"])
        self.assertTrue(any("IoU 过低" in item for item in report["failures"]))

    def test_q05_unsampled_fault_rejected(self) -> None:
        self._build_dataset(frames=5)
        self.corrupt.add(self.passes / "frame_0002.exr")
        code, report = self._run(5)
        self.assertEqual(code, 1)
        self.assertFalse(report["passed"])
        self.assertTrue(any("读取 EXR 失败" in item for item in report["failures"]))

    def test_q06_missing_normal_components_rejected(self) -> None:
        self._build_dataset(frames=3, missing_normal=True)
        code, report = self._run(3)
        self.assertEqual(code, 1)
        self.assertFalse(report["passed"])
        self.assertTrue(any("法线通道不完整" in item for item in report["failures"]))

    def test_zero_normals_rejected(self) -> None:
        self._build_dataset(frames=3, zero_normal=True)
        code, report = self._run(3)
        self.assertEqual(code, 1)
        self.assertFalse(report["passed"])
        self.assertTrue(any("法线模长异常" in item or "法线包含非有限值" in item for item in report["failures"]))

    def test_missing_alpha_rejected(self) -> None:
        self._build_dataset(frames=3, missing_alpha=True)
        code, report = self._run(3)
        self.assertEqual(code, 1)
        self.assertTrue(any("缺少 alpha 通道" in item for item in report["failures"]))

    def test_missing_depth_rejected(self) -> None:
        self._build_dataset(frames=3, missing_depth=True)
        code, report = self._run(3)
        self.assertEqual(code, 1)
        self.assertTrue(any("缺少 depth 通道" in item for item in report["failures"]))

    def test_nonfinite_depth_rejected(self) -> None:
        self._build_dataset(frames=3, nonfinite_depth=True)
        code, report = self._run(3)
        self.assertEqual(code, 1)
        self.assertTrue(any("depth 包含非有限值" in item or "产品区 depth 包含非有限值" in item for item in report["failures"]))

    def test_nonfinite_normal_rejected(self) -> None:
        self._build_dataset(frames=3, nonfinite_normal=True)
        code, report = self._run(3)
        self.assertEqual(code, 1)
        self.assertTrue(any("法线分量包含非有限值" in item or "法线包含非有限值" in item for item in report["failures"]))

    def test_frame_gap_rejected(self) -> None:
        self._build_gap_dataset()
        code, report = self._run(5, start_frame=1)
        self.assertEqual(code, 1)
        self.assertFalse(report["passed"])
        self.assertTrue(any("缺少帧" in item for item in report["failures"]))

    def test_duplicate_mask_rejected(self) -> None:
        self._build_dataset(frames=3)
        self._write_mask(self.passes / "mask" / "frame_0001_extra.png")
        code, report = self._run(3)
        self.assertEqual(code, 1)
        self.assertTrue(any("存在重复帧" in item for item in report["failures"]))


if __name__ == "__main__":
    unittest.main()
