"""V3-04：产品版本审核与约束绑定。"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from pydantic import ValidationError

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tests"))
sys.path.insert(0, str(PROJECT / "apps" / "api"))

import test_job_control as fixtures  # noqa: E402
from productdirector_api.main import ProductReviewRequest  # noqa: E402

main = fixtures.main


class ProductReviewModelTests(unittest.TestCase):
    def test_single_image_approval_requires_unverified_regions_and_visibility(self) -> None:
        with self.assertRaises(ValidationError):
            ProductReviewRequest(decision="APPROVED", source_kind="single_image")
        with self.assertRaises(ValidationError):
            ProductReviewRequest(decision="APPROVED", source_kind="single_image", unverified_regions=["背面"])
        ProductReviewRequest(
            decision="APPROVED", source_kind="single_image",
            unverified_regions=["背面", "底部"], camera_visibility_constraints=["front_only"],
        )
        # 拒绝时不做该要求
        ProductReviewRequest(decision="REJECTED", source_kind="single_image")

    def test_multi_view_and_cad_are_not_forced_to_declare_unverified_areas(self) -> None:
        ProductReviewRequest(decision="APPROVED", source_kind="multi_view", view_coverage=["front", "back"])
        ProductReviewRequest(decision="APPROVED", source_kind="cad", verified_dimensions={"width": 164.0})


class ProductReviewApiTests(unittest.TestCase):
    _create_plan = fixtures.JobControlAcceptanceTests._create_plan
    _create_asset = fixtures.JobControlAcceptanceTests._create_asset

    def setUp(self) -> None:
        fixtures.JobControlAcceptanceTests.setUp(self)
        plan = self._create_plan()
        approved = self.client.post(f"/api/v1/plans/{plan['id']}/approve", json={"approved": True}).json()
        self.version_id = approved["product_version_id"]
        self.asset = self._create_asset()

    tearDown = fixtures.JobControlAcceptanceTests.tearDown

    def _review(self, **overrides):
        body = {
            "decision": "APPROVED",
            "source_kind": "cad",
            "verified_dimensions": {"width": 164.0, "height": 138.0},
            "logo_regions": [{"x": 0.4, "y": 0.35, "width": 0.2, "height": 0.1, "label": "brand"}],
            "evidence_asset_ids": [self.asset["id"]],
            **overrides,
        }
        return self.client.post(f"/api/v1/product-versions/{self.version_id}/reviews", json=body)

    def test_review_is_frozen_and_bound_to_the_version(self) -> None:
        response = self._review()
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["decision"], "APPROVED")
        self.assertEqual(body["review"]["verified_dimensions"]["width"], 164.0)
        self.assertEqual(len(body["payload_sha256"]), 64)

        versions = self.client.get("/api/v1/product-versions").json()
        current = [item for item in versions if item["id"] == self.version_id][0]
        self.assertEqual(current["status"], "APPROVED")  # 审核不动批准状态
        with main.connect() as db:
            row = db.execute("SELECT * FROM product_versions WHERE id = ?", (self.version_id,)).fetchone()
        self.assertEqual(json.loads(row["verified_dimensions"])["width"], 164.0)
        self.assertEqual(json.loads(row["logo_regions"])[0]["label"], "brand")

        listed = self.client.get(f"/api/v1/product-versions/{self.version_id}/reviews").json()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["payload_sha256"], body["payload_sha256"])

    def test_single_image_approval_requires_declarations(self) -> None:
        self.assertEqual(self._review(source_kind="single_image").status_code, 422)
        ok = self._review(
            source_kind="single_image",
            unverified_regions=["背面", "底部"],
            camera_visibility_constraints=["front_only"],
        )
        self.assertEqual(ok.status_code, 201)
        self.assertEqual(ok.json()["review"]["camera_visibility_constraints"], ["front_only"])

    def test_evidence_assets_must_exist_and_belong_to_the_owner(self) -> None:
        self.assertEqual(self._review(evidence_asset_ids=["missing"]).status_code, 404)
        with main.connect() as db:
            db.execute("UPDATE assets SET owner_id = 'other-owner' WHERE id = ?", (self.asset["id"],))
        self.assertEqual(self._review().status_code, 403)

    def test_unknown_version_is_rejected(self) -> None:
        response = self.client.post(
            "/api/v1/product-versions/missing/reviews", json={"decision": "APPROVED", "source_kind": "cad"}
        )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
