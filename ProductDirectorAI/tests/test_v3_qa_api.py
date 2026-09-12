"""V3-04 QA 阈值/报告/审批 hash 绑定的 API 负例。"""
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tests"))
sys.path.insert(0, str(PROJECT / "apps" / "api"))

import test_job_control as fixtures  # noqa: E402
import test_v3_strict_runtime as rt  # noqa: E402

main = fixtures.main

PASS_QA = {
    "schema_version": "1.0",
    "status": "PASS",
    "passed": True,
    "fatal_failures": [],
    "not_verified": [],
    "checks": {
        "frame_completeness": {"status": "PASS"},
        "color_core": {"status": "PASS", "max_mean_abs_diff": 0.0},
        "edge": {"status": "PASS", "max_mean_abs_diff": 0.0},
        "contour": {"status": "PASS", "min_iou": 1.0},
        "logo": {"status": "PASS", "max_mean_abs_diff": 0.0, "min_mask_coverage": 1.0},
        "product_id": {"status": "PASS"},
        "dimension": {"status": "PASS"},
        "encoded_media": {"status": "PASS", "passed": True},
        "asset_hash": {"status": "PASS", "asset_sha256": "expected"},
    },
    "frames": [],
    "thresholds": dict(main.strict_qa.DEFAULT_STRICT_QA_THRESHOLD_SET),
    "threshold_set_id": main.strict_qa.DEFAULT_STRICT_QA_THRESHOLD_SET["threshold_set_id"],
    "threshold_set_version": main.strict_qa.DEFAULT_STRICT_QA_THRESHOLD_SET["threshold_set_version"],
    "policy_mode": "STRICT",
    "review_source_kind": "cad",
    "frame_count": rt.FRAME_COUNT,
}

FAIL_QA = dict(PASS_QA)
FAIL_QA["status"] = "FAIL"
FAIL_QA["passed"] = False
FAIL_QA["fatal_failures"] = ["Strict QA 必需检查失败"]
FAIL_QA["not_verified"] = []


