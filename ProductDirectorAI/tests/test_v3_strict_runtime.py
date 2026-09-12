"""V3 Strict Run 运行时闭环：RENDER→COMPOSITE→QA 真实小样贯通与失败阻断。"""
from __future__ import annotations

import json
import hashlib
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import OpenEXR
from PIL import Image

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tests"))
sys.path.insert(0, str(PROJECT / "apps" / "api"))

import test_job_control as fixtures  # noqa: E402

main = fixtures.main

SIZE = (8, 8)
FRAME_COUNT = 144
PRODUCT_SRGB = (124, 149, 170)
BACKGROUND_WORKFLOW_NAME = "local.background"
BACKGROUND_WORKFLOW_VERSION = "1.0.0"
BACKGROUND_WORKFLOW_HASH = hashlib.sha256(b"local-deterministic-background-v1").hexdigest()


def _write_rgba(path: Path, color: tuple[int, int, int], rect: tuple[int, int, int, int] = (1, 1, 7, 7)) -> None:
    height, width = SIZE
    array = np.zeros((height, width, 4), dtype=np.uint8)
    array[:, :, 0] = color[0]
    array[:, :, 1] = color[1]
    array[:, :, 2] = color[2]
    x0, y0, x1, y1 = rect
    array[y0:y1, x0:x1, 3] = 255
    Image.fromarray(array, mode="RGBA").save(path)


def _write_rgb(path: Path, color: tuple[int, int, int]) -> None:
    height, width = SIZE
    array = np.zeros((height, width, 3), dtype=np.uint8)
    array[:, :, 0] = color[0]
    array[:, :, 1] = color[1]
    array[:, :, 2] = color[2]
    Image.fromarray(array, mode="RGB").save(path)


def _write_pass_exr(path: Path, *, include_depth: bool = True) -> None:
    height, width = SIZE
    beauty = np.zeros((height, width, 4), dtype=np.float32)
    beauty[:, :, 0] = 0.2
    beauty[:, :, 1] = 0.3
    beauty[:, :, 2] = 0.4
    beauty[:, :, 3] = 1.0
    alpha = np.zeros((height, width), dtype=np.float32); alpha[1:7, 1:7] = 1.0
    channels = {"beauty": beauty, "alpha": alpha}
    if include_depth:
        channels["depth"] = np.full((height, width), 2.0, dtype=np.float32)
    channels["normal.X"] = np.zeros((height, width), dtype=np.float32)
    channels["normal.Y"] = np.zeros((height, width), dtype=np.float32)
    channels["normal.Z"] = np.ones((height, width), dtype=np.float32)
    OpenEXR.File({}, channels).write(str(path))


def _write_controlled_pass_fixture(pass_root: Path, frames: int) -> None:
    pass_root.mkdir(parents=True, exist_ok=True)
    (pass_root / "mask").mkdir(parents=True, exist_ok=True)
    for index in range(1, frames + 1):
        _write_pass_exr(pass_root / f"frame_{index:04d}.exr")
        _write_rgba(pass_root / "mask" / f"frame_{index:04d}.png", (0, 0, 0))


def _fake_controlled_blender_run(command: list[str]):
    args = [str(item) for item in command]
    output_root = Path(args[args.index("--output") + 1])
    frames = int(args[args.index("--frames") + 1])
    _write_controlled_pass_fixture(output_root / "passes", frames)
    return SimpleNamespace(
        returncode=0,
        stdout="DIRECTOR_PASS_CHANNELS beauty,alpha,depth,normal\n",
        stderr="",
    )


def _fake_controlled_blender_missing_passes(command: list[str]):
    args = [str(item) for item in command]
    output_root = Path(args[args.index("--output") + 1])
    (output_root / "passes").mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        returncode=0,
        stdout="DIRECTOR_PASS_CHANNELS beauty,alpha,depth,normal\n",
        stderr="",
    )


