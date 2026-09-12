"""V2 H3/ComfyUI Provider 集成：提交、失败记录与产物回收。"""
from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tests"))
sys.path.insert(0, str(PROJECT / "apps" / "api"))

import test_job_control as fixtures  # noqa: E402
from productdirector_api.providers import comfyui  # noqa: E402

main = fixtures.main
FIXTURE_GLB = PROJECT / "tests" / "fixtures" / "generic-product.glb"


def png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 120, 40)).save(buffer, format="PNG")
    return buffer.getvalue()


class H3ProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        fixtures.JobControlAcceptanceTests.setUp(self)
        upload = self.client.post(
            "/api/v1/assets", files={"file": ("product.png", png_bytes(), "image/png")}
        )
        self.assertEqual(upload.status_code, 201)
        self.image_asset = upload.json()

    tearDown = fixtures.JobControlAcceptanceTests.tearDown

    def _reconstruct(self, **overrides):
        body = {"product_asset_id": self.image_asset["id"], "crop": [0, 0, 256, 256], **overrides}
        return self.client.post("/api/v1/providers/h3/reconstruct", json=body)

    def _provider_rows(self) -> list[dict]:
        with main.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM provider_jobs ORDER BY created_at").fetchall()]

    # --- 状态 ---

    def test_status_reports_unreachable_provider_without_failing(self) -> None:
        with patch.object(comfyui, "health", return_value={"reachable": False, "base_url": "http://127.0.0.1:8188"}):
            response = self.client.get("/api/v1/providers/h3/status")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["reachable"])

    def test_status_requires_authentication(self) -> None:
        anonymous = fixtures.JobControlAcceptanceTests.__dict__  # noqa: F841 - 仅说明依赖真实鉴权
        from fastapi.testclient import TestClient
        client = TestClient(main.app)
        self.assertEqual(client.get("/api/v1/providers/h3/status").status_code, 401)

    # --- 提交 ---

    def test_reconstruct_rejects_non_image_assets(self) -> None:
        upload = self.client.post(
            "/api/v1/assets",
            files={"file": ("model.glb", FIXTURE_GLB.read_bytes(), "model/gltf-binary")},
        )
        response = self.client.post(
            "/api/v1/providers/h3/reconstruct", json={"product_asset_id": upload.json()["id"]}
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("图片", response.json()["detail"])

    def test_reconstruct_rejects_invalid_crop(self) -> None:
        response = self._reconstruct(crop=[0, 0, 10, 10])
        self.assertEqual(response.status_code, 422)

    def test_reconstruct_submits_and_records_a_running_provider_job(self) -> None:
        with patch.object(comfyui, "upload_image", return_value="remote.png") as upload, patch.object(
            comfyui, "submit", return_value="prompt-123"
        ) as submit:
            response = self._reconstruct()
        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertEqual(body["external_id"], "prompt-123")
        self.assertEqual(body["status"], "RUNNING")
        upload.assert_called_once()
        graph = submit.call_args.args[0]
        self.assertEqual(graph["1"]["inputs"]["image"], "remote.png")
        self.assertEqual(graph["7"]["inputs"]["steps"], 50)
        self.assertEqual(graph["7"]["inputs"]["cfg"], 5.0)

        rows = self._provider_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "RUNNING")
        self.assertEqual(rows[0]["external_id"], "prompt-123")
        self.assertEqual(json.loads(rows[0]["request_payload"])["asset_id"], self.image_asset["id"])

    def test_reconstruct_records_failure_instead_of_rolling_it_back(self) -> None:
        with patch.object(comfyui, "upload_image", return_value="remote.png"), patch.object(
            comfyui, "submit", side_effect=comfyui.ComfyUIError("provider refused")
        ):
            response = self._reconstruct()
        self.assertEqual(response.status_code, 503)
        rows = self._provider_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "FAILED")
        self.assertIn("provider refused", rows[0]["error"])

    # --- 回收 ---

    def _submitted_job(self) -> str:
        with patch.object(comfyui, "upload_image", return_value="remote.png"), patch.object(
            comfyui, "submit", return_value="prompt-xyz"
        ):
            response = self._reconstruct()
        return response.json()["provider_job_id"]

    def test_job_status_collects_the_artifact_and_registers_a_model_asset(self) -> None:
        provider_job_id = self._submitted_job()
        record = {"status": {"completed": True, "status_str": "success"},
                  "outputs": {"10": {"3d": [{"filename": "out.glb", "subfolder": "sub", "type": "output"}]}}}
        with patch.object(comfyui, "history", return_value=record), patch.object(
            comfyui, "download", return_value=FIXTURE_GLB.read_bytes()
        ) as download:
            response = self.client.get(f"/api/v1/providers/h3/jobs/{provider_job_id}")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "SUCCEEDED")
        self.assertIsNotNone(body["artifact_asset_id"])
        self.assertEqual(body["artifact"]["bytes"], FIXTURE_GLB.stat().st_size)
        self.assertTrue(body["artifact"]["glb"]["meshes"] >= 1)
        download.assert_called_once()

        assets = self.client.get("/api/v1/assets").json()
        created = next(item for item in assets if item["id"] == body["artifact_asset_id"])
        self.assertEqual(created["kind"], "model")
        self.assertEqual(created["sha256"], body["artifact"]["sha256"])
        self.assertIn("H3 重建", created["name"])

    def test_job_status_is_idempotent_after_success(self) -> None:
        provider_job_id = self._submitted_job()
        record = {"status": {"completed": True, "status_str": "success"},
                  "outputs": {"10": {"3d": [{"filename": "out.glb", "subfolder": "", "type": "output"}]}}}
        with patch.object(comfyui, "history", return_value=record), patch.object(
            comfyui, "download", return_value=FIXTURE_GLB.read_bytes()
        ) as download:
            first = self.client.get(f"/api/v1/providers/h3/jobs/{provider_job_id}").json()
            second = self.client.get(f"/api/v1/providers/h3/jobs/{provider_job_id}").json()
        self.assertEqual(first["artifact_asset_id"], second["artifact_asset_id"])
        self.assertEqual(download.call_count, 1)
        self.assertEqual(len(self._provider_rows()), 1)

    def test_job_status_fails_when_the_artifact_is_not_a_valid_glb(self) -> None:
        provider_job_id = self._submitted_job()
        record = {"status": {"completed": True, "status_str": "success"},
                  "outputs": {"10": {"3d": [{"filename": "out.glb", "subfolder": "", "type": "output"}]}}}
        with patch.object(comfyui, "history", return_value=record), patch.object(
            comfyui, "download", return_value=b"not-a-glb"
        ):
            body = self.client.get(f"/api/v1/providers/h3/jobs/{provider_job_id}").json()
        self.assertEqual(body["status"], "FAILED")
        self.assertIn("GLB", body["error"])
        self.assertIsNone(body["artifact_asset_id"])

    def test_job_status_fails_when_provider_returns_no_glb(self) -> None:
        provider_job_id = self._submitted_job()
        record = {"status": {"completed": True, "status_str": "success"},
                  "outputs": {"10": {"text": ["nothing useful"]}}}
        with patch.object(comfyui, "history", return_value=record):
            body = self.client.get(f"/api/v1/providers/h3/jobs/{provider_job_id}").json()
        self.assertEqual(body["status"], "FAILED")
        self.assertIn("GLB", body["error"])

    def test_job_status_reports_provider_failure(self) -> None:
        provider_job_id = self._submitted_job()
        record = {"status": {"completed": True, "status_str": "error"}, "outputs": {}}
        with patch.object(comfyui, "history", return_value=record):
            body = self.client.get(f"/api/v1/providers/h3/jobs/{provider_job_id}").json()
        self.assertEqual(body["status"], "FAILED")

    def test_unknown_provider_job_returns_404(self) -> None:
        self.assertEqual(self.client.get("/api/v1/providers/h3/jobs/nope").status_code, 404)

    # --- 视频生成 ---

    def _submit_video(self, **overrides):
        body = {
            "product_asset_id": self.image_asset["id"],
            "reference_video": "拳击靶-人物击打参考.mp4",
            "prompt": "replace the target with the product shown in <Picture 1>",
            "crop": [0, 0, 256, 256],
            **overrides,
        }
        return self.client.post("/api/v1/providers/h3/video", json=body)

    def test_video_submission_builds_the_h3_graph_and_tracks_the_job(self) -> None:
        with patch.object(comfyui, "upload_image", return_value="remote.png"), patch.object(
            comfyui, "submit", return_value="prompt-video"
        ) as submit:
            response = self._submit_video()
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["operation"], "GENERATE_VIDEO")
        graph = submit.call_args.args[0]
        self.assertEqual(graph["1"]["class_type"], "UNETLoader")
        self.assertEqual(graph["7"]["class_type"], "MiniMaxH3ReferenceToVideo")
        self.assertEqual(graph["7"]["inputs"]["ref_images.ref_image_0"], ["13", 0])
        self.assertEqual(graph["7"]["inputs"]["ref_videos.ref_video_0"], ["11", 0])
        self.assertEqual(graph["11"]["class_type"], "GetVideoComponents")
        self.assertEqual(graph["16"]["class_type"], "SamplerCustomAdvanced")
        self.assertEqual(graph["20"]["class_type"], "SaveVideo")
        self.assertEqual(graph["7"]["inputs"]["length"], 124)
        self.assertEqual(graph["9"]["inputs"]["steps"], 4)

        rows = self._provider_rows()
        self.assertEqual(rows[0]["operation"], "GENERATE_VIDEO")
        self.assertEqual(rows[0]["external_id"], "prompt-video")
        payload = json.loads(rows[0]["request_payload"])
        self.assertEqual(payload["reference_video"], "拳击靶-人物击打参考.mp4")
        self.assertIn("prompt_sha256", payload)
        self.assertNotIn("replace the target", rows[0]["request_payload"])

    def test_video_submission_rejects_missing_reference_video(self) -> None:
        self.assertEqual(self._submit_video(reference_video="   ").status_code, 422)

    def test_video_records_provider_failure(self) -> None:
        with patch.object(comfyui, "upload_image", return_value="remote.png"), patch.object(
            comfyui, "submit", side_effect=comfyui.ComfyUIError("node error")
        ):
            response = self._submit_video()
        self.assertEqual(response.status_code, 503)
        rows = self._provider_rows()
        self.assertEqual(rows[0]["operation"], "GENERATE_VIDEO")
        self.assertEqual(rows[0]["status"], "FAILED")

    def _submitted_video_job(self) -> str:
        with patch.object(comfyui, "upload_image", return_value="remote.png"), patch.object(
            comfyui, "submit", return_value="prompt-video"
        ):
            return self._submit_video().json()["provider_job_id"]

    def test_video_status_collects_the_artifact_and_serves_it(self) -> None:
        provider_job_id = self._submitted_video_job()
        record = {
            "status": {"completed": True, "status_str": "success"},
            "outputs": {"20": {"video": [{"filename": "clip_00001_.mp4", "subfolder": "productdirector/x", "type": "output"}]}},
        }
        payload = b"\x00\x00\x00\x18ftypmp42" + b"0" * 2048
        with patch.object(comfyui, "history", return_value=record), patch.object(
            comfyui, "download", return_value=payload
        ) as download:
            body = self.client.get(f"/api/v1/providers/h3/jobs/{provider_job_id}").json()
        self.assertEqual(body["status"], "SUCCEEDED")
        self.assertEqual(body["artifact"]["kind"], "video")
        self.assertEqual(body["artifact"]["bytes"], len(payload))
        self.assertIsNone(body["artifact_asset_id"])
        download.assert_called_once()

        served = self.client.get(f"/api/v1/providers/h3/jobs/{provider_job_id}/artifact")
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served.content, payload)

    def test_video_status_fails_when_no_video_output(self) -> None:
        provider_job_id = self._submitted_video_job()
        record = {"status": {"completed": True, "status_str": "success"}, "outputs": {"20": {"text": ["nope"]}}}
        with patch.object(comfyui, "history", return_value=record):
            body = self.client.get(f"/api/v1/providers/h3/jobs/{provider_job_id}").json()
        self.assertEqual(body["status"], "FAILED")
        self.assertIn("视频", body["error"])

    def test_artifact_endpoint_conflicts_before_the_job_finishes(self) -> None:
        provider_job_id = self._submitted_video_job()
        with patch.object(comfyui, "history", return_value=None):
            self.client.get(f"/api/v1/providers/h3/jobs/{provider_job_id}")
        self.assertEqual(
            self.client.get(f"/api/v1/providers/h3/jobs/{provider_job_id}/artifact").status_code, 409
        )


if __name__ == "__main__":
    unittest.main()
