"""A02：产品版本审核语义——批准后不可改写，变更生成新版本。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tests"))
sys.path.insert(0, str(PROJECT / "apps" / "api"))

import test_job_control as fixtures  # noqa: E402

main = fixtures.main

SHOTS = [
    {"id": "shot_01", "name": "正面推近", "duration_frames": 24, "camera": "static", "focal_length_mm": 85},
    {"id": "shot_02", "name": "侧向观察", "duration_frames": 72, "camera": "side_track", "focal_length_mm": 24},
    {"id": "shot_03", "name": "细节定格", "duration_frames": 48, "camera": "hero_orbit", "focal_length_mm": 55},
]


class ProductVersionTests(unittest.TestCase):
    _create_plan = fixtures.JobControlAcceptanceTests._create_plan
    _create_asset = fixtures.JobControlAcceptanceTests._create_asset

    def setUp(self) -> None:
        fixtures.JobControlAcceptanceTests.setUp(self)

    tearDown = fixtures.JobControlAcceptanceTests.tearDown

    def _approve_plan(self, plan_id: str) -> dict:
        response = self.client.post(f"/api/v1/plans/{plan_id}/approve", json={"approved": True})
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_approving_a_plan_approves_its_product_version(self) -> None:
        plan = self._create_plan()
        body = self._approve_plan(plan["id"])
        self.assertEqual(body["product_version_status"], "APPROVED")
        self.assertIsNotNone(body["product_version_approved_at"])

        versions = self.client.get(
            "/api/v1/product-versions", params={"product_asset_id": plan["product_asset_id"]}
        ).json()
        approved = [item for item in versions if item["id"] == body["product_version_id"]]
        self.assertEqual(len(approved), 1)
        self.assertEqual(approved[0]["status"], "APPROVED")

    def test_editing_after_approval_creates_a_new_active_version(self) -> None:
        plan = self._create_plan()
        first = self._approve_plan(plan["id"])

        edited = self.client.patch(
            f"/api/v1/plans/{plan['id']}",
            json={"intent": "改过的描述", "shots": SHOTS, "crop_anchor": "left"},
        )
        self.assertEqual(edited.status_code, 200)
        self.assertNotEqual(edited.json()["contract_id"], first.get("contract_id"))

        versions = self.client.get(
            "/api/v1/product-versions", params={"product_asset_id": plan["product_asset_id"]}
        ).json()
        self.assertEqual(len(versions), 2)
        by_version = {item["version"]: item for item in versions}
        self.assertEqual(by_version[1]["status"], "APPROVED")  # 已批准版本保持不可改写
        self.assertEqual(by_version[2]["status"], "ACTIVE")     # 变更生成新版本
        self.assertEqual(by_version[1]["id"], first["product_version_id"])
        self.assertNotEqual(by_version[1]["snapshot_sha256"], by_version[2]["snapshot_sha256"])

    def test_explicit_version_approval_is_idempotent(self) -> None:
        plan = self._create_plan()
        body = self._approve_plan(plan["id"])
        version_id = body["product_version_id"]
        first = self.client.post(f"/api/v1/product-versions/{version_id}/approve").json()
        second = self.client.post(f"/api/v1/product-versions/{version_id}/approve").json()
        self.assertEqual(first["status"], "APPROVED")
        self.assertEqual(first["approved_at"], second["approved_at"])

    def test_unknown_version_is_rejected(self) -> None:
        self.assertEqual(self.client.post("/api/v1/product-versions/missing/approve").status_code, 404)

    def test_versions_are_scoped_to_the_project(self) -> None:
        plan = self._create_plan()
        self._approve_plan(plan["id"])
        # 未知项目直接 404（不暴露其是否存在）；已存在的其他项目返回空列表。
        self.assertEqual(
            self.client.get("/api/v1/product-versions", params={"project_id": "other-project"}).status_code, 404
        )
        with main.connect() as db:
            db.execute(
                "INSERT INTO projects (id, owner_id, workspace_id, name, created_at) VALUES (?, ?, ?, ?, ?)",
                ("other-project", main.DEFAULT_OWNER_ID, main.DEFAULT_WORKSPACE_ID, "Other", main.utc_now()),
            )
        other = self.client.get("/api/v1/product-versions", params={"project_id": "other-project"})
        self.assertEqual(other.status_code, 200)
        self.assertEqual(other.json(), [])


if __name__ == "__main__":
    unittest.main()