class V3StrictRuntimeTests(unittest.TestCase):
    _create_plan = fixtures.JobControlAcceptanceTests._create_plan
    _create_asset = fixtures.JobControlAcceptanceTests._create_asset

    def setUp(self) -> None:
        fixtures.JobControlAcceptanceTests.setUp(self)

    tearDown = fixtures.JobControlAcceptanceTests.tearDown

    def _run_id(self, job_id: str) -> str:
        with main.connect() as db:
            row = db.execute(
                "SELECT id FROM runs WHERE job_id = ? ORDER BY created_at DESC LIMIT 1",
                (job_id,),
            ).fetchone()
        return row["id"]

    def _register_sources(self, job_id: str):
        run_id = self._run_id(job_id)
        response = self.client.post(
            f"/api/v1/runs/{run_id}/strict-source-manifest",
            json={
                "render_evidence": {"producer": "local-blender", "producer_version": "5.2", "pass_layout": "single-multilayer"},
                "background_workflow": {
                    "name": BACKGROUND_WORKFLOW_NAME,
                    "version": BACKGROUND_WORKFLOW_VERSION,
                    "workflow_hash": BACKGROUND_WORKFLOW_HASH,
                    "protection_map": [{"product_region": "logo", "background_region": "outside_product"}],
                },
            },
        )
        return run_id, response


    def _current_contract_id(self, plan_id: str) -> str:
        with main.connect() as db:
            row = db.execute(
                "SELECT id FROM plan_contracts WHERE plan_id = ? ORDER BY version DESC, created_at DESC LIMIT 1",
                (plan_id,),
            ).fetchone()
        return row["id"]

    def _review(self, version_id: str) -> dict:
        response = self.client.post(
            f"/api/v1/product-versions/{version_id}/reviews",
            json={
                "decision": "APPROVED",
                "source_kind": "cad",
                "verified_dimensions": {"width": 164.0, "height": 138.0},
                "logo_regions": [{"x": 0.4, "y": 0.35, "width": 0.2, "height": 0.1, "label": "brand"}],
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def _policy(self, version_id: str, allowed_operations: list[str] | None = None) -> dict:
        allowed_operations = allowed_operations if allowed_operations is not None else ["color_transform", "edge_composite"]
        response = self.client.post(
            f"/api/v1/product-versions/{version_id}/fidelity-policies",
            json={
                "mode": "STRICT",
                "protected_regions": [{"x": 0.2, "y": 0.3, "width": 0.4, "height": 0.4, "label": "logo"}],
                "allowed_operations": allowed_operations,
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
        self.assertEqual(response.status_code, 201, response.text)
        policy = response.json()
        self.assertEqual(policy["policy"]["background_workflows"][0]["workflow_hash"], BACKGROUND_WORKFLOW_HASH)
        return policy

    def _create_strict_run(self, *, required_shot_layers: list[str] | None = None, allowed_operations: list[str] | None = None, required_shot_index: int = 0) -> tuple[dict, str, dict, dict]:
        plan = self._create_plan()
        if required_shot_layers:
            shots = plan["shots"]
            shots[required_shot_index]["required_strict_layers"] = required_shot_layers
            updated = self.client.patch(
                f"/api/v1/plans/{plan['id']}",
                json={"intent": "V3 本镜头必需独立层合同", "shots": shots, "crop_anchor": "left"},
            )
            self.assertEqual(updated.status_code, 200, updated.text)
            plan = updated.json()
        approved = self.client.post(f"/api/v1/plans/{plan['id']}/approve", json={"approved": True})
        self.assertEqual(approved.status_code, 200)
        version_id = approved.json()["product_version_id"]
        contract_id = self._current_contract_id(plan["id"])
        review = self._review(version_id)
        policy = self._policy(version_id, allowed_operations=allowed_operations)
        with patch("productdirector_api.main.execute_job"):
            response = self.client.post(
                "/api/v1/runs",
                json={
                    "plan_id": plan["id"],
                    "require_fidelity_snapshot": True,
                    "plan_contract_id": contract_id,
                    "product_review_id": review["id"],
                    "fidelity_policy_id": policy["id"],
                },
            )
        self.assertEqual(response.status_code, 202, response.text)
        return plan, response.json()["job_id"], review, policy

    def _write_strict_inputs(self, job_id: str, *, include_depth: bool = True, frames: int = FRAME_COUNT) -> Path:
        strict_root = main.RUNS / job_id / "strict"
        for folder in ("passes", "product", "mask", "background"):
            (strict_root / folder).mkdir(parents=True, exist_ok=True)
        (strict_root / "passes" / "mask").mkdir(parents=True, exist_ok=True)
        for index in range(1, frames + 1):
            name = f"frame_{index:04d}.png"
            _write_rgba(strict_root / "product" / name, PRODUCT_SRGB)
            _write_rgba(strict_root / "mask" / name, (0, 0, 0))
            _write_rgba(strict_root / "passes" / "mask" / name, (0, 0, 0))
            _write_rgb(strict_root / "background" / name, (10, 10, 10))
            _write_pass_exr(strict_root / "passes" / f"frame_{index:04d}.exr", include_depth=include_depth)
        return strict_root

    def _write_optional_layer(self, job_id: str, layer_name: str, frames: int = FRAME_COUNT) -> None:
        strict_root = main.RUNS / job_id / "strict"
        (strict_root / layer_name).mkdir(parents=True, exist_ok=True)
        for index in range(1, frames + 1):
            _write_rgba(strict_root / layer_name / f"frame_{index:04d}.png", (20, 20, 20))

    def _write_optional_layer_frames(self, job_id: str, layer_name: str, frame_numbers: list[int]) -> None:
        strict_root = main.RUNS / job_id / "strict"
        (strict_root / layer_name).mkdir(parents=True, exist_ok=True)
        for index in frame_numbers:
            _write_rgba(strict_root / layer_name / f"frame_{index:04d}.png", (20, 20, 20))

    def test_strict_run_executes_full_closure_as_verification_sample(self) -> None:
        plan, job_id, review, policy = self._create_strict_run()
        self._write_strict_inputs(job_id, frames=FRAME_COUNT)

        main.execute_job(job_id)

        job = self.client.get(f"/api/v1/jobs/{job_id}").json()
        self.assertEqual(job["status"], "VERIFICATION_PASSED", job)
        self.assertEqual(job["stage"], "VERIFIED_SAMPLE")
        self.assertTrue(job["output_path"])
        self.assertFalse(job["release_eligible"])
        self.assertTrue(job["release_status"]["verification_only"])
        manifest = self.client.get(f"/api/v1/jobs/{job_id}/manifest").json()
        self.assertTrue(manifest.get("strict_mode"))
        self.assertIs(manifest.get("fidelity_complete"), False)
        self.assertEqual(manifest["input_trust"], "PRESEEDED_LOCAL_SAMPLE")
        self.assertEqual(manifest["fidelity_snapshot"]["product_review_id"], review["id"])
        self.assertEqual(manifest["fidelity_snapshot"]["fidelity_policy_id"], policy["id"])
        self.assertTrue(manifest["passes_report"]["passed"])
        self.assertTrue(manifest["composite_report"]["passed"])
        color_contract = manifest["composite_report"]["color_contract"]
        self.assertEqual(color_contract["working_space"], "display-linear")
        self.assertEqual(color_contract["product_color_space"], "display-srgb")
        self.assertEqual(color_contract["background_color_space"], "display-srgb")
        self.assertEqual(color_contract["blender_view_transform"], "AgX")
        self.assertTrue(color_contract["blender_view_transform_preapplied"])

    def test_strict_verification_sample_release_gate_rejects(self) -> None:
        plan, job_id, review, policy = self._create_strict_run()
        self._write_strict_inputs(job_id, frames=FRAME_COUNT)

        main.execute_job(job_id)

        release = self.client.get(f"/api/v1/jobs/{job_id}/release-status").json()
        self.assertFalse(release["eligible"], release)
        self.assertTrue(release["verification_only"], release)
        self.assertFalse(release["fidelity_complete"], release)
        self.assertEqual(release["input_trust"], "PRESEEDED_LOCAL_SAMPLE")
        self.assertIn("不可发布", release["reason"])

    def test_strict_verification_sample_cannot_retry(self) -> None:
        _, job_id, _, _ = self._create_strict_run()
        self._write_strict_inputs(job_id, frames=FRAME_COUNT)

        main.execute_job(job_id)

        retry = self.client.post(f"/api/v1/jobs/{job_id}/retry")
        self.assertEqual(retry.status_code, 409)
        self.assertEqual(self.client.get(f"/api/v1/jobs/{job_id}").json()["status"], "VERIFICATION_PASSED")

    def test_legacy_complete_endpoint_cannot_succeed_strict_run(self) -> None:
        _, job_id, _, _ = self._create_strict_run()
        self._write_strict_inputs(job_id, frames=1)
        claim = main.claim_job("strict-worker", job_id)
        self.assertTrue(claim["claimed"], claim)

        response = self.client.post(
            f"/internal/v1/workers/jobs/{job_id}/complete",
            json={
                "worker_id": "strict-worker",
                "lease_epoch": claim["lease_epoch"],
                "output_path": "preview.mp4",
                "manifest_path": "metadata.json",
            },
        )
        self.assertEqual(response.status_code, 409, response.text)
        job = self.client.get(f"/api/v1/jobs/{job_id}").json()
        self.assertNotEqual(job["status"], "SUCCEEDED", job)
        self.assertIsNone(job["output_path"])

    def test_missing_depth_channel_blocks_strict_run(self) -> None:
        _, job_id, _, _ = self._create_strict_run()
        self._write_strict_inputs(job_id, include_depth=False, frames=1)

        main.execute_job(job_id)

        job = self.client.get(f"/api/v1/jobs/{job_id}").json()
        self.assertEqual(job["status"], "QA_REJECTED", job)
        self.assertIn("depth", job.get("error", ""))

    def test_missing_background_frame_blocks_strict_run(self) -> None:
        _, job_id, _, _ = self._create_strict_run()
        self._write_strict_inputs(job_id, frames=FRAME_COUNT)
        missing = main.RUNS / job_id / "strict" / "background" / "frame_0002.png"
        missing.unlink()

        main.execute_job(job_id)

        job = self.client.get(f"/api/v1/jobs/{job_id}").json()
        self.assertEqual(job["status"], "QA_REJECTED", job)
        self.assertIn("background", job.get("error", ""))

    def test_wrong_frozen_hash_blocks_strict_run(self) -> None:
        _, job_id, review, _ = self._create_strict_run()
        with main.connect() as db:
            db.execute("UPDATE product_reviews SET payload_sha256 = ? WHERE id = ?", ("0" * 64, review["id"]))

        main.execute_job(job_id)

        job = self.client.get(f"/api/v1/jobs/{job_id}").json()
        self.assertEqual(job["status"], "QA_REJECTED", job)
        self.assertIn("hash", job.get("error", ""))

    def test_invalidated_version_blocks_strict_run(self) -> None:
        plan, job_id, _, _ = self._create_strict_run()
        updated = self.client.patch(
            f"/api/v1/plans/{plan['id']}",
            json={"intent": "V3 版本失效复核", "shots": plan["shots"], "crop_anchor": "left"},
        )
        self.assertEqual(updated.status_code, 200)
        approved = self.client.post(f"/api/v1/plans/{plan['id']}/approve", json={"approved": True})
        self.assertEqual(approved.status_code, 200)

        main.execute_job(job_id)

        job = self.client.get(f"/api/v1/jobs/{job_id}").json()
        self.assertEqual(job["status"], "QA_REJECTED", job)
        self.assertIn("旧保真审批已失效", job.get("error", ""))



    def test_registered_strict_sources_freeze_before_execution(self) -> None:
        plan, job_id, review, policy = self._create_strict_run()
        self._write_strict_inputs(job_id, frames=FRAME_COUNT)

        run_id, response = self._register_sources(job_id)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["manifest_sha256"])
        self.assertTrue(response.json()["source_report"]["passed"])

        main.execute_job(job_id)

        job = self.client.get(f"/api/v1/jobs/{job_id}").json()
        self.assertEqual(job["status"], "VERIFICATION_PASSED", job)
        manifest = self.client.get(f"/api/v1/jobs/{job_id}/manifest").json()
        self.assertEqual(manifest["input_trust"], "REGISTERED_LOCAL_SAMPLE")
        self.assertIs(manifest.get("fidelity_complete"), False)
        self.assertEqual(manifest["source_manifest"]["input_trust"], "REGISTERED_LOCAL_SAMPLE")

    def test_registered_sources_allowed_not_required_layer_may_be_absent(self) -> None:
        _, job_id, _, _ = self._create_strict_run(allowed_operations=["color_transform", "edge_composite", "shadow_layer"])
        self._write_strict_inputs(job_id, frames=FRAME_COUNT)

        _, response = self._register_sources(job_id)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["source_report"]["passed"])

        main.execute_job(job_id)
        self.assertEqual(self.client.get(f"/api/v1/jobs/{job_id}").json()["status"], "VERIFICATION_PASSED")

    def test_registered_sources_allowed_not_required_partial_layer_passes(self) -> None:
        _, job_id, _, _ = self._create_strict_run(allowed_operations=["color_transform", "edge_composite", "shadow_layer"])
        self._write_strict_inputs(job_id, frames=FRAME_COUNT)
        self._write_optional_layer_frames(job_id, "shadow", list(range(1, 49)))

        _, response = self._register_sources(job_id)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["source_report"]["passed"])

        main.execute_job(job_id)
        self.assertEqual(self.client.get(f"/api/v1/jobs/{job_id}").json()["status"], "VERIFICATION_PASSED")

    def test_registered_sources_optional_layer_out_of_range_rejected(self) -> None:
        _, job_id, _, _ = self._create_strict_run(allowed_operations=["color_transform", "edge_composite", "shadow_layer"])
        self._write_strict_inputs(job_id, frames=1)
        self._write_optional_layer_frames(job_id, "shadow", [145])

        _, response = self._register_sources(job_id)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("shadow", response.text)
        self.assertIn("越出", response.text)

    def test_registered_sources_missing_required_layer_rejected(self) -> None:
        _, job_id, _, _ = self._create_strict_run(
            required_shot_layers=["shadow_layer"],
            allowed_operations=["color_transform", "edge_composite", "shadow_layer"],
        )
        self._write_strict_inputs(job_id, frames=1)

        _, response = self._register_sources(job_id)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("shadow", response.text)

    def test_registered_sources_required_layer_must_cover_shot_range(self) -> None:
        _, job_id, _, _ = self._create_strict_run(
            required_shot_layers=["shadow_layer"],
            allowed_operations=["color_transform", "edge_composite", "shadow_layer"],
            required_shot_index=1,
        )
        self._write_strict_inputs(job_id, frames=1)
        self._write_optional_layer(job_id, "shadow", frames=1)

        _, response = self._register_sources(job_id)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("shadow", response.text)
        self.assertIn("缺少帧", response.text)

    def test_registered_sources_required_layer_covers_shot_range_passes(self) -> None:
        _, job_id, _, _ = self._create_strict_run(
            required_shot_layers=["shadow_layer"],
            allowed_operations=["color_transform", "edge_composite", "shadow_layer"],
            required_shot_index=1,
        )
        self._write_strict_inputs(job_id, frames=FRAME_COUNT)
        self._write_optional_layer_frames(job_id, "shadow", list(range(49, 97)))

        _, response = self._register_sources(job_id)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["source_report"]["passed"])

        main.execute_job(job_id)
        self.assertEqual(self.client.get(f"/api/v1/jobs/{job_id}").json()["status"], "VERIFICATION_PASSED")

    def test_registered_sources_required_layer_allows_allowed_extras_in_other_shots(self) -> None:
        _, job_id, _, _ = self._create_strict_run(
            required_shot_layers=["shadow_layer"],
            allowed_operations=["color_transform", "edge_composite", "shadow_layer"],
            required_shot_index=1,
        )
        self._write_strict_inputs(job_id, frames=FRAME_COUNT)
        self._write_optional_layer_frames(job_id, "shadow", list(range(1, 97)))

        _, response = self._register_sources(job_id)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["source_report"]["passed"])

        main.execute_job(job_id)
        self.assertEqual(self.client.get(f"/api/v1/jobs/{job_id}").json()["status"], "VERIFICATION_PASSED")

    def test_registered_sources_required_layer_missing_segment_rejected_with_allowed_extras(self) -> None:
        _, job_id, _, _ = self._create_strict_run(
            required_shot_layers=["shadow_layer"],
            allowed_operations=["color_transform", "edge_composite", "shadow_layer"],
            required_shot_index=1,
        )
        self._write_strict_inputs(job_id, frames=1)
        self._write_optional_layer_frames(job_id, "shadow", list(range(1, 49)))

        _, response = self._register_sources(job_id)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("shadow", response.text)
        self.assertIn("缺少帧", response.text)
        self.assertNotIn("多余帧", response.text)

    def test_registered_sources_present_layer_not_allowed_rejected(self) -> None:
        _, job_id, _, _ = self._create_strict_run()
        self._write_strict_inputs(job_id, frames=1)
        self._write_optional_layer(job_id, "shadow", frames=1)

        _, response = self._register_sources(job_id)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("未获策略允许", response.text)

    def test_preseeded_allowed_not_required_layer_may_be_absent(self) -> None:
        _, job_id, _, _ = self._create_strict_run(allowed_operations=["color_transform", "edge_composite", "shadow_layer"])
        self._write_strict_inputs(job_id, frames=FRAME_COUNT)

        main.execute_job(job_id)

        job = self.client.get(f"/api/v1/jobs/{job_id}").json()
        self.assertEqual(job["status"], "VERIFICATION_PASSED", job)

    def test_preseeded_missing_required_layer_rejected(self) -> None:
        _, job_id, _, _ = self._create_strict_run(
            required_shot_layers=["shadow_layer"],
            allowed_operations=["color_transform", "edge_composite", "shadow_layer"],
        )
        self._write_strict_inputs(job_id, frames=1)

        main.execute_job(job_id)

        job = self.client.get(f"/api/v1/jobs/{job_id}").json()
        self.assertEqual(job["status"], "QA_REJECTED", job)
        self.assertIn("shadow", job.get("error", ""))

    def test_registered_sources_reject_replaced_file(self) -> None:
        _, job_id, _, _ = self._create_strict_run()
        self._write_strict_inputs(job_id, frames=FRAME_COUNT)
        _, response = self._register_sources(job_id)
        self.assertEqual(response.status_code, 200, response.text)

        replaced = main.RUNS / job_id / "strict" / "background" / "frame_0001.png"
        _write_rgb(replaced, (90, 90, 90))
        main.execute_job(job_id)

        job = self.client.get(f"/api/v1/jobs/{job_id}").json()
        self.assertEqual(job["status"], "QA_REJECTED", job)
        self.assertIn("替换", job.get("error", ""))

    def test_registered_sources_reject_missing_mask_frame(self) -> None:
        _, job_id, _, _ = self._create_strict_run()
        self._write_strict_inputs(job_id, frames=FRAME_COUNT)
        missing = main.RUNS / job_id / "strict" / "mask" / "frame_0002.png"
        missing.unlink()

        _, response = self._register_sources(job_id)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("mask", response.text)

    def test_registered_sources_reject_beauty_product_mismatch(self) -> None:
        _, job_id, _, _ = self._create_strict_run()
        self._write_strict_inputs(job_id, frames=1)
        _write_rgba(main.RUNS / job_id / "strict" / "product" / "frame_0001.png", (0, 0, 0))

        _, response = self._register_sources(job_id)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("不一致", response.text)

    def test_registered_sources_reject_running_run_race(self) -> None:
        _, job_id, _, _ = self._create_strict_run()
        self._write_strict_inputs(job_id, frames=1)
        claim = main.claim_job("strict-worker", job_id)
        self.assertTrue(claim["claimed"], claim)

        _, response = self._register_sources(job_id)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("领取/执行前", response.text)

    def test_registered_sources_reject_unapproved_background_workflow(self) -> None:
        _, job_id, _, _ = self._create_strict_run()
        self._write_strict_inputs(job_id, frames=1)
        run_id = self._run_id(job_id)

        response = self.client.post(
            f"/api/v1/runs/{run_id}/strict-source-manifest",
            json={
                "render_evidence": {"producer": "local-blender", "producer_version": "5.2", "pass_layout": "single-multilayer"},
                "background_workflow": {
                    "name": BACKGROUND_WORKFLOW_NAME,
                    "version": "999.0.0",
                    "workflow_hash": hashlib.sha256(b"unapproved-background-workflow").hexdigest(),
                    "protection_map": [],
                },
            },
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("背景工作流未出现", response.text)

    def test_registered_sources_reject_unapproved_background_protection_map(self) -> None:
        _, job_id, _, _ = self._create_strict_run()
        self._write_strict_inputs(job_id, frames=1)
        run_id = self._run_id(job_id)

        response = self.client.post(
            f"/api/v1/runs/{run_id}/strict-source-manifest",
            json={
                "render_evidence": {"producer": "local-blender", "producer_version": "5.2", "pass_layout": "single-multilayer"},
                "background_workflow": {
                    "name": BACKGROUND_WORKFLOW_NAME,
                    "version": BACKGROUND_WORKFLOW_VERSION,
                    "workflow_hash": BACKGROUND_WORKFLOW_HASH,
                    "protection_map": [{"product_region": "full_product", "background_region": "anywhere"}],
                },
            },
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("背景工作流未出现", response.text)


    def test_registered_sources_reject_cross_owner(self) -> None:
        _, job_id, _, _ = self._create_strict_run()
        self._write_strict_inputs(job_id, frames=1)
        run_id = self._run_id(job_id)
        with main.connect() as db:
            now = main.utc_now()
            db.execute("INSERT INTO owners (id, name, created_at) VALUES (?, ?, ?)", ("owner-other", "Other", now))
            db.execute("INSERT INTO workspaces (id, owner_id, name, created_at) VALUES (?, ?, ?, ?)", ("workspace-other", "owner-other", "Other WS", now))
            db.execute("INSERT INTO projects (id, owner_id, workspace_id, name, created_at) VALUES (?, ?, ?, ?, ?)", ("project-other", "owner-other", "workspace-other", "Other Project", now))
        response = self.client.post(
            f"/api/v1/runs/{run_id}/strict-source-manifest",
            params={"owner_id": "owner-other", "project_id": "project-other"},
            json={
                "render_evidence": {"producer": "local-blender", "producer_version": "5.2", "pass_layout": "single-multilayer"},
                "background_workflow": {
                    "name": BACKGROUND_WORKFLOW_NAME,
                    "version": BACKGROUND_WORKFLOW_VERSION,
                    "workflow_hash": BACKGROUND_WORKFLOW_HASH,
                    "protection_map": [],
                },
            },
        )
        self.assertEqual(response.status_code, 403, response.text)

    def test_registered_sources_reject_invalidated_version(self) -> None:
        plan, job_id, _, _ = self._create_strict_run()
        self._write_strict_inputs(job_id, frames=1)
        updated = self.client.patch(
            f'/api/v1/plans/{plan["id"]}',
            json={"intent": "V3 来源注册版本失效复核", "shots": plan["shots"], "crop_anchor": "left"},
        )
        self.assertEqual(updated.status_code, 200)
        self.client.post(f'/api/v1/plans/{plan["id"]}/approve', json={"approved": True})

        _, response = self._register_sources(job_id)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("旧保真审批已失效", response.text)

    @patch.object(main, "BLENDER", "C:/fake/blender.exe")
    @patch.object(main, "probe_blender_version", return_value="Blender 5.2.0 LTS")
    @patch.object(main, "run_controlled_blender_command", side_effect=_fake_controlled_blender_run)
    def test_controlled_blender_product_sources_remain_verification_only(self, *mocks) -> None:
        _, job_id, review, policy = self._create_strict_run()

        main.execute_job(job_id)

        job = self.client.get(f"/api/v1/jobs/{job_id}").json()
        self.assertEqual(job["status"], "VERIFICATION_PASSED", job)
        manifest = self.client.get(f"/api/v1/jobs/{job_id}/manifest").json()
        self.assertEqual(manifest["input_trust"], main.CONTROLLED_BLENDER_PRODUCT_TRUST)
        self.assertIs(manifest.get("fidelity_complete"), False)
        evidence = manifest["controlled_render_evidence"]
        self.assertEqual(evidence["producer_control"], "CONTROLLED_WORKER")
        self.assertEqual(evidence["worker_id"], "local-background")
        self.assertEqual(evidence["blender_version"], "Blender 5.2.0 LTS")
        self.assertEqual(evidence["render_script_sha256"], main._file_sha256(main.BLENDER_SCRIPT))
        self.assertEqual(evidence["exit_code"], 0)
        self.assertEqual(evidence["frames_requested"], FRAME_COUNT)
        release = self.client.get(f"/api/v1/jobs/{job_id}/release-status").json()
        self.assertFalse(release["eligible"], release)
        self.assertTrue(release["verification_only"], release)

    def test_controlled_render_rejects_asset_hash_mismatch(self) -> None:
        _, job_id, _, _ = self._create_strict_run()
        with main.connect() as db:
            asset_row = db.execute("SELECT * FROM assets WHERE id = (SELECT asset_id FROM jobs WHERE id = ?)", (job_id,)).fetchone()
        asset_path = main.resolve_asset_path(main.row_to_dict(asset_row))
        asset_path.write_bytes(b"tampered asset bytes")

        main.execute_job(job_id)

        job = self.client.get(f"/api/v1/jobs/{job_id}").json()
        self.assertEqual(job["status"], "QA_REJECTED", job)
        self.assertIn("资产文件 hash", job.get("error", ""))

    def test_controlled_render_rejects_invalidated_version(self) -> None:
        plan, job_id, _, _ = self._create_strict_run()
        updated = self.client.patch(
            f'/api/v1/plans/{plan["id"]}',
            json={"intent": "V3 受控渲染版本失效复核", "shots": plan["shots"], "crop_anchor": "left"},
        )
        self.assertEqual(updated.status_code, 200)
        self.client.post(f'/api/v1/plans/{plan["id"]}/approve', json={"approved": True})

        main.execute_job(job_id)

        job = self.client.get(f"/api/v1/jobs/{job_id}").json()
        self.assertEqual(job["status"], "QA_REJECTED", job)
        self.assertIn("旧保真审批已失效", job.get("error", ""))

    @patch.object(main, "BLENDER", "C:/fake/blender.exe")
    @patch.object(main, "probe_blender_version", return_value="Blender 5.2.0 LTS")
    @patch.object(
        main, "run_controlled_blender_command",
        return_value=SimpleNamespace(returncode=2, stdout="", stderr="blender render crashed"),
    )
    def test_controlled_render_process_failure_blocks_run(self, *mocks) -> None:
        _, job_id, _, _ = self._create_strict_run()

        main.execute_job(job_id)

        job = self.client.get(f"/api/v1/jobs/{job_id}").json()
        self.assertEqual(job["status"], "QA_REJECTED", job)
        self.assertIn("渲染进程失败", job.get("error", ""))

    @patch.object(main, "BLENDER", "C:/fake/blender.exe")
    @patch.object(main, "probe_blender_version", return_value="Blender 5.2.0 LTS")
    @patch.object(main, "run_controlled_blender_command", side_effect=_fake_controlled_blender_missing_passes)
    def test_controlled_render_missing_pass_blocks_run(self, *mocks) -> None:
        _, job_id, _, _ = self._create_strict_run()

        main.execute_job(job_id)

        job = self.client.get(f"/api/v1/jobs/{job_id}").json()
        self.assertEqual(job["status"], "QA_REJECTED", job)
        self.assertIn("五通道校验未通过", job.get("error", ""))

    def test_controlled_render_evidence_rejects_tampered_file(self) -> None:
        strict_root = main.RUNS / "controlled-evidence-probe" / "strict"
        target = strict_root / "passes" / "frame_0001.exr"
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_pass_exr(target)
        evidence = {
            "generated_files": [{
                "path": "passes/frame_0001.exr",
                "sha256": main._file_sha256(target),
            }],
        }
        target.write_bytes(b"tampered exr")
        failures = main.verify_controlled_render_evidence(strict_root, evidence)
        self.assertTrue(any("替换" in failure for failure in failures), failures)

if __name__ == "__main__":
    unittest.main()
