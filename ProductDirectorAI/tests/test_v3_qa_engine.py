"""V3-04 QA 引擎与审批 hash 绑定的真实文件正负例。"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tests"))
sys.path.insert(0, str(PROJECT / "apps" / "api"))

import test_v3_strict_runtime as rt  # noqa: E402
from productdirector_api import strict_qa  # noqa: E402


SNAPSHOT = {
    "plan_id": "plan",
    "plan_contract_id": "contract",
    "owner_id": "owner-default",
    "product_version_id": "version",
    "product_review_id": "review",
    "fidelity_policy_id": "policy",
}
POLICY = {"mode": "STRICT"}
REVIEW = {"source_kind": "cad", "logo_regions": []}


def _spec(frame_count: int = 1) -> SimpleNamespace:
    return SimpleNamespace(frame_count=frame_count)


class StrictQaEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="pd-qa-engine-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _write_product_mask_pass(self, *, product_alpha: bool = True, mask_alpha: bool = True, pass_exr: bool = True) -> None:
        (self.root / "product").mkdir(parents=True, exist_ok=True)
        (self.root / "mask").mkdir(parents=True, exist_ok=True)
        (self.root / "passes").mkdir(parents=True, exist_ok=True)
        if product_alpha:
            rt._write_rgba(self.root / "product" / "frame_0001.png", rt.PRODUCT_SRGB)
        else:
            rt._write_rgb(self.root / "product" / "frame_0001.png", rt.PRODUCT_SRGB)
        if mask_alpha:
            rt._write_rgba(self.root / "mask" / "frame_0001.png", (0, 0, 0))
        else:
            rt._write_rgb(self.root / "mask" / "frame_0001.png", (0, 0, 0))
        if pass_exr:
            rt._write_pass_exr(self.root / "passes" / "frame_0001.exr")

    def test_missing_beauty_reference_is_fatal(self) -> None:
        self._write_product_mask_pass(pass_exr=False)
        report = strict_qa.run_strict_qa(
            self.root, {}, _spec(), SNAPSHOT, POLICY, REVIEW, strict_qa.DEFAULT_STRICT_QA_THRESHOLD_SET
        )
        self.assertEqual(report["status"], "FAIL", report)
        self.assertFalse(report["passed"], report)
        self.assertTrue(any("Beauty" in item for item in report["fatal_failures"]), report)

    def test_rgb_product_without_alpha_is_rejected(self) -> None:
        self._write_product_mask_pass(product_alpha=False)
        report = strict_qa.run_strict_qa(
            self.root, {}, _spec(), SNAPSHOT, POLICY, REVIEW, strict_qa.DEFAULT_STRICT_QA_THRESHOLD_SET
        )
        self.assertEqual(report["status"], "FAIL", report)
        self.assertTrue(any("可信 Alpha" in item for item in report["fatal_failures"]), report)

    def test_mask_without_alpha_raises(self) -> None:
        path = self.root / "mask" / "frame_0001.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(np.zeros((8, 8, 3), dtype=np.uint8), mode="RGB").save(path)
        with self.assertRaises(ValueError):
            strict_qa.read_mask(path)

    def test_beauty_selector_rejects_normal_only_planes(self) -> None:
        planes = {
            "normal.X": np.zeros((8, 8), dtype=np.float32),
            "normal.Y": np.zeros((8, 8), dtype=np.float32),
            "normal.Z": np.ones((8, 8), dtype=np.float32),
        }
        self.assertIsNone(strict_qa._pick_beauty(planes))

    def test_self_reported_media_report_stays_not_verified(self) -> None:
        self._write_product_mask_pass()
        fake = {"trusted": True, "passed": True, "status": "PASS"}
        report = strict_qa.run_strict_qa(
            self.root, {}, _spec(), SNAPSHOT, POLICY, REVIEW,
            strict_qa.DEFAULT_STRICT_QA_THRESHOLD_SET, media_report=fake,
        )
        self.assertEqual(report["checks"]["encoded_media"]["status"], "NOT_VERIFIED", report)

    def test_asset_hash_mismatch_is_fatal(self) -> None:
        self._write_product_mask_pass()
        asset = self.root / "asset.glb"
        asset.write_bytes(b"approved-asset-v1")
        evidence = {
            "producer_control": "CONTROLLED_WORKER",
            "asset_sha256": hashlib.sha256(b"tampered-asset").hexdigest(),
        }
        report = strict_qa.run_strict_qa(
            self.root, {}, _spec(), SNAPSHOT, POLICY, REVIEW,
            strict_qa.DEFAULT_STRICT_QA_THRESHOLD_SET,
            controlled_evidence=evidence, asset_path=asset,
        )
        self.assertEqual(report["checks"]["asset_hash"]["status"], "FAIL", report)
        self.assertEqual(report["status"], "FAIL", report)

    def test_positive_files_are_not_verified_not_falsely_pass(self) -> None:
        self._write_product_mask_pass()
        report = strict_qa.run_strict_qa(
            self.root, {}, _spec(), SNAPSHOT, POLICY, REVIEW, strict_qa.DEFAULT_STRICT_QA_THRESHOLD_SET
        )
        self.assertFalse(report["passed"], report)
        self.assertEqual(report["status"], "NOT_VERIFIED", report)
        self.assertFalse(report["fatal_failures"], report)


if __name__ == "__main__":
    unittest.main()
