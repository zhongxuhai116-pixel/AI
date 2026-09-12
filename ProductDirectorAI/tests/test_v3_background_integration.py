"""V3-03 背景 Producer 与同一持租约 Job Worker 的端到端离线正负例。"""
from __future__ import annotations

import os
import shutil
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
import test_v3_strict_runtime as rt  # noqa: E402

main = fixtures.main

BACKGROUND_WORKFLOW = {
    "name": rt.BACKGROUND_WORKFLOW_NAME,
    "version": rt.BACKGROUND_WORKFLOW_VERSION,
    "workflow_hash": rt.BACKGROUND_WORKFLOW_HASH,
    "protection_map": [{"product_region": "logo", "background_region": "outside_product"}],
}
FULL_SIZE = (960, 540)


def _write_full_rgba(path: Path, color: tuple[int, int, int]) -> None:
    height, width = FULL_SIZE
    array = np.zeros((height, width, 4), dtype=np.uint8)
    array[:, :, 0] = color[0]
    array[:, :, 1] = color[1]
    array[:, :, 2] = color[2]
    array[1:height - 1, 1:width - 1, 3] = 255
    Image.fromarray(array, mode="RGBA").save(path)


def _write_full_pass_exr(path: Path) -> None:
    height, width = FULL_SIZE
    beauty = np.zeros((height, width, 4), dtype=np.float32)
    beauty[:, :, 0] = 0.2
    beauty[:, :, 1] = 0.3
    beauty[:, :, 2] = 0.4
    beauty[:, :, 3] = 1.0
    alpha = np.zeros((height, width), dtype=np.float32)
    alpha[1:height - 1, 1:width - 1] = 1.0
    channels = {
        "beauty": beauty,
        "alpha": alpha,
        "depth": np.full((height, width), 2.0, dtype=np.float32),
        "normal.X": np.zeros((height, width), dtype=np.float32),
        "normal.Y": np.zeros((height, width), dtype=np.float32),
        "normal.Z": np.ones((height, width), dtype=np.float32),
    }
    OpenEXR.File({}, channels).write(str(path))


def _fake_blender_full(command: list[str]):
    args = [str(item) for item in command]
    output_root = Path(args[args.index("--output") + 1])
    frames = int(args[args.index("--frames") + 1])
    strict_root = output_root
    pass_root = strict_root / "passes"
    for folder in (pass_root, pass_root / "mask", strict_root / "product", strict_root / "mask"):
        folder.mkdir(parents=True, exist_ok=True)
    for index in range(1, frames + 1):
        name = f"frame_{index:04d}.png"
        _write_full_pass_exr(pass_root / f"frame_{index:04d}.exr")
        product_path = strict_root / "product" / name
        _write_full_rgba(product_path, rt.PRODUCT_SRGB)
        shutil.copyfile(product_path, strict_root / "mask" / name)
        shutil.copyfile(product_path, pass_root / "mask" / name)
    return SimpleNamespace(
        returncode=0,
        stdout="DIRECTOR_PASS_CHANNELS beauty,alpha,depth,normal\n",
        stderr="",
    )


def _fake_blender_passes_only(command: list[str]):
    """模拟受控 Blender 只写出五通道 EXR、漏掉 Strict product/mask 的坏样例。"""
    args = [str(item) for item in command]
    output_root = Path(args[args.index("--output") + 1])
    frames = int(args[args.index("--frames") + 1])
    pass_root = output_root / "passes"
    pass_root.mkdir(parents=True, exist_ok=True)
    for index in range(1, frames + 1):
        _write_full_pass_exr(pass_root / f"frame_{index:04d}.exr")
    return SimpleNamespace(
        returncode=0,
        stdout="DIRECTOR_PASS_CHANNELS beauty,alpha,depth,normal\n",
        stderr="",
    )


