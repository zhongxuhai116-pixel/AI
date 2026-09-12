"""A02：主规划 V1 的时长边界——三个镜头、总时长 5–8 秒、固定 24fps。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

from pydantic import ValidationError

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tests"))
sys.path.insert(0, str(PROJECT / "apps" / "api"))

import test_job_control as fixtures  # noqa: E402
from productdirector_api import director  # noqa: E402
from productdirector_api.main import OutputSpec, PlanRequest, PlanUpdate  # noqa: E402

main = fixtures.main


def shots_summing_to(total: int) -> list[dict]:
    base = total // 3
    shares = [base, base, total - base * 2]
    cameras = ("dolly_in", "side_track", "hero_orbit")
    return [
        {"id": f"shot_0{index + 1}", "name": f"镜头 {index + 1}",
         "duration_frames": shares[index], "camera": cameras[index], "focal_length_mm": 35}
        for index in range(3)
    ]


class DurationContractTests(unittest.TestCase):
    def test_documented_durations_are_accepted_and_others_rejected(self) -> None:
        for seconds in (5, 6, 7, 8):
            request = PlanRequest.model_validate({
                "product_asset_id": "a", "intent": "x", "duration_seconds": seconds,
                "output": {"width": 540, "height": 960, "fps": 24, "duration_seconds": seconds},
            })
            self.assertEqual(request.duration_seconds, seconds)
            self.assertEqual(PlanUpdate.model_validate(
                {"intent": "x", "shots": shots_summing_to(24 * seconds), "duration_seconds": seconds}
            ).duration_seconds, seconds)
        for seconds in (4, 9, 10):
            with self.assertRaises(ValidationError):
                PlanRequest.model_validate({
                    "product_asset_id": "a", "intent": "x", "duration_seconds": seconds,
                    "output": {"width": 540, "height": 960, "fps": 24, "duration_seconds": seconds},
                })

    def test_shot_frames_must_match_the_declared_duration(self) -> None:
        for seconds in (5, 6, 7, 8):
            total = 24 * seconds
            PlanUpdate.model_validate({"intent": "x", "shots": shots_summing_to(total), "duration_seconds": seconds})
            with self.assertRaises(ValidationError):
                PlanUpdate.model_validate(
                    {"intent": "x", "shots": shots_summing_to(total), "duration_seconds": seconds + 1}
                )

    def test_output_spec_frame_count_follows_the_duration(self) -> None:
        self.assertEqual(OutputSpec(duration_seconds=5).frame_count, 120)
        self.assertEqual(OutputSpec(duration_seconds=8).frame_count, 192)
        with self.assertRaises(ValidationError):
            OutputSpec(duration_seconds=4)

    def test_director_scales_shots_to_the_requested_duration(self) -> None:
        for seconds in (5, 6, 7, 8):
            result = director.build_plan(
                "展示产品外观与细节", "model", validator=PlanUpdate, duration_seconds=seconds
            )
            self.assertEqual(sum(shot["duration_frames"] for shot in result.plan["shots"]), 24 * seconds)
            self.assertTrue(all(shot["duration_frames"] >= 24 for shot in result.plan["shots"]))
            self.assertEqual(result.repairs, [])


class DurationApiTests(unittest.TestCase):
    _create_asset = fixtures.JobControlAcceptanceTests._create_asset

    def setUp(self) -> None:
        fixtures.JobControlAcceptanceTests.setUp(self)

    tearDown = fixtures.JobControlAcceptanceTests.tearDown

    def test_template_plan_uses_the_requested_duration(self) -> None:
        asset = self._create_asset()
        for seconds in (5, 8):
            created = self.client.post(
                "/api/v1/plans/template",
                json={
                    "product_asset_id": asset["id"],
                    "intent": f"{seconds} 秒计划",
                    "duration_seconds": seconds,
                    "output": {"width": 1080, "height": 1920, "fps": 24, "duration_seconds": seconds},
                },
            )
            self.assertEqual(created.status_code, 201, created.text)
            body = created.json()
            self.assertEqual(body["output"]["duration_seconds"], seconds)
            self.assertEqual(sum(shot["duration_frames"] for shot in body["shots"]), 24 * seconds)

    def test_patch_rejects_frames_that_do_not_match_the_duration(self) -> None:
        asset = self._create_asset()
        plan = self.client.post(
            "/api/v1/plans/template",
            json={
                "product_asset_id": asset["id"], "intent": "5 秒", "duration_seconds": 5,
                "output": {"width": 540, "height": 960, "fps": 24, "duration_seconds": 5},
            },
        ).json()
        wrong = self.client.patch(
            f"/api/v1/plans/{plan['id']}",
            json={"intent": "5 秒", "shots": shots_summing_to(144), "duration_seconds": 5},
        )
        self.assertEqual(wrong.status_code, 422)
        right = self.client.patch(
            f"/api/v1/plans/{plan['id']}",
            json={"intent": "5 秒", "shots": shots_summing_to(120), "duration_seconds": 5},
        )
        self.assertEqual(right.status_code, 200)
        self.assertEqual(sum(shot["duration_frames"] for shot in right.json()["shots"]), 120)


if __name__ == "__main__":
    unittest.main()
