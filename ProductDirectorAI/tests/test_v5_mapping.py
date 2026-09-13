"""V5-03/04：观察与推断分离、参考到计划的映射与误差约束。"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tests"))
sys.path.insert(0, str(PROJECT / "apps" / "api"))

import test_job_control as fixtures  # noqa: E402
from test_v3_strict_runtime import (  # noqa: E402
    BACKGROUND_WORKFLOW_HASH,
    BACKGROUND_WORKFLOW_NAME,
    BACKGROUND_WORKFLOW_VERSION,
)

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


def _make_tail_video(path: Path) -> None:
    subprocess.run(
        [FFMPEG, "-y", "-v", "error",
         "-f", "lavfi", "-i", "color=c=red:d=1:r=24:s=320x240",
         "-f", "lavfi", "-i", "color=c=blue:d=1:r=24:s=320x240",
         "-f", "lavfi", "-i", "color=c=green:d=1.5:r=24:s=320x240",
         "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
         "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True,
    )


def _make_short_tail_video(path: Path) -> None:
    subprocess.run(
        [FFMPEG, "-y", "-v", "error",
         "-f", "lavfi", "-i", "color=c=red:d=1:r=24:s=320x240",
         "-f", "lavfi", "-i", "color=c=blue:d=1:r=24:s=320x240",
         "-f", "lavfi", "-i", "color=c=green:d=0.5:r=24:s=320x240",
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

    def test_reference_recreation_citation_in_manifest_helper(self) -> None:
        """V5-06 引用追踪：计划携带 from_reference 时，Run manifest 记录来源引用与冻结映射。"""
        ref, analysis = self._approved_analysis()
        version_id = self._approved_version()
        created = self.client.post(
            f"/api/v1/projects/{main.DEFAULT_PROJECT_ID}/plans/from-reference",
            json={
                "analysis_id": analysis["id"],
                "product_version_id": version_id,
                "selected_dimensions": ["duration"],
            },
        ).json()
        with main.connect() as db:
            plan = db.execute("SELECT * FROM plans WHERE id = ?", (created["id"],)).fetchone()
            citation = main.reference_recreation_citation(db, json.loads(plan["payload"]), created["id"])
        self.assertIsNotNone(citation)
        self.assertEqual(citation["reference_id"], ref["id"])
        self.assertEqual(citation["analysis_id"], analysis["id"])
        self.assertEqual(citation["analysis_revision"], analysis["revision"])
        self.assertEqual(citation["mapping_id"], created["reference_mapping_id"])
        self.assertEqual(citation["mapping"]["total_duration_error_frames"],
                         created["reference_mapping"]["total_duration_error_frames"])
        self.assertIn("reference_source_sha256", citation)
        self.assertIn("reference_proxy_sha256", citation)
        # 无 from_reference 的计划不产生引用块
        plain = self._create_plan()
        with main.connect() as db:
            row = db.execute("SELECT * FROM plans WHERE id = ?", (plain["id"],)).fetchone()
            self.assertIsNone(main.reference_recreation_citation(db, json.loads(row["payload"]), plain["id"]))

    def test_output_spec_explicit_frame_count_for_reenactment(self) -> None:
        """V5-06：重演计划输出携带显式整帧帧数，绕过 V1 5–8 秒合同；V1 约束不回退。"""
        spec = main.ReenactmentOutputSpec.model_validate(
            {"width": 540, "height": 960, "fps": 24, "duration_seconds": 3.21, "frame_count": 77}
        )
        self.assertEqual(spec.total_frames, 77)
        self.assertAlmostEqual(spec.duration_seconds, 3.21)
        resolved = main.output_spec_for_plan(
            {"from_reference": {"analysis_id": "a", "reference_id": "b"},
             "output": {"width": 540, "height": 960, "fps": 24, "duration_seconds": 3.21, "frame_count": 77}}
        )
        self.assertIsInstance(resolved, main.ReenactmentOutputSpec)
        self.assertEqual(resolved.total_frames, 77)
        # V1 合同保持不变：无显式帧数时 5–8 秒，其余拒绝。
        self.assertEqual(main.OutputSpec(duration_seconds=5).total_frames, 120)
        with self.assertRaises(ValidationError):
            main.OutputSpec(duration_seconds=4)

    def test_strict_run_creation_accepts_from_reference_plan(self) -> None:
        """V5-06：重演计划（2–4 镜头、分数时长）可以按严格管线启动任务。"""
        _, analysis = self._approved_analysis()
        version_id = self._approved_version()
        created = self.client.post(
            f"/api/v1/projects/{main.DEFAULT_PROJECT_ID}/plans/from-reference",
            json={
                "analysis_id": analysis["id"],
                "product_version_id": version_id,
                "selected_dimensions": ["duration"],
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        plan = created.json()
        approved = self.client.post(f"/api/v1/plans/{plan['id']}/approve", json={"approved": True})
        self.assertEqual(approved.status_code, 200, approved.text)
        plan_version_id = approved.json()["product_version_id"]
        contracts = self.client.get(f"/api/v1/plans/{plan['id']}/contracts")
        self.assertEqual(contracts.status_code, 200, contracts.text)
        contract_id = contracts.json()[0]["id"]
        review = self.client.post(
            f"/api/v1/product-versions/{plan_version_id}/reviews",
            json={
                "decision": "APPROVED",
                "source_kind": "cad",
                "verified_dimensions": {"width": 164.0, "height": 138.0},
                "logo_regions": [{"x": 0.4, "y": 0.35, "width": 0.2, "height": 0.1, "label": "brand"}],
            },
        )
        self.assertEqual(review.status_code, 201, review.text)
        policy = self.client.post(
            f"/api/v1/product-versions/{plan_version_id}/fidelity-policies",
            json={
                "mode": "STRICT",
                "protected_regions": [{"x": 0.2, "y": 0.3, "width": 0.4, "height": 0.4, "label": "logo"}],
                "allowed_operations": ["color_transform", "edge_composite"],
                "background_workflows": [
                    {
                        "name": BACKGROUND_WORKFLOW_NAME,
                        "version": BACKGROUND_WORKFLOW_VERSION,
                        "workflow_hash": BACKGROUND_WORKFLOW_HASH,
                        "protection_map": [{"product_region": "logo", "background_region": "outside_product"}],
                    },
                ],
            },
        )
        self.assertEqual(policy.status_code, 201, policy.text)
        with patch("productdirector_api.main.execute_job"):
            run = self.client.post(
                "/api/v1/runs",
                json={
                    "plan_id": plan["id"],
                    "require_fidelity_snapshot": True,
                    "plan_contract_id": contract_id,
                    "product_review_id": review.json()["id"],
                    "fidelity_policy_id": policy.json()["id"],
                },
            )
        self.assertEqual(run.status_code, 202, run.text)

    def test_open_tail_segment_uses_reference_duration(self) -> None:
        """V5-06：末尾开放分段（end=null 表示"到片尾"）用参考片实际时长求解，不静默假设 1 秒。"""
        tail = main.VAR / "test-reference-tail.mp4"
        _make_tail_video(tail)
        with open(tail, "rb") as handle:
            ref = self.client.post(
                "/api/v1/references/upload",
                files={"file": ("tail.mp4", handle, "video/mp4")},
            ).json()
        analyzed = self.client.post(f"/api/v1/references/{ref['id']}/analyze", json={"scope": "cuts"}).json()
        approved = self.client.post(f"/api/v1/reference-analyses/{analyzed['id']}/approve").json()
        version_id = self._approved_version()
        created = self.client.post(
            f"/api/v1/projects/{main.DEFAULT_PROJECT_ID}/plans/from-reference",
            json={"analysis_id": approved["id"], "product_version_id": version_id,
                  "selected_dimensions": ["duration"]},
        ).json()
        last = created["reference_mapping"]["shots"][-1]
        self.assertIsNone(last["source_range_s"][1])
        self.assertAlmostEqual(last["duration_source_s"], 1.5, places=2)
        self.assertEqual(last["duration_target_frames"], 36)  # 1.5s × 24fps
        self.assertEqual(created["shots"][-1]["duration_frames"], 36)
        self.assertLessEqual(abs(created["reference_mapping"]["total_duration_error_frames"]), 2.0)

    def test_short_segment_stays_within_duration_error_gate(self) -> None:
        """V5-06：不足 1 秒的尾段量化整帧（不套用 V1 24 帧下限），单镜头误差 ≤1 帧、总误差 ≤2 帧。"""
        tail = main.VAR / "test-reference-short-tail.mp4"
        _make_short_tail_video(tail)
        with open(tail, "rb") as handle:
            ref = self.client.post(
                "/api/v1/references/upload",
                files={"file": ("short.mp4", handle, "video/mp4")},
            ).json()
        analyzed = self.client.post(f"/api/v1/references/{ref['id']}/analyze", json={"scope": "cuts"}).json()
        approved = self.client.post(f"/api/v1/reference-analyses/{analyzed['id']}/approve").json()
        version_id = self._approved_version()
        created = self.client.post(
            f"/api/v1/projects/{main.DEFAULT_PROJECT_ID}/plans/from-reference",
            json={"analysis_id": approved["id"], "product_version_id": version_id,
                  "selected_dimensions": ["duration"]},
        ).json()
        last = created["reference_mapping"]["shots"][-1]
        self.assertEqual(last["duration_target_frames"], 12)  # 0.5s × 24fps
        self.assertEqual(created["shots"][-1]["duration_frames"], 12)
        for shot in created["reference_mapping"]["shots"]:
            self.assertLessEqual(abs(shot["duration_error_frames"]), 1.0)
        self.assertLessEqual(abs(created["reference_mapping"]["total_duration_error_frames"]), 2.0)

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