class V3QaApiTests(unittest.TestCase):
    _create_plan = fixtures.JobControlAcceptanceTests._create_plan
    _create_asset = fixtures.JobControlAcceptanceTests._create_asset

    def setUp(self) -> None:
        fixtures.JobControlAcceptanceTests.setUp(self)

    tearDown = fixtures.JobControlAcceptanceTests.tearDown

    def _current_contract_id(self, plan_id: str) -> str:
        with main.connect() as db:
            row = db.execute(
                "SELECT id FROM plan_contracts WHERE plan_id = ? ORDER BY version DESC, created_at DESC LIMIT 1",
                (plan_id,),
            ).fetchone()
        return row["id"]

    def _review(self, version_id: str) -> dict:
        response = self.client.post(
            f"/api/v1/product-versions/{version_id}/reviews",
            json={
                "decision": "APPROVED",
                "source_kind": "cad",
                "verified_dimensions": {"width": 164.0, "height": 138.0},
                "logo_regions": [{"x": 0.4, "y": 0.35, "width": 0.2, "height": 0.1, "label": "brand"}],
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def _policy(self, version_id: str) -> dict:
        response = self.client.post(
            f"/api/v1/product-versions/{version_id}/fidelity-policies",
            json={
                "mode": "STRICT",
                "protected_regions": [{"x": 0.2, "y": 0.3, "width": 0.4, "height": 0.4, "label": "logo"}],
                "allowed_operations": ["color_transform", "edge_composite"],
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def _create_strict_run(self) -> tuple[str, dict, dict]:
        plan = self._create_plan()
        approved = self.client.post(f"/api/v1/plans/{plan['id']}/approve", json={"approved": True})
        self.assertEqual(approved.status_code, 200, approved.text)
        version_id = approved.json()["product_version_id"]
        contract_id = self._current_contract_id(plan["id"])
        review = self._review(version_id)
        policy = self._policy(version_id)
        with patch("productdirector_api.main.execute_job"):
            response = self.client.post(
                "/api/v1/runs",
                json={
                    "plan_id": plan["id"],
                    "require_fidelity_snapshot": True,
                    "plan_contract_id": contract_id,
                    "product_review_id": review["id"],
                    "fidelity_policy_id": policy["id"],
                },
            )
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()["job_id"], review, policy

    def _write_strict_inputs(self, job_id: str) -> None:
        strict_root = main.RUNS / job_id / "strict"
        for folder in ("passes", "product", "mask", "background"):
            (strict_root / folder).mkdir(parents=True, exist_ok=True)
        (strict_root / "passes" / "mask").mkdir(parents=True, exist_ok=True)
        for index in range(1, rt.FRAME_COUNT + 1):
            name = f"frame_{index:04d}.png"
            rt._write_rgba(strict_root / "product" / name, rt.PRODUCT_SRGB)
            rt._write_rgba(strict_root / "mask" / name, (0, 0, 0))
            rt._write_rgba(strict_root / "passes" / "mask" / name, (0, 0, 0))
            rt._write_rgb(strict_root / "background" / name, (10, 10, 10))
            rt._write_pass_exr(strict_root / "passes" / f"frame_{index:04d}.exr")

    def _manifest_hash(self, job_id: str) -> str:
        manifest = self.client.get(f"/api/v1/jobs/{job_id}/manifest").json()
        return hashlib.sha256(main._canonical_json_text(manifest).encode("utf-8")).hexdigest()

    def _report_id(self, job_id: str) -> str:
        with main.connect() as db:
            row = db.execute(
                "SELECT id FROM qa_reports WHERE job_id = ? ORDER BY created_at DESC LIMIT 1",
                (job_id,),
            ).fetchone()
        self.assertIsNotNone(row)
        return row["id"]

    def _run_id(self, job_id: str) -> str:
        with main.connect() as db:
            row = db.execute(
                "SELECT id FROM runs WHERE job_id = ? ORDER BY created_at DESC LIMIT 1",
                (job_id,),
            ).fetchone()
        self.assertIsNotNone(row)
        return row["id"]

    def _approve(self, job_id: str) -> tuple[str, str]:
        report_id = self._report_id(job_id)
        manifest_hash = self._manifest_hash(job_id)
        response = self.client.post(
            f"/api/v1/qa-reports/{report_id}/decisions",
            json={"decision": "APPROVED", "manifest_hash": manifest_hash},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return report_id, manifest_hash

    def _first_file(self, root: Path) -> Path:
        files = [path for path in sorted(root.rglob("*")) if path.is_file()]
        self.assertTrue(files, str(root))
        return files[0]

    def _asset_file(self, job_id: str) -> Path:
        with main.connect() as db:
            row = db.execute(
                "SELECT * FROM assets WHERE id = (SELECT asset_id FROM jobs WHERE id = ?)",
                (job_id,),
            ).fetchone()
        self.assertIsNotNone(row)
        return Path(main.resolve_asset_path(main.row_to_dict(row)))

    def test_threshold_set_version_is_frozen(self) -> None:
        response = self.client.post(
            "/api/v1/qa-threshold-sets",
            json={"threshold_set_version": "2026.09.2", "thresholds": {"color_core_mae_max": 0.001}},
        )
        self.assertEqual(response.status_code, 201, response.text)
        duplicate = self.client.post(
            "/api/v1/qa-threshold-sets",
            json={"threshold_set_version": "2026.09.2", "thresholds": {"color_core_mae_max": 0.001}},
        )
        self.assertEqual(duplicate.status_code, 409, duplicate.text)

    def test_threshold_set_rejects_loosened_or_out_of_range(self) -> None:
        cases = [
            ("2026.09.loose-color", {"color_core_mae_max": 1.0}),
            ("2026.09.loose-iou", {"contour_iou_min": 0.0}),
            ("2026.09.bad-binary", {"min_mask_binary_ratio": 1.5}),
            ("2026.09.loose-dimension", {"verified_dimension_max_relative_error": 2.0}),
        ]
        for version, thresholds in cases:
            with self.subTest(version=version):
                response = self.client.post(
                    "/api/v1/qa-threshold-sets",
                    json={"threshold_set_version": version, "thresholds": thresholds},
                )
                self.assertEqual(response.status_code, 422, response.text)

    @patch("productdirector_api.main.strict_qa.run_strict_qa", return_value=PASS_QA)
    def test_decision_requires_exact_current_manifest_hash(self, *mocks) -> None:
        job_id, _, _ = self._create_strict_run()
        self._write_strict_inputs(job_id)
        main.execute_job(job_id)
        with main.connect() as db:
            report_id = db.execute("SELECT id FROM qa_reports WHERE job_id = ? ORDER BY created_at DESC LIMIT 1", (job_id,)).fetchone()["id"]
        manifest_hash = self._manifest_hash(job_id)
        bad = self.client.post(
            f"/api/v1/qa-reports/{report_id}/decisions",
            json={"decision": "APPROVED", "manifest_hash": "0" * 64},
        )
        self.assertEqual(bad.status_code, 409, bad.text)
        good = self.client.post(
            f"/api/v1/qa-reports/{report_id}/decisions",
            json={"decision": "APPROVED", "manifest_hash": manifest_hash},
        )
        self.assertEqual(good.status_code, 200, good.text)

    @patch("productdirector_api.main.strict_qa.run_strict_qa", return_value=PASS_QA)
    def test_approved_report_invalidates_after_product_file_tamper(self, *mocks) -> None:
        job_id, _, _ = self._create_strict_run()
        self._write_strict_inputs(job_id)
        main.execute_job(job_id)
        report_id, _ = self._approve(job_id)
        product = main.RUNS / job_id / "strict" / "product" / "frame_0001.png"
        product.write_bytes(b"tampered-product-frame")
        fetched = self.client.get(f"/api/v1/qa-reports/{report_id}")
        self.assertEqual(fetched.status_code, 200, fetched.text)
        self.assertFalse(fetched.json()["decision_valid"])
        self.assertEqual(fetched.json()["effective_decision"], "REVOKED")
        stale = self.client.post(
            f"/api/v1/qa-reports/{report_id}/decisions",
            json={"decision": "APPROVED", "manifest_hash": self._manifest_hash(job_id)},
        )
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertIn("替换", stale.text)

    @patch("productdirector_api.main.strict_qa.run_strict_qa", return_value=PASS_QA)
    def test_approved_report_is_revoked_on_each_bound_file_tamper(self, *mocks) -> None:
        job_id, _, _ = self._create_strict_run()
        self._write_strict_inputs(job_id)
        main.execute_job(job_id)
        report_id, manifest_hash = self._approve(job_id)
        strict_root = main.RUNS / job_id / "strict"
        cases = [
            ("metadata", main.RUNS / job_id / "metadata.json"),
            ("product", strict_root / "product" / "frame_0001.png"),
            ("mask", strict_root / "mask" / "frame_0001.png"),
            ("background", strict_root / "background" / "frame_0001.png"),
            ("composite_out", self._first_file(strict_root / "composite_out")),
            ("strict_preview", main.RUNS / job_id / "strict_preview.mp4"),
        ]
        for label, path in cases:
            with self.subTest(label=label):
                original = path.read_bytes()
                path.write_bytes(b"tampered-" + label.encode("utf-8"))
                fetched = self.client.get(f"/api/v1/qa-reports/{report_id}")
                self.assertFalse(fetched.json()["decision_valid"], fetched.text)
                self.assertEqual(fetched.json()["effective_decision"], "REVOKED")
                stale = self.client.post(
                    f"/api/v1/qa-reports/{report_id}/decisions",
                    json={"decision": "APPROVED", "manifest_hash": manifest_hash},
                )
                self.assertEqual(stale.status_code, 409, stale.text)
                path.write_bytes(original)
                restored = self.client.get(f"/api/v1/qa-reports/{report_id}")
                self.assertTrue(restored.json()["decision_valid"], restored.text)

    @patch("productdirector_api.main.strict_qa.run_strict_qa", side_effect=[PASS_QA, FAIL_QA])
    def test_newer_failed_qa_report_invalidates_old_approval(self, *mocks) -> None:
        job_id, _, _ = self._create_strict_run()
        self._write_strict_inputs(job_id)
        main.execute_job(job_id)
        old_report_id, _ = self._approve(job_id)
        run_id = self._run_id(job_id)
        newer = self.client.post(f"/api/v1/runs/{run_id}/qa", json={})
        self.assertEqual(newer.status_code, 200, newer.text)
        self.assertEqual(newer.json()["status"], "FAIL")
        fetched = self.client.get(f"/api/v1/qa-reports/{old_report_id}")
        self.assertEqual(fetched.status_code, 200, fetched.text)
        self.assertFalse(fetched.json()["decision_valid"])
        self.assertEqual(fetched.json()["effective_decision"], "REVOKED")
        stale_decision = self.client.post(
            f"/api/v1/qa-reports/{old_report_id}/decisions",
            json={"decision": "APPROVED", "manifest_hash": self._manifest_hash(job_id)},
        )
        self.assertEqual(stale_decision.status_code, 409, stale_decision.text)
        self.assertFalse(main._job_has_valid_qa_approval(job_id))
        release = self.client.get(f"/api/v1/jobs/{job_id}/release-status")
        self.assertEqual(release.status_code, 200, release.text)
        self.assertFalse(release.json()["eligible"])

    def test_release_eligibility_rejects_untrusted_input_trust(self) -> None:
        job = {"id": "job-x", "status": "SUCCEEDED", "output_path": "strict_preview.mp4"}
        manifest = {
            "strict_mode": True,
            "input_trust": "PRESEEDED_LOCAL_SAMPLE",
            "fidelity_complete": True,
        }
        with patch.object(main, "_job_has_frozen_snapshot", return_value=True), patch.object(
            main, "_job_manifest_payload", return_value=manifest
        ):
            result = main.job_release_eligibility(job)
        self.assertFalse(result["eligible"])
        self.assertIn("input_trust", result["reason"])

    def test_release_eligibility_requires_valid_qa_approval(self) -> None:
        job = {"id": "job-x", "status": "SUCCEEDED", "output_path": "strict_preview.mp4"}
        manifest = {
            "strict_mode": True,
            "input_trust": main.STRICT_VERIFIED_INPUT_TRUST,
            "fidelity_complete": True,
        }
        with patch.object(main, "_job_has_frozen_snapshot", return_value=True), patch.object(
            main, "_job_manifest_payload", return_value=manifest
        ), patch.object(main, "_job_has_valid_qa_approval", return_value=False):
            result = main.job_release_eligibility(job)
        self.assertFalse(result["eligible"])
        self.assertIn("QA", result["reason"])

    @patch("productdirector_api.main.strict_qa.run_strict_qa", return_value=PASS_QA)
    def test_approved_decision_invalidates_on_threshold_review_asset_change(self, *mocks) -> None:
        job_id, _, _ = self._create_strict_run()
        self._write_strict_inputs(job_id)
        main.execute_job(job_id)
        report_id, _ = self._approve(job_id)
        with main.connect() as db:
            report = db.execute("SELECT * FROM qa_reports WHERE id = ?", (report_id,)).fetchone()
            threshold_id = report["threshold_set_id"]
            threshold = db.execute(
                "SELECT payload_sha256 FROM qa_threshold_sets WHERE id = ?", (threshold_id,),
            ).fetchone()
            run = db.execute("SELECT product_review_id FROM runs WHERE job_id = ?", (job_id,)).fetchone()
            review = db.execute(
                "SELECT payload_sha256 FROM product_reviews WHERE id = ?", (run["product_review_id"],),
            ).fetchone()
        self.assertIsNotNone(threshold)
        self.assertIsNotNone(review)

        with main.connect() as db:
            db.execute(
                "UPDATE qa_threshold_sets SET payload_sha256 = ? WHERE id = ?",
                ("0" * 64, threshold_id),
            )
        fetched = self.client.get(f"/api/v1/qa-reports/{report_id}")
        self.assertFalse(fetched.json()["decision_valid"], fetched.text)
        self.assertEqual(fetched.json()["effective_decision"], "REVOKED")
        with main.connect() as db:
            db.execute(
                "UPDATE qa_threshold_sets SET payload_sha256 = ? WHERE id = ?",
                (threshold["payload_sha256"], threshold_id),
            )

        with main.connect() as db:
            db.execute(
                "UPDATE product_reviews SET payload_sha256 = ? WHERE id = ?",
                ("0" * 64, run["product_review_id"]),
            )
        fetched = self.client.get(f"/api/v1/qa-reports/{report_id}")
        self.assertFalse(fetched.json()["decision_valid"], fetched.text)
        with main.connect() as db:
            db.execute(
                "UPDATE product_reviews SET payload_sha256 = ? WHERE id = ?",
                (review["payload_sha256"], run["product_review_id"]),
            )

        asset_file = self._asset_file(job_id)
        original_asset = asset_file.read_bytes()
        asset_file.write_bytes(b"tampered-asset")
        fetched = self.client.get(f"/api/v1/qa-reports/{report_id}")
        self.assertFalse(fetched.json()["decision_valid"], fetched.text)
        self.assertEqual(fetched.json()["effective_decision"], "REVOKED")
        asset_file.write_bytes(original_asset)
        restored = self.client.get(f"/api/v1/qa-reports/{report_id}")
        self.assertTrue(restored.json()["decision_valid"], restored.text)


if __name__ == "__main__":
    unittest.main()
