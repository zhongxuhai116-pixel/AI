"""V2 AI 导演：描述到合法可编辑计划，含修复上限与 V2 验收样本。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tests"))
sys.path.insert(0, str(PROJECT / "apps" / "api"))

import test_job_control as fixtures  # noqa: E402
from productdirector_api import director  # noqa: E402
from productdirector_api.main import PlanUpdate  # noqa: E402

main = fixtures.main

EXECUTABLE_SAMPLES = [
    ("给一款无线耳机做一条 6 秒竖屏短视频，突出外观与细节", "image"),
    ("围绕智能音箱转一圈展示，要有环绕感", "model"),
    ("开场先给产品全景，再推近到按键细节", "model"),
    ("展示一款机械键盘的材质纹理和灯光", "image"),
    ("用一个侧面横移镜头说明台灯的厚度", "model"),
    ("新品保温杯，突出杯身材质与盖子细节", "image"),
    ("扫地机器人 360 度展示，最后定格在顶部激光雷达", "model"),
    ("展示电动牙刷的握感与刷头特写", "image"),
    ("背包产品：先整体外观，再拉近看拉链与缝线", "image"),
    ("智能手表环绕展示，强调表盘与表带材质", "model"),
]


class DirectorUnitTests(unittest.TestCase):
    def test_rule_based_plan_is_valid_for_every_acceptance_sample(self) -> None:
        for intent, kind in EXECUTABLE_SAMPLES:
            result = director.build_plan(intent, kind, validator=PlanUpdate)
            self.assertEqual(result.repairs, [], intent)
            self.assertEqual(len(result.plan["shots"]), 3, intent)
            self.assertEqual(sum(shot["duration_frames"] for shot in result.plan["shots"]), 144, intent)
            PlanUpdate.model_validate({"intent": result.plan["intent"], "shots": result.plan["shots"]})

    def test_images_never_get_a_physical_orbit_claim(self) -> None:
        result = director.build_plan("环绕展示这款耳机", "image", validator=PlanUpdate)
        self.assertNotIn("hero_orbit", [shot["camera"] for shot in result.plan["shots"]])

    def test_models_can_use_orbit_when_the_description_asks_for_it(self) -> None:
        result = director.build_plan("环绕展示这款音箱", "model", validator=PlanUpdate)
        self.assertIn("hero_orbit", [shot["camera"] for shot in result.plan["shots"]])

    def test_broken_llm_output_is_repaired_within_the_limit(self) -> None:
        def broken(intent, asset_kind):
            return {"intent": intent, "shots": [
                {"id": "shot_01", "name": "a", "duration_frames": 10, "camera": "crane_up", "focal_length_mm": 999},
                {"id": "shot_02", "name": "b", "duration_frames": 10, "camera": "static", "focal_length_mm": 35},
            ]}

        result = director.build_plan("测试修复", "image", validator=PlanUpdate, llm=broken)
        self.assertLessEqual(result.attempts, director.MAX_REPAIRS + 1)
        self.assertTrue(result.repairs)
        self.assertEqual(sum(shot["duration_frames"] for shot in result.plan["shots"]), 144)
        PlanUpdate.model_validate({"intent": result.plan["intent"], "shots": result.plan["shots"]})

    def test_partially_wrong_shots_are_repaired_field_by_field(self) -> None:
        def partly(intent, asset_kind):
            return {"intent": intent, "shots": [
                {"id": "shot_01", "name": "开场", "duration_frames": 999, "camera": "dolly_in", "focal_length_mm": 50},
                {"id": "shot_02", "name": "中段", "duration_frames": 48, "camera": "side_track", "focal_length_mm": 35},
                {"id": "shot_03", "name": "收尾", "duration_frames": 48, "camera": "static", "focal_length_mm": 85},
            ]}

        result = director.build_plan("修复时长", "image", validator=PlanUpdate, llm=partly)
        self.assertTrue(result.repairs)
        self.assertEqual([shot["name"] for shot in result.plan["shots"]], ["开场", "中段", "收尾"])
        self.assertEqual(sum(shot["duration_frames"] for shot in result.plan["shots"]), 144)

    def test_provider_exception_falls_back_to_the_template(self) -> None:
        def explode(intent, asset_kind):
            raise RuntimeError("provider down")

        result = director.build_plan("兜底", "image", validator=PlanUpdate, llm=explode)
        self.assertEqual(result.provider, "rules")
        self.assertTrue(any("provider failed" in note for note in result.repairs))
        PlanUpdate.model_validate({"intent": result.plan["intent"], "shots": result.plan["shots"]})

    def test_parse_llm_content_extracts_json_from_prose(self) -> None:
        parsed = director.parse_llm_content('计划如下：{"intent": "x", "shots": []} 完')
        self.assertEqual(parsed["intent"], "x")
        with self.assertRaises(ValueError):
            director.parse_llm_content("没有 JSON")


class DirectorApiTests(unittest.TestCase):
    def setUp(self) -> None:
        fixtures.JobControlAcceptanceTests.setUp(self)

    tearDown = fixtures.JobControlAcceptanceTests.tearDown

    def test_endpoint_returns_a_validated_plan(self) -> None:
        response = self.client.post("/api/v1/director/plan", json={"intent": "展示一款保温杯的细节"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["validated"])
        self.assertEqual(body["provider"], "rules")
        self.assertEqual(body["configured_provider"], "rules")
        self.assertEqual(len(body["plan"]["shots"]), 3)
        PlanUpdate.model_validate({"intent": body["plan"]["intent"], "shots": body["plan"]["shots"]})

    def test_endpoint_uses_the_asset_kind_and_checks_ownership(self) -> None:
        asset = fixtures.JobControlAcceptanceTests._create_asset(self)
        response = self.client.post(
            "/api/v1/director/plan",
            json={"intent": "环绕展示这款产品", "product_asset_id": asset["id"]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["asset_kind"], "model")
        self.assertIn("hero_orbit", [shot["camera"] for shot in response.json()["plan"]["shots"]])
        self.assertEqual(
            self.client.post("/api/v1/director/plan", json={"intent": "x", "product_asset_id": "missing"}).status_code,
            404,
        )

    def test_v2_acceptance_at_least_nine_of_ten_samples(self) -> None:
        ok = 0
        for intent, _kind in EXECUTABLE_SAMPLES:
            response = self.client.post("/api/v1/director/plan", json={"intent": intent})
            if response.status_code == 200 and response.json()["validated"] and response.json()["attempts"] <= 3:
                ok += 1
        self.assertGreaterEqual(ok, 9)


if __name__ == "__main__":
    unittest.main()
