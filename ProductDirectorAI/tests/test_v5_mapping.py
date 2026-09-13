"""V5-03/04：观察与推断分离、参考到计划的映射与误差约束。"""
from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tests"))
sys.path.insert(0, str(PROJECT / "apps" / "api"))

import test_job_control as fixtures  # noqa: E402

main = fixtures.main
FFMPEG = main.FFMPEG or shutil.which("ffmpeg")


def _make_three_segment_video(path: Path) -> None:
    subprocess.run(
        [FFMPEG, "-y", "-v", "error",
         "-f", "lavfi", "-i", "color=c=red:d=1:r=24:s=320x240",
         "-f", "lavfi", "-i", "color=c=blue:d=1:r=24:s=320x240",
         "-f", "lavfi", "-i", "color=c=green:d=1:r=24:s=320x240",
         "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
         "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True,
    )


@unittest.skipUnless(FFMPEG, "需要 FFmpeg 才能生成真实参考视频")
class V5MappingTests(unittest.TestCase):
    _create_asset = fixtures.JobControlAcceptanceTests._create_asset
    _create_plan = fixtures.JobControlAcceptanceTests._create_plan

    def setUp(self) -> None:
        fixtures.JobControlAcceptanceTests.setUp(self)
        self.video = main.VAR / "test-reference-mapping.mp4"
        _make_three_segment_video(self.video)

    tearDown = fixtures.JobControlAcceptanceTests.tearDown

    def _approved_analysis(self) -> tuple[dict, dict]:
        with open(self.video, "rb") as handle:
            ref = self.client.post(
                "/api/v1/references/upload",
                files={"file": ("reference.mp4", handle, "video/mp4")},
            ).json()
        analyzed = self.client.post(f"/api/v1/references/{ref['id']}/analyze", json={"scope": "cuts"}).json()
        approved = self.client.post(f"/api/v1/reference-analyses/{analyzed['id']}/approve").json()
        return ref, approved

    def _approved_version(self) -> str:
        plan = self._create_plan()
        approved = self.client.post(f"/api/v1/plans/{plan['id']}/approve", json={"approved": True})
        return approved.json()["product_version_id"]

    def test_observations_separate_facts_and_inference(self) -> None:
        ref, analysis = self._approved_analysis()
        segments = analysis["analysis"]["segments"]
        self.assertGreaterEqual(len(segments), 3)
        for segment in segments:
            observations = segment["observations"]
            self.assertIn("brightness_mean", observations)
            self.assertIn("motion_type", observations)
            self.assertIn("shot_size", observations)
            self.assertIsNotNone(observations["brightness_mean"]["value"])  # 测量事实
            self.assertIsNone(observations["shot_size"]["value"])  # 不可推断如实为 null
            self.assertIsNone(observations["focal_length_mm"]["value"])
            self.assertIsNotNone(observations["motion_type"]["confidence"])
            self.assertEqual(observations["motion_type"]["value"], "static")  # 纯色段无运动
            for key in ("brightness_mean", "motion_type", "shot_size"):
                self.assertIn("method", observations[key])
                self.assertIn("evidence_frame", observations[key])

    def test_plan_from_reference_mapping_and_duration_errors(self) -> None:
        _, analysis = self._approved_analysis()
        version_id = self._approved_version()
        response = self.client.post(
            f"/api/v1/projects/{main.DEFAULT_PROJECT_ID}/plans/from-reference",
            json={
                "analysis_id": analysis["id"],
                "product_version_id": version_id,
                "selected_dimensions": ["duration", "motion_direction", "transition"],
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(len(body["shots"]), 3)
        self.assertEqual([shot["duration_frames"] for shot in body["shots"]], [24, 24, 24])
        self.assertEqual(body["output"]["frame_count"], 72)
        # 时长误差：每镜头 ≤1 帧、总时长 ≤2 帧（主规划 V5 门）
        for shot in body["reference_mapping"]["shots"]:
            self.assertLessEqual(abs(shot["duration_error_frames"]), 1.0)
        self.assertLessEqual(abs(body["reference_mapping"]["total_duration_error_frames"]), 2.0)
        self.assertEqual(body["reference_mapping"]["shots"][0]["camera"], "static")
        self.assertIn("unsupported_dimensions", body["reference_mapping"]["shots"][0])
        # 冻结引用可取回
        mapping = self.client.get(f"/api/v1/plans/{body['id']}/reference-mapping")
        self.assertEqual(mapping.status_code, 200, mapping.text)
        self.assertEqual(mapping.json()["mapping"]["analysis_id"], analysis["id"])
        self.assertEqual(len(mapping.json()["mapping"]["shots"]), 3)

    def test_plan_from_reference_requires_approved_analysis(self) -> None:
        with open(self.video, "rb") as handle:
            ref = self.client.post(
                "/api/v1/references/upload",
                files={"file": ("reference.mp4", handle, "video/mp4")},
            ).json()
        draft = self.client.post(f"/api/v1/references/{ref['id']}/analyze", json={"scope": "cuts"}).json()
        version_id = self._approved_version()
        rejected = self.client.post(
            f"/api/v1/projects/{main.DEFAULT_PROJECT_ID}/plans/from-reference",
            json={"analysis_id": draft["id"], "product_version_id": version_id,
                  "selected_dimensions": ["duration"]},
        )
        self.assertEqual(rejected.status_code, 409, rejected.text)
        bad_dimension = self.client.post(
            f"/api/v1/projects/{main.DEFAULT_PROJECT_ID}/plans/from-reference",
            json={"analysis_id": draft["id"], "product_version_id": version_id,
                  "selected_dimensions": ["brand"]},
        )
        self.assertEqual(bad_dimension.status_code, 422, bad_dimension.text)


if __name__ == "__main__":
    unittest.main()
