"""verify_strict_multiframe_output.py 的真实 OpenEXR/PNG 正负测试（云端证据版）。"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image
import OpenEXR


PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "scripts" / "verify_strict_multiframe_output.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("verify_strict_multiframe_output", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load_module()


def _write_exr(path: Path, name: str, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, dict):
        payload = {key: value.astype(np.float32) for key, value in data.items()}
    else:
        payload = {name: data.astype(np.float32)}
    OpenEXR.File({}, payload).write(str(path))


class VerifyStrictMultiframeOutputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pd-p01b-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.beauty = self.tmp / "passes" / "beauty"
        self.alpha = self.tmp / "passes" / "alpha"
        self.depth = self.tmp / "passes" / "depth"
        self.normal = self.tmp / "passes" / "normal"
        self.product = self.tmp / "product"
        self.mask = self.tmp / "mask"
        self.display = self.tmp / "display"
        for folder in (self.beauty, self.alpha, self.depth, self.normal, self.product, self.mask, self.display):
            folder.mkdir(parents=True, exist_ok=True)
        self.metadata = self.tmp / "metadata.json"
        self.input_file = self.tmp / "input.glb"
        self.input_file.write_bytes(b"p01b-test-input-glb")
        self.calibration_root = self.tmp / "calibration"
        self.calibration_root.mkdir(parents=True, exist_ok=True)
        for name in ("depth_cal.json", "normal_cal.json", "product_mapping.json", "color_config.ocio"):
            (self.calibration_root / name).write_bytes(f"p01b-calibration-{name}".encode("utf-8"))

    def _write_frame(self, frame: int, *, display_offset: int = 0) -> None:
        h = w = 8
        beauty = np.zeros((h, w, 4), dtype=np.float32)
        beauty[..., 0] = 0.2
        beauty[..., 1] = 0.3
        beauty[..., 2] = 0.4
        beauty[..., 3] = 0.75
        _write_exr(self.beauty / f"frame_{frame:04d}.exr", "beauty", beauty)
        _write_exr(self.alpha / f"frame_{frame:04d}.exr", "alpha.V", np.full((h, w), 0.75, dtype=np.float32))
        _write_exr(self.depth / f"frame_{frame:04d}.exr", "depth.V", np.full((h, w), 4.0, dtype=np.float32))
        _write_exr(
            self.normal / f"frame_{frame:04d}.exr",
            "normal",
            {
                "normal.X": np.full((h, w), 0.0, dtype=np.float32),
                "normal.Y": np.full((h, w), 0.0, dtype=np.float32),
                "normal.Z": np.full((h, w), 1.0, dtype=np.float32),
            },
        )

        product = np.zeros((h, w, 4), dtype=np.uint8)
        product[..., 0] = 124
        product[..., 1] = 149
        product[..., 2] = 170
        product[..., 3] = 191
        Image.fromarray(product, "RGBA").save(self.product / f"frame_{frame:04d}.png")
        Image.fromarray(product, "RGBA").save(self.mask / f"frame_{frame:04d}.png")
        display = product.copy()
        display[..., 0] = np.clip(display[..., 0].astype(int) + display_offset, 0, 255).astype(np.uint8)
        Image.fromarray(display, "RGBA").save(self.display / f"frame_{frame:04d}.png")

    def _write_metadata(self) -> None:
        self.metadata.write_text(json.dumps({
            "depth": {"unit": "meters", "space": "camera_distance", "no_hit_sentinel": 1e10, "source": "field_calibration_NOT_YET"},
            "normal": {"space": "camera", "encoding": "signed_float"},
            "product_id": {"stable_id": "product_mesh_uuid_NOT_YET", "mapping_source": "asset_manifest_NOT_YET"},
            "color": {"scene_linear": "Blender EXR linear", "display_transform": "AgX", "product_display": "sRGB"},
            "calibration_evidence": {
                "bound_render_ledger_sha256": "0" * 64,
                "bound_input_sha256": "0" * 64,
                "source_files": {"depth": "depth_cal.json", "normal": "normal_cal.json", "product_mapping": "product_mapping.json", "color_ocio": "color_config.ocio"},
                "source_sha256": {key: "0" * 64 for key in ("depth", "normal", "product_mapping", "color_ocio")},
            },
        }, ensure_ascii=False), encoding="utf-8")

    def _write_valid_metadata(self) -> None:
        def sha(path):
            return MODULE._sha256_file(path)
        source_files = {
            "depth": "depth_cal.json",
            "normal": "normal_cal.json",
            "product_mapping": "product_mapping.json",
            "color_ocio": "color_config.ocio",
        }
        source_hashes = {key: sha(self.calibration_root / rel) for key, rel in source_files.items()}
        ledger = {k: v for k, v in MODULE._file_ledger(self.tmp).items() if Path(k).name != "metadata.json"}
        ledger_sha = hashlib.sha256(json.dumps(ledger, sort_keys=True).encode("utf-8")).hexdigest()
        self.metadata.write_text(json.dumps({
            "depth": {"unit": "meters", "space": "camera_distance", "no_hit_sentinel": 1e10, "source": "calibration_artifact_v1"},
            "normal": {"space": "camera", "encoding": "signed_float"},
            "product_id": {"stable_id": "product-0001", "mapping_source": "asset_manifest_v1.json"},
            "color": {"scene_linear": "Blender EXR linear", "display_transform": "AgX", "product_display": "sRGB"},
            "calibration_evidence": {
                "bound_render_ledger_sha256": ledger_sha,
                "bound_input_sha256": sha(self.input_file),
                "source_files": source_files,
                "source_sha256": source_hashes,
            },
        }, ensure_ascii=False), encoding="utf-8")

    def _run(self, **overrides):
        kwargs = dict(
            root=self.tmp,
            expected_frames=1,
            width=8,
            height=8,
            beauty_dir=self.beauty,
            alpha_dir=self.alpha,
            depth_dir=self.depth,
            normal_dir=self.normal,
            product_dir=self.product,
            mask_dir=self.mask,
            display_dir=self.display,
            pass_mask_dir=None,
            metadata_file=self.metadata,
            input_file=self.input_file,
            calibration_root=self.calibration_root,
        )
        kwargs.update(overrides)
        return MODULE.run_verification(**kwargs)

    def test_placeholder_metadata_stays_not_verified(self) -> None:
        self._write_frame(1)
        self._write_metadata()
        result = self._run()
        self.assertEqual(result["status"], "PASS", result)
        self.assertTrue(result["core_mae"]["pass"])
        self.assertTrue(result["alpha"]["pass"])
        self.assertEqual(result["semantic_contract_status"], "NOT_VERIFIED")
        self.assertFalse(result["evidence_bound"])
        self.assertFalse(result["release_eligible"])

    def test_require_semantics_fails_for_placeholder_metadata(self) -> None:
        self._write_frame(1)
        self._write_metadata()
        result = self._run(require_semantics=True)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["semantic_contract_status"], "NOT_VERIFIED")
        self.assertFalse(result["evidence_bound"])
        self.assertTrue(any("语义合同未通过校验" in item for item in result["failures"]))

    def test_wrong_bound_hash_stays_not_verified(self) -> None:
        self._write_frame(1)
        self._write_valid_metadata()
        payload = json.loads(self.metadata.read_text(encoding="utf-8"))
        payload["calibration_evidence"]["bound_render_ledger_sha256"] = "1" * 64
        self.metadata.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        result = self._run(require_semantics=True)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["semantic_contract_status"], "NOT_VERIFIED")

    def test_bound_calibration_evidence_stays_evidence_bound(self) -> None:
        self._write_frame(1)
        self._write_valid_metadata()
        result = self._run()
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(result["semantic_contract_status"], "EVIDENCE_BOUND")
        self.assertTrue(result["evidence_bound"])
        self.assertFalse(result["semantic_verified"])
        self.assertFalse(result["release_eligible"])

    def test_require_semantics_fails_even_for_evidence_bound(self) -> None:
        self._write_frame(1)
        self._write_valid_metadata()
        result = self._run(require_semantics=True)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["semantic_contract_status"], "EVIDENCE_BOUND")
        self.assertTrue(any("不升格" in item for item in result["failures"]))

    def test_missing_metadata_is_not_verified(self) -> None:
        self._write_frame(1)
        result = self._run(metadata_file=None)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["semantic_contract_status"], "NOT_VERIFIED")
        self.assertFalse(result["release_eligible"])

    def test_require_semantics_fails_without_metadata(self) -> None:
        self._write_frame(1)
        result = self._run(metadata_file=None, require_semantics=True)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("语义合同未通过校验" in item or "metadata 文件缺失" in item for item in result["failures"]))

    def test_missing_product_frame_fails(self) -> None:
        self._write_frame(1)
        (self.product / "frame_0001.png").unlink()
        result = self._run()
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("product 帧集与合同不一致" in item for item in result["failures"]))

    def test_tampered_display_core_fails_contract(self) -> None:
        self._write_frame(1, display_offset=10)
        self._write_metadata()
        result = self._run()
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["core_mae"]["pass"])
        self.assertTrue(any("core MAE" in item for item in result["failures"]))

    def test_corrupt_exr_fails_closed(self) -> None:
        self._write_frame(1)
        p = self.alpha / "frame_0001.exr"
        data = p.read_bytes()
        p.write_bytes(data[: len(data) // 2])
        result = self._run()
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("alpha EXR 读取失败" in item or "alpha EXR 尺寸" in item for item in result["failures"]))

    def test_extra_mask_frame_fails(self) -> None:
        self._write_frame(1)
        shutil.copy2(self.mask / "frame_0001.png", self.mask / "frame_0002.png")
        result = self._run(expected_frames=1)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("mask 帧集与合同不一致" in item for item in result["failures"]))

    def test_edge_only_diff_is_diagnostic_not_contract_failure(self) -> None:
        # 8x8 可见区全为产品，2px 腐蚀后只剩核心；在边缘像素改 display。
        self._write_frame(1)
        display = np.asarray(Image.open(self.display / "frame_0001.png").convert("RGBA")).copy()
        display[1, 1, 0] = np.clip(display[1, 1, 0] + 12, 0, 255)
        Image.fromarray(display, "RGBA").save(self.display / "frame_0001.png")
        result = self._run()
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["core_mae"]["pass"])
        self.assertGreater(result["edge_diagnostic"]["max_abs_diff"], 0)
        self.assertGreater(result["max_diagnostic"]["display_product_rgb_max_abs_diff"], 0)
        self.assertFalse(result["edge_diagnostic"]["is_contract_gate"])


if __name__ == "__main__":
    unittest.main()
