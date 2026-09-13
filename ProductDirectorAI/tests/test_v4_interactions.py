"""V4-02/03：交互计划合同、校验引擎（接触/穿透/时序）与数据版预演。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tests"))
sys.path.insert(0, str(PROJECT / "apps" / "api"))

import test_job_control as fixtures  # noqa: E402

main = fixtures.main


class V4InteractionTests(unittest.TestCase):
    _create_plan = fixtures.JobControlAcceptanceTests._create_plan
    _create_asset = fixtures.JobControlAcceptanceTests._create_asset

    def setUp(self) -> None:
        fixtures.JobControlAcceptanceTests.setUp(self)

    tearDown = fixtures.JobControlAcceptanceTests.tearDown

    def _approved_version(self) -> tuple[dict, str]:
        plan = self._create_plan()
        approved = self.client.post(f"/api/v1/plans/{plan['id']}/approve", json={"approved": True})
        self.assertEqual(approved.status_code, 200, approved.text)
        return plan, approved.json()["product_version_id"]

    def _character(self, height=(1.7, 1.8)) -> dict:
        response = self.client.post(
            "/api/v1/characters",
            json={"name": "测试人物", "source": "synthesized_proxy", "height_range_m": list(height)},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def _anchor_set(self, version_id: str, z: float = 0.65, radius: float = 0.05) -> dict:
        anchor = self.client.post(
            f"/api/v1/product-versions/{version_id}/anchors",
            json={
                "name": "按钮锚点",
                "position_m": [0.5, 0.5, z],
                "normal": [0, 0, 1],
                "radius_m": radius,
                "allowed_actions": ["press_button"],
            },
        )
        self.assertEqual(anchor.status_code, 201, anchor.text)
        approved = self.client.post(
            f"/api/v1/product-versions/{version_id}/anchor-sets/approve",
            json={"revision": anchor.json()["revision"]},
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        return approved.json()

    def _interaction(self, plan_id: str, version_id: str, anchor_set_id: str, character_id: str, **overrides):
        payload = {
            "character_id": character_id,
            "anchor_set_id": anchor_set_id,
            "action": "press_button",
            "prepare_frame": 10,
            "contact_frame": 46,
            "end_frame": 60,
            "speed_scale": 1.0,
            **overrides,
        }
        response = self.client.post(f"/api/v1/plans/{plan_id}/interactions", json=payload)
        return response

    def test_create_interaction_requires_approved_anchor_set(self) -> None:
        plan, version_id = self._approved_version()
        character = self._character()
        missing = self._interaction(plan["id"], version_id, "nonexistent-set", character["id"])
        self.assertEqual(missing.status_code, 409, missing.text)

    def test_create_interaction_validation(self) -> None:
        plan, version_id = self._approved_version()
        character = self._character()
        anchor_set = self._anchor_set(version_id)
        bad_action = self._interaction(plan["id"], version_id, anchor_set["id"], character["id"], action="fly")
        self.assertEqual(bad_action.status_code, 422, bad_action.text)
        bad_frames = self._interaction(plan["id"], version_id, anchor_set["id"], character["id"],
                                       prepare_frame=50, contact_frame=40)
        self.assertEqual(bad_frames.status_code, 422, bad_frames.text)
        out_of_range = self._interaction(plan["id"], version_id, anchor_set["id"], character["id"], end_frame=500)
        self.assertEqual(out_of_range.status_code, 422, out_of_range.text)
        anchor_without_action = self.client.post(
            f"/api/v1/product-versions/{version_id}/anchors",
            json={"name": "其他锚点", "position_m": [0.3, 0.3, 0.6], "normal": [0, 0, 1],
                  "radius_m": 0.05, "allowed_actions": ["celebrate"]},
        ).json()
        celebrate_set = self.client.post(
            f"/api/v1/product-versions/{version_id}/anchor-sets/approve",
            json={"revision": anchor_without_action["revision"]},
        ).json()
        wrong_anchor = self._interaction(plan["id"], version_id, celebrate_set["id"], character["id"])
        self.assertEqual(wrong_anchor.status_code, 409, wrong_anchor.text)  # 集合内没有允许 press_button 的锚点

    def test_interaction_versions_immutable(self) -> None:
        plan, version_id = self._approved_version()
        character = self._character()
        anchor_set = self._anchor_set(version_id)
        first = self._interaction(plan["id"], version_id, anchor_set["id"], character["id"])
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(first.json()["version"], 1)
        second = self._interaction(plan["id"], version_id, anchor_set["id"], character["id"], contact_frame=48)
        self.assertEqual(second.status_code, 201, second.text)
        self.assertEqual(second.json()["version"], 2)
        listing = self.client.get(f"/api/v1/plans/{plan['id']}/interactions").json()
        self.assertEqual(len(listing), 2)
        v1 = [item for item in listing if item["version"] == 1][0]
        self.assertEqual(v1["interaction"]["contact_frame"], 46)  # 旧版本不可改写

    def test_validate_pass(self) -> None:
        plan, version_id = self._approved_version()
        character = self._character()
        anchor_set = self._anchor_set(version_id, z=0.65)
        created = self._interaction(plan["id"], version_id, anchor_set["id"], character["id"])
        self.assertEqual(created.status_code, 201, created.text)
        report = self.client.post(f"/api/v1/interaction-plans/{created.json()['id']}/validate")
        self.assertEqual(report.status_code, 200, report.text)
        self.assertTrue(report.json()["passed"], report.text)
        self.assertEqual(report.json()["failures"], [])

    def test_validate_timing_deviation_fails(self) -> None:
        plan, version_id = self._approved_version()
        character = self._character()
        anchor_set = self._anchor_set(version_id, z=0.65)
        created = self._interaction(plan["id"], version_id, anchor_set["id"], character["id"], contact_frame=50)
        report = self.client.post(f"/api/v1/interaction-plans/{created.json()['id']}/validate").json()
        self.assertFalse(report["passed"])
        self.assertTrue(any("时序" in failure for failure in report["failures"]), report["failures"])

    def test_validate_unreachable_anchor_fails(self) -> None:
        plan, version_id = self._approved_version()
        character = self._character(height=(0.9, 1.0))  # 可达带 [0.475, 1.0925]
        anchor_set = self._anchor_set(version_id, z=0.9)  # 世界高 1.305m > 1.0925
        created = self._interaction(plan["id"], version_id, anchor_set["id"], character["id"])
        report = self.client.post(f"/api/v1/interaction-plans/{created.json()['id']}/validate").json()
        self.assertFalse(report["passed"])
        self.assertTrue(any("接触距离" in failure for failure in report["failures"]), report["failures"])
        self.assertTrue(report["metrics"]["contact"]["contact_distance_m"] > report["metrics"]["contact"]["threshold_m"])

    def test_validate_penetration_fails(self) -> None:
        plan, version_id = self._approved_version()
        character = self._character(height=(0.9, 1.0))
        anchor_set = self._anchor_set(version_id, z=0.9, radius=0.05)
        created = self._interaction(plan["id"], version_id, anchor_set["id"], character["id"])
        report = self.client.post(f"/api/v1/interaction-plans/{created.json()['id']}/validate").json()
        self.assertFalse(report["passed"])
        self.assertTrue(any("穿透" in failure for failure in report["failures"]), report["failures"])
        self.assertGreater(report["metrics"]["penetration"]["penetration_m"], 0.01)

    def test_interaction_invalidated_after_plan_update(self) -> None:
        plan, version_id = self._approved_version()
        character = self._character()
        anchor_set = self._anchor_set(version_id, z=0.65)
        created = self._interaction(plan["id"], version_id, anchor_set["id"], character["id"])
        self.assertEqual(created.status_code, 201, created.text)
        updated = self.client.patch(
            f"/api/v1/plans/{plan['id']}",
            json={"intent": "V4 版本升级", "shots": plan["shots"], "crop_anchor": "center"},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.client.post(f"/api/v1/plans/{plan['id']}/approve", json={"approved": True})
        stale = self.client.post(f"/api/v1/interaction-plans/{created.json()['id']}/validate")
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertIn("旧交互计划失效", stale.text)

    def test_previz_timeline(self) -> None:
        plan, version_id = self._approved_version()
        character = self._character()
        anchor_set = self._anchor_set(version_id, z=0.65)
        created = self._interaction(plan["id"], version_id, anchor_set["id"], character["id"])
        timeline = self.client.post(f"/api/v1/interaction-plans/{created.json()['id']}/previz")
        self.assertEqual(timeline.status_code, 200, timeline.text)
        body = timeline.json()
        self.assertEqual([item["event"] for item in body["events"]], ["prepare", "contact", "end"])
        self.assertEqual(len(body["frames"]), 60)
        contact_frame = [f for f in body["frames"] if f["event"] == "contact"][0]
        self.assertAlmostEqual(contact_frame["hand_z_m"], 0.65 * 1.45, places=2)


if __name__ == "__main__":
    unittest.main()
