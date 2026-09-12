"""V3-01：Strict Run 冻结绑定——不可变产品版本/审核/策略引用与状态流阻断。"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tests"))
sys.path.insert(0, str(PROJECT / "apps" / "api"))

import test_job_control as fixtures  # noqa: E402
from productdirector_api.main import RunRequest  # noqa: E402

main = fixtures.main


class RunRequestFidelityModelTests(unittest.TestCase):
    def test_partial_binding_is_rejected_by_model(self) -> None:
        with self.assertRaises(ValidationError):
            RunRequest(plan_id="p", require_fidelity_snapshot=True)
        with self.assertRaises(ValidationError):
            RunRequest(plan_id="p", product_review_id="r")
        with self.assertRaises(ValidationError):
            RunRequest(plan_id="p", product_review_sha256="a" * 64)
        RunRequest(plan_id="p")
        RunRequest(
            plan_id="p", require_fidelity_snapshot=True,
            plan_contract_id="c", product_review_id="r", fidelity_policy_id="f",
        )


class V3RunFidelityBindingTests(unittest.TestCase):
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

    def _approved_context(self) -> tuple[dict, str]:
        plan = self._create_plan()
        approved = self.client.post(f"/api/v1/plans/{plan['id']}/approve", json={"approved": True})
        self.assertEqual(approved.status_code, 200)
        return plan, approved.json()["product_version_id"]

    def _review(self, version_id: str, decision: str = "APPROVED", source_kind: str = "cad", **overrides) -> dict:
        body = {
            "decision": decision,
            "source_kind": source_kind,
            "verified_dimensions": {"width": 164.0, "height": 138.0},
            "logo_regions": [{"x": 0.4, "y": 0.35, "width": 0.2, "height": 0.1, "label": "brand"}],
            **overrides,
        }
        response = self.client.post(f"/api/v1/product-versions/{version_id}/reviews", json=body)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def _policy(self, version_id: str, mode: str = "STRICT") -> dict:
        response = self.client.post(
            f"/api/v1/product-versions/{version_id}/fidelity-policies",
            json={
                "mode": mode,
                "protected_regions": [{"x": 0.2, "y": 0.3, "width": 0.4, "height": 0.4, "label": "logo"}],
                "allowed_operations": ["color_transform", "edge_composite", "shadow_layer"],
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def _strict_run(self, plan_id, contract_id, review_id, policy_id, **overrides):
        body = {
            "plan_id": plan_id,
            "require_fidelity_snapshot": True,
            "plan_contract_id": contract_id,
            "product_review_id": review_id,
            "fidelity_policy_id": policy_id,
            **overrides,
        }
        return self.client.post("/api/v1/runs", json=body)

    def _row_counts(self) -> dict:
        with main.connect() as db:
            return {
                "jobs": db.execute("SELECT COUNT(*) AS c FROM jobs").fetchone()["c"],
                "runs": db.execute("SELECT COUNT(*) AS c FROM runs").fetchone()["c"],
                "run_jobs": db.execute("SELECT COUNT(*) AS c FROM run_jobs").fetchone()["c"],
                "job_events": db.execute("SELECT COUNT(*) AS c FROM job_events").fetchone()["c"],
            }

    def test_strict_run_freezes_real_review_and_policy_hashes(self) -> None:
        plan, version_id = self._approved_context()
        contract_id = self._current_contract_id(plan["id"])
        review = self._review(version_id)
        policy = self._policy(version_id)

        with patch("productdirector_api.main.execute_job"):
            response = self._strict_run(plan["id"], contract_id, review["id"], policy["id"])
        self.assertEqual(response.status_code, 202, response.text)
        body = response.json()
        snapshot = body["fidelity_snapshot"]
        self.assertEqual(snapshot["product_review_id"], review["id"])
        self.assertEqual(snapshot["product_review_sha256"], review["payload_sha256"])
        self.assertEqual(snapshot["fidelity_policy_id"], policy["id"])
        self.assertEqual(snapshot["fidelity_policy_sha256"], policy["payload_sha256"])
        self.assertEqual(snapshot["plan_contract_id"], contract_id)

        with main.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id = ?", (body["run_id"],)).fetchone()
        self.assertEqual(row["product_review_id"], review["id"])
        self.assertEqual(row["product_review_sha256"], review["payload_sha256"])
        self.assertEqual(row["fidelity_policy_id"], policy["id"])
        self.assertEqual(row["fidelity_policy_sha256"], policy["payload_sha256"])
        self.assertEqual(json.loads(row["fidelity_snapshot_json"])["product_review_id"], review["id"])

    def test_expected_hashes_are_accepted_and_wrong_hashes_rejected(self) -> None:
        plan, version_id = self._approved_context()
        contract_id = self._current_contract_id(plan["id"])
        review = self._review(version_id)
        policy = self._policy(version_id)

        with patch("productdirector_api.main.execute_job"):
            ok = self._strict_run(
                plan["id"], contract_id, review["id"], policy["id"],
                product_review_sha256=review["payload_sha256"],
                fidelity_policy_sha256=policy["payload_sha256"],
            )
        self.assertEqual(ok.status_code, 202, ok.text)

        bad_review = self._strict_run(
            plan["id"], contract_id, review["id"], policy["id"], product_review_sha256="0" * 64
        )
        self.assertEqual(bad_review.status_code, 409)
        bad_policy = self._strict_run(
            plan["id"], contract_id, review["id"], policy["id"], fidelity_policy_sha256="0" * 64
        )
        self.assertEqual(bad_policy.status_code, 409)

    def test_plan_version_change_invalidates_old_fidelity_binding(self) -> None:
        plan, version_id = self._approved_context()
        old_contract_id = self._current_contract_id(plan["id"])
        old_review = self._review(version_id)
        old_policy = self._policy(version_id)

        updated = self.client.patch(
            f"/api/v1/plans/{plan['id']}",
            json={"intent": "V3 新版计划", "shots": plan["shots"], "crop_anchor": "left"},
        )
        self.assertEqual(updated.status_code, 200)
        approved = self.client.post(f"/api/v1/plans/{plan['id']}/approve", json={"approved": True})
        self.assertEqual(approved.status_code, 200)

        stale = self._strict_run(plan["id"], old_contract_id, old_review["id"], old_policy["id"])
        self.assertEqual(stale.status_code, 409)
        self.assertIn("旧保真审批已失效", stale.json()["detail"])

        new_contract_id = self._current_contract_id(plan["id"])
        wrong_version_review = self._strict_run(plan["id"], new_contract_id, old_review["id"], old_policy["id"])
        self.assertEqual(wrong_version_review.status_code, 404)

    def test_rejected_review_blocks_strict_run(self) -> None:
        plan, version_id = self._approved_context()
        contract_id = self._current_contract_id(plan["id"])
        review = self._review(version_id, decision="REJECTED")
        policy = self._policy(version_id)
        response = self._strict_run(plan["id"], contract_id, review["id"], policy["id"])
        self.assertEqual(response.status_code, 409)
        self.assertIn("审核未通过", response.json()["detail"])

    def test_cross_owner_review_is_rejected(self) -> None:
        plan, version_id = self._approved_context()
        contract_id = self._current_contract_id(plan["id"])
        review = self._review(version_id)
        policy = self._policy(version_id)
        with main.connect() as db:
            db.execute("UPDATE product_reviews SET reviewer = ? WHERE id = ?", ("other-owner", review["id"]))
        response = self._strict_run(plan["id"], contract_id, review["id"], policy["id"])
        self.assertEqual(response.status_code, 403)

    def test_failed_binding_leaves_no_partial_jobs(self) -> None:
        plan, version_id = self._approved_context()
        contract_id = self._current_contract_id(plan["id"])
        review = self._review(version_id)
        policy = self._policy(version_id)
        before = self._row_counts()

        bad = self._strict_run(plan["id"], contract_id, review["id"], policy["id"], fidelity_policy_sha256="0" * 64)
        self.assertEqual(bad.status_code, 409)
        self.assertEqual(self._row_counts(), before)

    def test_snapshot_is_stable_after_later_review_and_policy_are_added(self) -> None:
        plan, version_id = self._approved_context()
        contract_id = self._current_contract_id(plan["id"])
        first_review = self._review(version_id)
        first_policy = self._policy(version_id)
        with patch("productdirector_api.main.execute_job"):
            created = self._strict_run(plan["id"], contract_id, first_review["id"], first_policy["id"])
        self.assertEqual(created.status_code, 202, created.text)
        run_id = created.json()["run_id"]

        second_review = self._review(version_id, verified_dimensions={"width": 200.0})
        second_policy = self._policy(version_id, mode="CONTROLLED")
        with main.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        self.assertEqual(row["product_review_id"], first_review["id"])
        self.assertNotEqual(row["product_review_id"], second_review["id"])
        self.assertEqual(row["fidelity_policy_id"], first_policy["id"])
        self.assertNotEqual(row["fidelity_policy_id"], second_policy["id"])

    def test_legacy_run_remains_compatible(self) -> None:
        plan, _ = self._approved_context()
        with patch("productdirector_api.main.execute_job"):
            response = self.client.post("/api/v1/runs", json={"plan_id": plan["id"]})
        self.assertEqual(response.status_code, 202, response.text)
        self.assertIsNone(response.json()["fidelity_snapshot"])


    def test_non_strict_policy_is_rejected_for_strict_run(self) -> None:
        plan, version_id = self._approved_context()
        contract_id = self._current_contract_id(plan["id"])
        review = self._review(version_id)
        for mode in ("CONTROLLED", "CREATIVE"):
            with self.subTest(mode=mode):
                policy = self._policy(version_id, mode=mode)
                before = self._row_counts()
                response = self._strict_run(plan["id"], contract_id, review["id"], policy["id"])
                self.assertEqual(response.status_code, 409)
                self.assertIn("仅接受 STRICT", response.json()["detail"])
                self.assertEqual(self._row_counts(), before)

    def test_idempotent_retry_before_invalidation_marks_reused(self) -> None:
        plan, version_id = self._approved_context()
        contract_id = self._current_contract_id(plan["id"])
        review = self._review(version_id)
        policy = self._policy(version_id)
        payload = {"idempotency_key": "v3-strict-reuse"}
        with patch("productdirector_api.main.execute_job"):
            first = self._strict_run(plan["id"], contract_id, review["id"], policy["id"], **payload)
            second = self._strict_run(plan["id"], contract_id, review["id"], policy["id"], **payload)
        self.assertEqual(first.status_code, 202, first.text)
        self.assertEqual(second.status_code, 202, second.text)
        self.assertTrue(second.json()["reused_idempotent"])
        self.assertFalse(second.json()["created"])
        self.assertEqual(second.json()["run_id"], first.json()["run_id"])

    def test_idempotent_retry_after_invalidation_does_not_recreate_strict_run(self) -> None:
        plan, version_id = self._approved_context()
        contract_id = self._current_contract_id(plan["id"])
        review = self._review(version_id)
        policy = self._policy(version_id)
        with patch("productdirector_api.main.execute_job"):
            first = self._strict_run(plan["id"], contract_id, review["id"], policy["id"], idempotency_key="v3-strict-invalidated")
        self.assertEqual(first.status_code, 202, first.text)

        updated = self.client.patch(
            f"/api/v1/plans/{plan['id']}",
            json={"intent": "V3 幂等失效复核", "shots": plan["shots"], "crop_anchor": "left"},
        )
        self.assertEqual(updated.status_code, 200)
        approved = self.client.post(f"/api/v1/plans/{plan['id']}/approve", json={"approved": True})
        self.assertEqual(approved.status_code, 200)

        before = self._row_counts()
        retry = self._strict_run(plan["id"], contract_id, review["id"], policy["id"], idempotency_key="v3-strict-invalidated")
        self.assertEqual(retry.status_code, 409)
        self.assertEqual(self._row_counts(), before)
        with main.connect() as db:
            count = db.execute(
                "SELECT COUNT(*) AS c FROM runs WHERE idempotency_key = ?", ("v3-strict-invalidated",)
            ).fetchone()["c"]
        self.assertEqual(count, 1)

if __name__ == "__main__":
    unittest.main()