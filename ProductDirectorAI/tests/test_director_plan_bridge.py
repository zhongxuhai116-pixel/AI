"""A02：运行时快照与目标 3D 合同之间的桥接必须可验证、可往返。"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from pydantic import ValidationError

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "apps" / "api"))

from productdirector_api import director_plan  # noqa: E402
from productdirector_api.main import Shot  # noqa: E402

try:
    from jsonschema import Draft202012Validator
except ModuleNotFoundError:  # pragma: no cover
    Draft202012Validator = None


def runtime_snapshot(**overrides) -> dict:
    snapshot = {
        "schema_version": "1.0",
        "product_asset_id": "11111111-1111-4111-8111-111111111111",
        "intent": "桥接测试",
        "output": {"width": 1080, "height": 1920, "fps": 24, "duration_seconds": 6},
        "fidelity_mode": "STRICT_REQUESTED",
        "crop_anchor": "center",
        "shots": [
            {"id": "shot_01", "name": "正面推近", "duration_frames": 24, "camera": "static", "focal_length_mm": 85},
            {"id": "shot_02", "name": "侧向观察", "duration_frames": 72, "camera": "side_track", "focal_length_mm": 24},
            {"id": "shot_03", "name": "立体环绕展示", "duration_frames": 48, "camera": "hero_orbit", "focal_length_mm": 55},
        ],
    }
    snapshot.update(overrides)
    return snapshot


@unittest.skipUnless(Draft202012Validator, "需要 jsonschema")
class BridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.validator = Draft202012Validator(director_plan.load_target_schema())

    def errors(self, document: dict) -> list:
        return sorted(self.validator.iter_errors(document), key=lambda error: list(error.path))

    def test_default_snapshot_converts_to_a_valid_3d_document(self) -> None:
        document = director_plan.to_target_document(runtime_snapshot())
        self.assertEqual(self.errors(document), [])
        self.assertEqual(document["fidelity_mode"], "STRICT")
        self.assertEqual(document["output"]["container"], "mp4")
        self.assertEqual([shot["camera"]["path"]["type"] for shot in document["shots"]],
                         ["static", "side_track", "hero_orbit"])

    def test_unspecified_3d_fields_fall_back_to_renderer_defaults(self) -> None:
        document = director_plan.to_target_document(runtime_snapshot())
        orbit = document["shots"][2]["camera"]["path"]
        # 与 blender/scripts/render_product.py 的历史默认取景保持一致
        self.assertEqual(orbit["radius_m"], director_plan.DEFAULT_ORBIT_RADIUS_M)
        self.assertEqual(orbit["height_m"], director_plan.DEFAULT_ORBIT_HEIGHT_M)
        self.assertEqual(orbit["start_angle_deg"], director_plan.DEFAULT_ORBIT_START_DEG)
        self.assertEqual(orbit["end_angle_deg"], director_plan.DEFAULT_ORBIT_END_DEG)
        self.assertEqual(document["shots"][0]["camera"]["target_m"], list(director_plan.DEFAULT_CAMERA_TARGET))
        self.assertEqual(document["shots"][0]["scene"]["background_color"], "#0A0A0C")

    def test_explicit_3d_fields_survive_the_bridge(self) -> None:
        snapshot = runtime_snapshot(
            # scale 与 sensor_width 必须落在目标合同当前允许的范围内（见 test_contract_limits_*）
            product_pose={"position_m": [0.1, -0.2, 0.05], "rotation_xyz_deg": [0, 0, 25], "scale": 1},
            scene={"template": "studio_product", "background_color": "#101828", "lighting_preset": "three_point"},
        )
        snapshot["shots"][2]["camera_path"] = {
            "type": "hero_orbit", "radius_m": 6.0, "height_m": 2.2,
            "start_angle_deg": -80, "end_angle_deg": 80,
        }
        snapshot["shots"][2]["camera_target_m"] = [0, 0, 0.9]
        snapshot["shots"][2]["sensor_width_mm"] = 36

        document = director_plan.to_target_document(snapshot)
        self.assertEqual(self.errors(document), [])
        camera = document["shots"][2]["camera"]
        self.assertEqual(camera["path"]["radius_m"], 6.0)
        self.assertEqual(camera["sensor_width_mm"], 36)
        self.assertEqual(camera["target_m"], [0, 0, 0.9])
        self.assertEqual(document["shots"][0]["product_pose"]["rotation_xyz_deg"], [0, 0, 25])
        self.assertEqual(document["shots"][0]["scene"]["lighting_preset"], "three_point")

    def test_contract_limits_are_reported_instead_of_silently_clamped(self) -> None:
        """运行时支持超出目标合同的值，但桥接必须显式报错而不是伪装合规。"""
        scaled = runtime_snapshot(product_pose={"position_m": [0, 0, 0], "rotation_xyz_deg": [0, 0, 0], "scale": 1.5})
        with self.assertRaises(director_plan.TargetContractError) as caught:
            director_plan.to_target_document(scaled)
        self.assertIn("scale", str(caught.exception))

        wide_sensor = runtime_snapshot()
        wide_sensor["shots"][0]["sensor_width_mm"] = 24
        with self.assertRaises(director_plan.TargetContractError) as caught:
            director_plan.to_target_document(wide_sensor)
        self.assertIn("sensor_width_mm", str(caught.exception))

    def test_round_trip_preserves_shot_semantics(self) -> None:
        original = runtime_snapshot()
        original["shots"][1]["camera_path"] = {
            "type": "side_track", "start_m": [-2.0, -3.4, 1.1], "end_m": [2.0, -3.4, 1.1],
        }
        document = director_plan.to_target_document(original)
        restored = director_plan.from_target_document(document)

        self.assertEqual([shot["camera"] for shot in restored["shots"]], ["static", "side_track", "hero_orbit"])
        self.assertEqual([shot["duration_frames"] for shot in restored["shots"]], [24, 72, 48])
        self.assertEqual([shot["focal_length_mm"] for shot in restored["shots"]], [85, 24, 55])
        self.assertEqual(restored["shots"][1]["camera_path"]["start_m"], (-2.0, -3.4, 1.1))
        # 往返后的镜头仍然能被运行时模型接受
        for shot in restored["shots"]:
            Shot.model_validate(shot)

    def test_runtime_rejects_unknown_camera_path_type(self) -> None:
        with self.assertRaises(ValidationError):
            Shot.model_validate({
                "id": "shot_01", "name": "x", "duration_frames": 48,
                "camera": "hero_orbit", "focal_length_mm": 35,
                "camera_path": {"type": "crane_up", "height_m": 1.0},
            })

    def test_runtime_rejects_out_of_range_orbit(self) -> None:
        with self.assertRaises(ValidationError):
            Shot.model_validate({
                "id": "shot_01", "name": "x", "duration_frames": 48,
                "camera": "hero_orbit", "focal_length_mm": 35,
                "camera_path": {"type": "hero_orbit", "radius_m": 0},
            })

    def test_target_schema_example_still_round_trips(self) -> None:
        example = json.loads((PROJECT / "contracts" / "director-plan.v1.example.json").read_text(encoding="utf-8"))
        self.assertEqual(self.errors(example), [])
        restored = director_plan.from_target_document(example)
        again = director_plan.to_target_document({
            "intent": restored["intent"],
            "output": restored["output"],
            "shots": restored["shots"],
            "product_pose": restored["product_pose"],
            "scene": restored["scene"],
        })
        self.assertEqual(self.errors(again), [])


if __name__ == "__main__":
    unittest.main()
