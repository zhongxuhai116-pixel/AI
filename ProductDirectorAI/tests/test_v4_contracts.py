"""V4-01：交互锚点 / 人物 / 动作模板合同与版本化的 API 与纯几何测试。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tests"))
sys.path.insert(0, str(PROJECT / "apps" / "api"))

import test_job_control as fixtures  # noqa: E402

main = fixtures.main
from productdirector_api import interaction_geometry  # noqa: E402


class V4ContractTests(unittest.TestCase):
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

    VALID_ANCHOR = {
        "name": "主按钮",
        "position_m": [0.5, 0.5, 0.8],
        "normal": [0, 0, 1],
        "radius_m": 0.05,
        "allowed_actions": ["press_button"],
    }

    def test_anchor_create_validation(self) -> None:
        _, version_id = self._approved_version()
        cases = [
            ({**self.VALID_ANCHOR, "position_m": [0.5, 1.2, 0.8]}, "position_m"),
            ({**self.VALID_ANCHOR, "normal": [1, 1, 1]}, "normal"),
            ({**self.VALID_ANCHOR, "radius_m": 0}, "radius_m"),
            ({**self.VALID_ANCHOR, "allowed_actions": []}, "allowed_actions"),
            ({**self.VALID_ANCHOR, "allowed_actions": ["fly"]}, "不允许的动作类型"),
        ]
        for payload, needle in cases:
            with self.subTest(needle=needle):
                response = self.client.post(f"/api/v1/product-versions/{version_id}/anchors", json=payload)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertIn(needle, response.text)

    def test_anchor_edit_creates_new_revision_immutable(self) -> None:
        _, version_id = self._approved_version()
        created = self.client.post(f"/api/v1/product-versions/{version_id}/anchors", json=self.VALID_ANCHOR)
        self.assertEqual(created.status_code, 201, created.text)
        first = created.json()
        self.assertEqual(first["revision"], 1)
        edited = self.client.patch(
            f"/api/v1/anchors/{first['id']}", json={**self.VALID_ANCHOR, "radius_m": 0.08}
        )
        self.assertEqual(edited.status_code, 200, edited.text)
        second = edited.json()
        self.assertEqual(second["revision"], 2)
        listing = self.client.get(f"/api/v1/product-versions/{version_id}/anchors").json()
        self.assertEqual(len(listing), 2)
        rev1 = [item for item in listing if item["revision"] == 1][0]
        self.assertEqual(rev1["anchor"]["radius_m"], 0.05)  # 旧修订不可改写

    def test_anchor_set_approve_frozen_idempotent(self) -> None:
        _, version_id = self._approved_version()
        created = self.client.post(f"/api/v1/product-versions/{version_id}/anchors", json=self.VALID_ANCHOR).json()
        approve = self.client.post(
            f"/api/v1/product-versions/{version_id}/anchor-sets/approve",
            json={"revision": created["revision"]},
        )
        self.assertEqual(approve.status_code, 200, approve.text)
        set_row = approve.json()
        self.assertEqual(set_row["revision"], 1)
        self.assertEqual(len(set_row["set"]["anchors"]), 1)
        again = self.client.post(
            f"/api/v1/product-versions/{version_id}/anchor-sets/approve", json={"revision": 1}
        )
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.json()["id"], set_row["id"])  # 幂等
        fetched = self.client.get(f"/api/v1/anchor-sets/{set_row['id']}")
        self.assertEqual(fetched.status_code, 200, fetched.text)
        self.assertEqual(fetched.json()["payload_sha256"], set_row["payload_sha256"])
        missing = self.client.post(
            f"/api/v1/product-versions/{version_id}/anchor-sets/approve", json={"revision": 99}
        )
        self.assertEqual(missing.status_code, 404, missing.text)

    def test_anchors_do_not_carry_to_new_product_version(self) -> None:
        plan, version_id = self._approved_version()
        created = self.client.post(f"/api/v1/product-versions/{version_id}/anchors", json=self.VALID_ANCHOR)
        self.assertEqual(created.status_code, 201, created.text)
        self.client.post(f"/api/v1/product-versions/{version_id}/anchor-sets/approve", json={"revision": 1})
        updated = self.client.patch(
            f"/api/v1/plans/{plan['id']}",
            json={"intent": "V4 版本升级", "shots": plan["shots"], "crop_anchor": "center"},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        reapproved = self.client.post(f"/api/v1/plans/{plan['id']}/approve", json={"approved": True})
        new_version = reapproved.json()["product_version_id"]
        self.assertNotEqual(new_version, version_id)
        # 旧锚点不自动沿用：新版本列表为空
        listing = self.client.get(f"/api/v1/product-versions/{new_version}/anchors")
        self.assertEqual(listing.status_code, 200, listing.text)
        self.assertEqual(listing.json(), [])
        # 旧版本自己的锚点与冻结集仍保留
        old_anchors = self.client.get(f"/api/v1/product-versions/{version_id}/anchors").json()
        self.assertEqual(len(old_anchors), 1)
        old_sets = self.client.get(f"/api/v1/product-versions/{version_id}/anchor-sets").json()
        self.assertEqual(len(old_sets), 1)

    def test_character_contract(self) -> None:
        licensed_missing = self.client.post(
            "/api/v1/characters",
            json={"name": "人物A", "source": "licensed_asset", "height_range_m": [1.6, 1.8]},
        )
        self.assertEqual(licensed_missing.status_code, 422, licensed_missing.text)  # 缺许可/同意记录
        proxy = self.client.post(
            "/api/v1/characters",
            json={"name": "合成人物", "source": "synthesized_proxy", "height_range_m": [1.6, 1.8]},
        )
        self.assertEqual(proxy.status_code, 201, proxy.text)
        real = self.client.post(
            "/api/v1/characters",
            json={
                "name": "授权人物",
                "source": "licensed_asset",
                "height_range_m": [1.7, 1.8],
                "license_record": "CC-BY asset pack",
                "consent_record": "recorded consent #1",
            },
        )
        self.assertEqual(real.status_code, 201, real.text)
        listing = self.client.get("/api/v1/characters")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(len(listing.json()), 2)
        bad_height = self.client.post(
            "/api/v1/characters",
            json={"name": "x", "source": "synthesized_proxy", "height_range_m": [2.0, 1.5]},
        )
        self.assertEqual(bad_height.status_code, 422, bad_height.text)

    def test_character_asset_refs_validated_and_capability(self) -> None:
        missing = self.client.post(
            "/api/v1/characters",
            json={
                "name": "缺素材人物", "source": "licensed_asset", "height_range_m": [1.7, 1.8],
                "license_record": "x", "consent_record": "y", "asset_refs": ["no-such-asset"],
            },
        )
        self.assertEqual(missing.status_code, 404, missing.text)
        asset = self._create_asset()
        licensed = self.client.post(
            "/api/v1/characters",
            json={
                "name": "带素材人物", "source": "licensed_asset", "height_range_m": [1.7, 1.8],
                "license_record": "CC0 pack", "consent_record": "consent #1",
                "asset_refs": [asset["id"]],
            },
        )
        self.assertEqual(licensed.status_code, 201, licensed.text)
        capability = self.client.get(f"/api/v1/characters/{licensed.json()['id']}/capability")
        self.assertEqual(capability.status_code, 200, capability.text)
        body = capability.json()
        self.assertEqual(body["generation_route"]["status"], "NOT_CONFIGURED")
        self.assertEqual(body["proxy_previz"]["status"], "AVAILABLE")
        self.assertTrue(body["usable_for_previz"])
        self.assertTrue(body["usable_for_final_person_layer"])
        self.assertEqual(body["licensed_assets"][0]["status"], "BOUND")
        proxy = self.client.post(
            "/api/v1/characters",
            json={"name": "纯合成人物", "source": "synthesized_proxy", "height_range_m": [1.7, 1.8]},
        ).json()
        proxy_capability = self.client.get(f"/api/v1/characters/{proxy['id']}/capability").json()
        self.assertTrue(proxy_capability["usable_for_previz"])
        self.assertFalse(proxy_capability["usable_for_final_person_layer"])

    def test_motion_templates_required_set(self) -> None:
        response = self.client.get("/api/v1/motion-templates")
        self.assertEqual(response.status_code, 200)
        ids = {item["id"] for item in response.json()}
        self.assertTrue({"approach", "press_button", "single_punch_target", "celebrate"} <= ids)
        press = [item for item in response.json() if item["id"] == "press_button"][0]
        self.assertTrue(press["requires_anchor"])
        self.assertTrue(press["contact_events"])

    def test_anchor_world_transform_math(self) -> None:
        bbox_min = (-0.5, -0.2, 0.0)
        bbox_size = (1.0, 0.8, 1.6)
        anchor = {"position_m": [0.5, 0.25, 1.0], "normal": [0, 0, 1], "radius_m": 0.1}
        world = interaction_geometry.anchor_to_world(anchor, bbox_min, bbox_size)
        self.assertAlmostEqual(world["position_m"][0], 0.0)
        self.assertAlmostEqual(world["position_m"][1], 0.0)
        self.assertAlmostEqual(world["position_m"][2], 1.6)
        self.assertAlmostEqual(world["radius_m"], 0.16)  # 最长边 1.6 × 0.1
        self.assertEqual(world["normal"], [0, 0, 1])
        self.assertTrue(interaction_geometry.is_unit_vector((1, 0, 0)))
        self.assertFalse(interaction_geometry.is_unit_vector((1, 1, 0)))


if __name__ == "__main__":
    unittest.main()
