"""V6 生产计划（多场景/按 Profile 时长）与 V1 三镜头合同的边界测试。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tests"))
sys.path.insert(0, str(PROJECT / "apps" / "api"))

import test_job_control as fixtures  # noqa: E402

main = fixtures.main


class ProductionPlanTests(unittest.TestCase):
    _create_asset = fixtures.JobControlAcceptanceTests._create_asset
    _create_plan = fixtures.JobControlAcceptanceTests._create_plan

    def setUp(self) -> None:
        fixtures.JobControlAcceptanceTests.setUp(self)
        self.asset = self._create_asset()

    tearDown = fixtures.JobControlAcceptanceTests.tearDown

    def _shots(self, durations: list[int]) -> list[dict]:
        return [
            {"id": f"shot_{index + 1:02d}", "name": f"场景 {index + 1}", "camera": "static",
             "focal_length_mm": 35, "duration_frames": duration,
             "caption_text": f"Texto escena {index + 1}"}
            for index, duration in enumerate(durations)
        ]

    def test_five_scene_fifteen_second_plan_is_accepted(self) -> None:
        """15 秒 5 场景（72/96/72/72/48 = 360 帧）在 TikTok MX Profile 下可创建。"""
        response = self.client.post("/api/v1/plans/production", json={
            "product_asset_id": self.asset["id"], "profile_id": "tiktok-mx-9x16-esmx",
            "intent": "Anuncio 15s lámpara astronauta", "locale": "es-MX",
            "shots": self._shots([72, 96, 72, 72, 48]),
            "voiceover_text": "Haz de cada noche una experiencia mágica.",
            # V6-16：外观未核验的模型需要显式接受（复核 BUG-04 的门）
            "accept_unverified_appearance": True,
        })
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["total_frames"], 360)
        self.assertEqual(body["duration_seconds"], 15.0)
        self.assertEqual(len(body["shots"]), 5)
        self.assertEqual(body["output"]["frame_count"], 360)
        self.assertEqual(body["production_plan"]["locale"], "es-MX")

    def test_duration_outside_profile_range_is_rejected(self) -> None:
        # 1:1 Ads 素材 Profile 允许 3–60s；这里用超出上限的构造（96s = 2304 帧）
        response = self.client.post("/api/v1/plans/production", json={
            "product_asset_id": self.asset["id"], "profile_id": "tiktok-mx-9x16-esmx",
            "intent": "demasiado largo", "shots": self._shots([1440, 1440]),
        })
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["detail"]["code"], "duration_out_of_profile_range")

    def test_single_shot_plan_is_rejected(self) -> None:
        response = self.client.post("/api/v1/plans/production", json={
            "product_asset_id": self.asset["id"], "profile_id": "tiktok-mx-9x16-esmx",
            "intent": "una escena", "shots": self._shots([120]),
        })
        self.assertEqual(response.status_code, 422, response.text)

    def test_run_uses_explicit_frame_count_for_production_plan(self) -> None:
        created = self.client.post("/api/v1/plans/production", json={
            "product_asset_id": self.asset["id"], "profile_id": "tiktok-mx-9x16-esmx",
            "intent": "Anuncio 15s", "shots": self._shots([72, 96, 72, 72, 48]),
            "accept_unverified_appearance": True,  # 外观门（复核 BUG-04）
        }).json()
        approved = self.client.post(f"/api/v1/plans/{created['id']}/approve", json={"approved": True})
        self.assertEqual(approved.status_code, 200, approved.text)
        with patch("productdirector_api.main.execute_job"):
            run = self.client.post("/api/v1/runs", json={"plan_id": created["id"]})
        self.assertEqual(run.status_code, 202, run.text)
        with main.connect() as db:
            plan_row = db.execute("SELECT payload FROM plans WHERE id = ?", (created["id"],)).fetchone()
        spec = main.output_spec_for_plan(__import__("json").loads(plan_row["payload"]))
        self.assertEqual(spec.total_frames, 360)
        self.assertEqual(type(spec).__name__, "ReenactmentOutputSpec")

    def test_v1_three_shot_five_to_eight_second_contract_still_enforced(self) -> None:
        """V1 导演路径仍拒绝非 3 镜头 / 非 5–8 秒（回归保护）。"""
        plan = self._create_plan()
        approved = self.client.post(f"/api/v1/plans/{plan['id']}/approve", json={"approved": True})
        self.assertEqual(approved.status_code, 200)
        with main.connect() as db:
            row = db.execute("SELECT payload FROM plans WHERE id = ?", (plan["id"],)).fetchone()
            payload = __import__("json").loads(row["payload"])
            payload["shots"] = payload["shots"][:2]
            payload["output"] = {"width": 540, "height": 960, "fps": 24, "duration_seconds": 6.0}
            db.execute("UPDATE plans SET payload = ? WHERE id = ?",
                       (__import__("json").dumps(payload, ensure_ascii=False), plan["id"]))
        with patch("productdirector_api.main.execute_job"):
            run = self.client.post("/api/v1/runs", json={"plan_id": plan["id"]})
        self.assertEqual(run.status_code, 409, run.text)


if __name__ == "__main__":
    unittest.main()
