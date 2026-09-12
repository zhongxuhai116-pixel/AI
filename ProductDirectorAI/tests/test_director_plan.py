from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from pydantic import ValidationError


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "apps" / "api"))

from productdirector_api.main import (  # noqa: E402
    OutputSpec,
    PlanRequest,
    PlanUpdate,
    parse_r_frame_rate,
    build_image_filtergraph,
)


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
        graph = build_image_filtergraph(plan_payload((24, 72, 48)), OutputSpec(width=540, height=960, fps=24, duration_seconds=6))

        self.assertEqual(graph.count("zoompan="), 3)
        self.assertIn("d=24", graph)
        self.assertIn("d=72", graph)
        self.assertIn("d=48", graph)
        self.assertIn("0.16*on/23", graph)  # dolly-in
        self.assertIn("0.08+0.84*on/71", graph)  # side track
        self.assertIn("sin(PI*on/47)", graph)  # 2D orbit approximation
        self.assertIn("s=540x960:fps=24", graph)
        self.assertIn("concat=n=3:v=1:a=0", graph)

    def test_image_preview_filter_respects_1080x1920_output(self):
        graph = build_image_filtergraph(plan_payload((24, 72, 48)), OutputSpec(width=1080, height=1920, fps=24, duration_seconds=6))

        self.assertIn("s=1080x1920:fps=24", graph)

    def test_plan_request_enforces_output_9_16_ratio(self):
        with self.assertRaises(ValidationError):
            PlanRequest(
                product_asset_id="P1",
                intent="demo",
                output={"width": 540, "height": 1080},
            )

    def test_plan_request_enforces_duration_and_output_consistency(self):
        with self.assertRaises(ValidationError):
            PlanRequest(
                product_asset_id="P1",
                intent="demo",
                duration_seconds=8,
                output={"width": 540, "height": 960, "duration_seconds": 6},
            )

    def test_parse_r_frame_rate_supports_fraction_and_raw(self):
        self.assertAlmostEqual(parse_r_frame_rate("30000/1001"), 29.97002997002997)
        self.assertEqual(parse_r_frame_rate("24"), 24.0)

    def test_parse_r_frame_rate_is_fault_tolerant(self):
        self.assertEqual(parse_r_frame_rate("not-a-rate"), 0.0)
        self.assertEqual(parse_r_frame_rate("24/0"), 0.0)

    # --- A06 构图锚点：横向/纵向素材裁进 9:16 时保留哪一侧 ---

    def test_crop_anchor_defaults_to_center(self):
        self.assertEqual(PlanUpdate.model_validate(plan_payload()).crop_anchor.value, "center")
        self.assertEqual(PlanRequest.model_validate({"product_asset_id": "a", "intent": "x"}).crop_anchor.value, "center")

    def test_crop_anchor_offsets_change_the_landscape_crop(self):
        snapshot = plan_payload()

        snapshot["crop_anchor"] = "left"
        self.assertIn("crop=1280:2276:0:(ih-oh)/2", build_image_filtergraph(snapshot, OutputSpec()))
        snapshot["crop_anchor"] = "right"
        self.assertIn("crop=1280:2276:iw-ow:(ih-oh)/2", build_image_filtergraph(snapshot, OutputSpec()))
        snapshot["crop_anchor"] = "center"
        self.assertIn("crop=1280:2276:(iw-ow)/2:(ih-oh)/2", build_image_filtergraph(snapshot, OutputSpec()))

    def test_crop_anchor_offsets_change_the_portrait_crop(self):
        snapshot = plan_payload()

        snapshot["crop_anchor"] = "top"
        self.assertIn("crop=1280:2276:(iw-ow)/2:0", build_image_filtergraph(snapshot, OutputSpec()))
        snapshot["crop_anchor"] = "bottom"
        self.assertIn("crop=1280:2276:(iw-ow)/2:ih-oh", build_image_filtergraph(snapshot, OutputSpec()))

    def test_legacy_snapshot_without_crop_anchor_still_renders_as_center(self):
        # 旧作业快照没有该字段，必须继续按居中裁切工作。
        snapshot = plan_payload()
        self.assertNotIn("crop_anchor", snapshot)
        self.assertIn("crop=1280:2276:(iw-ow)/2:(ih-oh)/2", build_image_filtergraph(snapshot, OutputSpec()))

    def test_invalid_crop_anchor_is_rejected(self):
        with self.assertRaises(ValidationError):
            PlanUpdate.model_validate({**plan_payload(), "crop_anchor": "diagonal"})


if __name__ == "__main__":
    unittest.main()
