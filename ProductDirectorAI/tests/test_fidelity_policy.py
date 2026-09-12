"""V3-03：FidelityPolicy 合同的版本化与校验。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

from pydantic import ValidationError

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tests"))
sys.path.insert(0, str(PROJECT / "apps" / "api"))

import test_job_control as fixtures  # noqa: E402
from productdirector_api.main import FidelityPolicyRequest, ProtectedRegion  # noqa: E402

main = fixtures.main


class FidelityPolicyModelTests(unittest.TestCase):
    def test_protected_regions_must_stay_inside_the_frame(self) -> None:
        ProtectedRegion(x=0.1, y=0.1, width=0.5, height=0.5)
        with self.assertRaises(ValidationError):
            ProtectedRegion(x=0.8, y=0.1, width=0.4, height=0.2)
        with self.assertRaises(ValidationError):
            ProtectedRegion(x=0.1, y=0.1, width=0, height=0.2)

    def test_unknown_operations_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            FidelityPolicyRequest(mode="STRICT", allowed_operations=["repaint_product"])

    def test_strict_mode_refuses_background_generation(self) -> None:
        with self.assertRaises(ValidationError):
            FidelityPolicyRequest(mode="STRICT", allowed_operations=["background_generation"])
        FidelityPolicyRequest(mode="CONTROLLED", allowed_operations=["background_generation", "color_transform"])


class FidelityPolicyApiTests(unittest.TestCase):
    _create_plan = fixtures.JobControlAcceptanceTests._create_plan
    _create_asset = fixtures.JobControlAcceptanceTests._create_asset

    def setUp(self) -> None:
        fixtures.JobControlAcceptanceTests.setUp(self)
        plan = self._create_plan()
        approved = self.client.post(f"/api/v1/plans/{plan['id']}/approve", json={"approved": True}).json()
        self.version_id = approved["product_version_id"]

    tearDown = fixtures.JobControlAcceptanceTests.tearDown

    def _create(self, **overrides):
        body = {
            "mode": "STRICT",
            "protected_regions": [{"x": 0.2, "y": 0.3, "width": 0.4, "height": 0.4, "label": "logo"}],
            "allowed_operations": ["color_transform", "edge_composite", "shadow_layer"],
            **overrides,
        }
        return self.client.post(f"/api/v1/product-versions/{self.version_id}/fidelity-policies", json=body)

    def test_creating_a_policy_versions_it_and_freezes_the_content(self) -> None:
        first = self._create()
        self.assertEqual(first.status_code, 201, first.text)
        body = first.json()
        self.assertEqual(body["version"], 1)
        self.assertEqual(body["mode"], "STRICT")
        self.assertEqual(body["policy"]["protected_regions"][0]["label"], "logo")
        self.assertEqual(len(body["payload_sha256"]), 64)

        second = self._create(mode="CONTROLLED", allowed_operations=["color_transform", "background_generation"])
        self.assertEqual(second.json()["version"], 2)
        self.assertEqual(second.json()["mode"], "CONTROLLED")

        listed = self.client.get(f"/api/v1/product-versions/{self.version_id}/fidelity-policies").json()
        self.assertEqual([item["version"] for item in listed], [2, 1])
        first_again = [item for item in listed if item["version"] == 1][0]
        self.assertEqual(first_again["payload_sha256"], body["payload_sha256"])
        self.assertEqual(first_again["policy"]["mode"], "STRICT")

    def test_invalid_requests_are_rejected(self) -> None:
        self.assertEqual(self._create(mode="FREE").status_code, 422)
        self.assertEqual(self._create(allowed_operations=["repaint_product"]).status_code, 422)
        self.assertEqual(
            self._create(protected_regions=[{"x": 0.9, "y": 0.1, "width": 0.5, "height": 0.2}]).status_code, 422
        )

    def test_unknown_version_is_rejected(self) -> None:
        response = self.client.post(
            "/api/v1/product-versions/missing/fidelity-policies", json={"mode": "STRICT"}
        )
        self.assertEqual(response.status_code, 404)

    def test_policies_are_scoped_to_the_project(self) -> None:
        with main.connect() as db:
            db.execute(
                "INSERT INTO projects (id, owner_id, workspace_id, name, created_at) VALUES (?, ?, ?, ?, ?)",
                ("other-project", main.DEFAULT_OWNER_ID, main.DEFAULT_WORKSPACE_ID, "Other", main.utc_now()),
            )
        response = self.client.get(
            f"/api/v1/product-versions/{self.version_id}/fidelity-policies", params={"project_id": "other-project"}
        )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
