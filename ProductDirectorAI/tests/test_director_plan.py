from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from pydantic import ValidationError


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "apps" / "api"))

from productdirector_api.main import PlanUpdate, build_image_filtergraph  # noqa: E402


def plan_payload(durations: tuple[int, int, int] = (48, 48, 48)) -> dict:
    cameras = ("dolly_in", "side_track", "hero_orbit")
    return {
        "intent": "用三段受控相机展示通用产品",
        "shots": [
            {
                "id": f"shot_{index + 1:02d}",
                "name": f"镜头 {index + 1}",
                "camera": cameras[index],
                "focal_length_mm": 35 + index * 10,
                "duration_frames": durations[index],
            }
            for index in range(3)
        ],
    }


class DirectorPlanContractTests(unittest.TestCase):
    def test_accepts_three_shots_that_fill_the_six_second_timeline(self):
        plan = PlanUpdate.model_validate(plan_payload((36, 60, 48)))

        self.assertEqual(sum(shot.duration_frames for shot in plan.shots), 144)
        self.assertEqual([shot.camera for shot in plan.shots], ["dolly_in", "side_track", "hero_orbit"])

    def test_rejects_a_plan_that_cannot_fill_the_fixed_timeline(self):
        with self.assertRaises(ValidationError):
            PlanUpdate.model_validate(plan_payload((48, 48, 47)))

    def test_frozen_snapshot_preserves_the_render_inputs(self):
        snapshot = PlanUpdate.model_validate(plan_payload((24, 72, 48))).model_dump()
        encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode("utf-8")
        decoded = json.loads(encoded)

        self.assertEqual(decoded["shots"][0]["duration_frames"], 24)
        self.assertEqual(decoded["shots"][1]["focal_length_mm"], 45)
        self.assertEqual(sum(shot["duration_frames"] for shot in decoded["shots"]), 144)

    def test_image_preview_compiles_every_shot_from_the_frozen_snapshot(self):
        graph = build_image_filtergraph(plan_payload((24, 72, 48)))

        self.assertEqual(graph.count("zoompan="), 3)
        self.assertIn("d=24", graph)
        self.assertIn("d=72", graph)
        self.assertIn("d=48", graph)
        self.assertIn("0.16*on/23", graph)  # dolly-in
        self.assertIn("0.08+0.84*on/71", graph)  # side track
        self.assertIn("sin(PI*on/47)", graph)  # 2D orbit approximation
        self.assertIn("concat=n=3:v=1:a=0", graph)


if __name__ == "__main__":
    unittest.main()