class V3BackgroundIntegrationTests(unittest.TestCase):
    _create_plan = fixtures.JobControlAcceptanceTests._create_plan
    _create_asset = fixtures.JobControlAcceptanceTests._create_asset

    def setUp(self) -> None:
        fixtures.JobControlAcceptanceTests.setUp(self)
        self.source_root = Path(__import__("tempfile").mkdtemp(prefix="pd-bg-src-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.source_root, ignore_errors=True))

    tearDown = fixtures.JobControlAcceptanceTests.tearDown

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

    def _policy(self, version_id: str) -> dict:
        response = self.client.post(
            f"/api/v1/product-versions/{version_id}/fidelity-policies",
            json={
                "mode": "STRICT",
                "protected_regions": [{"x": 0.2, "y": 0.3, "width": 0.4, "height": 0.4, "label": "logo"}],
                "allowed_operations": ["color_transform", "edge_composite"],
                "background_workflows": [BACKGROUND_WORKFLOW],
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def _create_run(self, source_mode: str | None = "CONTROLLED_IMPORT", *, include_mode: bool = True) -> tuple[dict, str]:
        plan = self._create_plan()
        approved = self.client.post(f"/api/v1/plans/{plan['id']}/approve", json={"approved": True})
        self.assertEqual(approved.status_code, 200, approved.text)
        version_id = approved.json()["product_version_id"]
        review = self._review(version_id)
        policy = self._policy(version_id)
        payload = {
            "plan_id": plan["id"],
            "require_fidelity_snapshot": True,
            "plan_contract_id": self._current_contract_id(plan["id"]),
            "product_review_id": review["id"],
            "fidelity_policy_id": policy["id"],
            "background_workflow": BACKGROUND_WORKFLOW,
        }
        if include_mode:
            payload["background_source_mode"] = source_mode
        with patch("productdirector_api.main.execute_job"):
            response = self.client.post("/api/v1/runs", json=payload)
        return plan, response

    def _write_controlled_source(self, job_id: str, frames: int = rt.FRAME_COUNT, source_job: str | None = None) -> Path:
        source_job = source_job or job_id
        source_dir = self.source_root / source_job
        source_dir.mkdir(parents=True, exist_ok=True)
        height, width = FULL_SIZE
        for index in range(1, frames + 1):
            array = np.zeros((height, width, 4), dtype=np.uint8)
            array[:, :, 0] = 12
            array[:, :, 1] = 34
            array[:, :, 2] = 56
            array[:, :, 3] = 0
            Image.fromarray(array, mode="RGBA").save(source_dir / f"frame_{index:04d}.png")
        return source_dir

    @patch.dict(os.environ, {"PRODUCTDIRECTOR_BACKGROUND_SOURCE_ROOT": ""}, clear=False)
    def _set_source_root_env(self) -> None:
        os.environ["PRODUCTDIRECTOR_BACKGROUND_SOURCE_ROOT"] = str(self.source_root)

    @patch.object(main, "BLENDER", "C:/fake/blender.exe")
    @patch.object(main, "probe_blender_version", return_value="Blender 5.2.0 LTS")
    @patch.object(main, "run_controlled_blender_command", side_effect=_fake_blender_full)
    @patch.dict(os.environ, {"PRODUCTDIRECTOR_BACKGROUND_WORKFLOW_FILE": "", "PRODUCTDIRECTOR_BACKGROUND_SOURCE_ROOT": ""}, clear=False)
    def test_controlled_import_composite_is_verification_sample(self, *mocks) -> None:
        os.environ["PRODUCTDIRECTOR_BACKGROUND_SOURCE_ROOT"] = str(self.source_root)
        plan, response = self._create_run("CONTROLLED_IMPORT")
        self.assertEqual(response.status_code, 202, response.text)
        job_id = response.json()["job_id"]
        self._write_controlled_source(job_id)

        main.execute_job(job_id)

        job = self.client.get(f"/api/v1/jobs/{job_id}").json()
        self.assertEqual(job["status"], "VERIFICATION_PASSED", job)
        manifest = self.client.get(f"/api/v1/jobs/{job_id}/manifest").json()
        self.assertEqual(manifest["input_trust"], "CONTROLLED_IMPORT_VERIFICATION_SAMPLE", manifest)
        self.assertIs(manifest.get("fidelity_complete"), False)
        evidence = manifest["background_evidence"]
        self.assertEqual(evidence["source_provenance"], "CONTROLLED_IMPORT")
        self.assertFalse(evidence["generation_claim"])
        release = self.client.get(f"/api/v1/jobs/{job_id}/release-status").json()
        self.assertFalse(release["eligible"], release)
        self.assertTrue((main.RUNS / job_id / "strict" / "background" / "frame_0001.png").exists())
        product_path = main.RUNS / job_id / "strict" / "product" / "frame_0001.png"
        mask_path = main.RUNS / job_id / "strict" / "mask" / "frame_0001.png"
        self.assertEqual(product_path.read_bytes(), mask_path.read_bytes())

    @patch.object(main, "BLENDER", "C:/fake/blender.exe")
    @patch.object(main, "probe_blender_version", return_value="Blender 5.2.0 LTS")
    @patch.object(main, "run_controlled_blender_command", side_effect=_fake_blender_full)
    @patch.dict(os.environ, {"PRODUCTDIRECTOR_BACKGROUND_WORKFLOW_FILE": ""}, clear=False)
    def test_independent_workflow_missing_file_fails_closed(self, *mocks) -> None:
        os.environ.pop("PRODUCTDIRECTOR_BACKGROUND_WORKFLOW_FILE", None)
        _, response = self._create_run("INDEPENDENT_BACKGROUND_WORKFLOW")
        self.assertEqual(response.status_code, 202, response.text)
        job_id = response.json()["job_id"]

        main.execute_job(job_id)

        job = self.client.get(f"/api/v1/jobs/{job_id}").json()
        self.assertEqual(job["status"], "QA_REJECTED", job)
        self.assertIn("现场 workflow 文件缺失", job.get("error", ""))
        self.assertFalse((main.RUNS / job_id / "strict" / "background").exists())

    @patch.object(main, "BLENDER", "C:/fake/blender.exe")
    @patch.object(main, "probe_blender_version", return_value="Blender 5.2.0 LTS")
    @patch.object(main, "run_controlled_blender_command", side_effect=_fake_blender_passes_only)
    @patch.dict(os.environ, {"PRODUCTDIRECTOR_BACKGROUND_SOURCE_ROOT": ""}, clear=False)
    def test_missing_strict_product_mask_from_controlled_render_fails_closed(self, *mocks) -> None:
        os.environ["PRODUCTDIRECTOR_BACKGROUND_SOURCE_ROOT"] = str(self.source_root)
        _, response = self._create_run("CONTROLLED_IMPORT")
        self.assertEqual(response.status_code, 202, response.text)
        job_id = response.json()["job_id"]
        self._write_controlled_source(job_id)

        main.execute_job(job_id)

        job = self.client.get(f"/api/v1/jobs/{job_id}").json()
        self.assertEqual(job["status"], "QA_REJECTED", job)
        self.assertIn("遮罩", job.get("error", ""))
        self.assertFalse((main.RUNS / job_id / "strict" / "product").exists())
        self.assertFalse((main.RUNS / job_id / "strict" / "mask").exists())

    @patch.object(main, "BLENDER", "C:/fake/blender.exe")
    @patch.object(main, "probe_blender_version", return_value="Blender 5.2.0 LTS")
    @patch.object(main, "run_controlled_blender_command", side_effect=_fake_blender_full)
    @patch.dict(os.environ, {"PRODUCTDIRECTOR_BACKGROUND_SOURCE_ROOT": ""}, clear=False)
    def test_controlled_import_cannot_reuse_other_job_directory(self, *mocks) -> None:
        os.environ["PRODUCTDIRECTOR_BACKGROUND_SOURCE_ROOT"] = str(self.source_root)
        _, response = self._create_run("CONTROLLED_IMPORT")
        self.assertEqual(response.status_code, 202, response.text)
        job_id = response.json()["job_id"]
        self._write_controlled_source(job_id, source_job="job-other")

        main.execute_job(job_id)

        job = self.client.get(f"/api/v1/jobs/{job_id}").json()
        self.assertEqual(job["status"], "QA_REJECTED", job)
        self.assertIn("背景源目录不存在", job.get("error", ""))
        self.assertFalse((main.RUNS / job_id / "strict" / "background").exists())

    def test_background_workflow_requires_explicit_source_mode(self) -> None:
        _, response = self._create_run(None, include_mode=False)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("background_source_mode", response.text)


if __name__ == "__main__":
    unittest.main()
