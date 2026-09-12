"""A05：远程 Worker 的受限输入/输出通道。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tests"))
sys.path.insert(0, str(PROJECT / "apps" / "api"))

import test_job_control as fixtures  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

main = fixtures.main
WORKER_TOKEN = "test-only-worker-" + "y" * 32


class WorkerChannelTests(unittest.TestCase):
    _create_plan = fixtures.JobControlAcceptanceTests._create_plan
    _create_asset = fixtures.JobControlAcceptanceTests._create_asset

    def setUp(self) -> None:
        fixtures.JobControlAcceptanceTests.setUp(self)
        # 本用例验证 Worker 身份边界，因此用真实的 worker 令牌而非依赖覆盖。
        main.app.dependency_overrides.clear()
        self._worker_identity.stop()
        self.worker = patch.object(main.security, "WORKER_TOKEN", WORKER_TOKEN)
        self.worker.start()
        self.addCleanup(self.worker.stop)
        self.anonymous = TestClient(main.app, base_url="http://127.0.0.1:8000")
        self.addCleanup(self.anonymous.close)
        self.headers = {"Authorization": f"Bearer {WORKER_TOKEN}"}

    tearDown = fixtures.JobControlAcceptanceTests.tearDown

    def _claimed_job(self) -> tuple[str, int]:
        plan = self._create_plan()
        self.client.post(f"/api/v1/plans/{plan['id']}/approve", json={"approved": True})
        with patch("productdirector_api.main.execute_job"):
            created = self.client.post("/api/v1/runs", json={"plan_id": plan["id"], "idempotency_key": "a05-channel"})
        job_id = created.json()["job_id"]
        claim = self.anonymous.post(
            "/internal/v1/workers/claim",
            headers=self.headers,
            json={"worker_id": main.security.WORKER_ID, "job_id": job_id},
        ).json()
        return job_id, int(claim["lease_epoch"])

    def test_input_requires_worker_token_and_active_lease(self) -> None:
        job_id, epoch = self._claimed_job()
        params = {"worker_id": main.security.WORKER_ID, "lease_epoch": epoch}
        self.assertEqual(self.anonymous.get(f"/internal/v1/workers/jobs/{job_id}/input", params=params).status_code, 401)
        ok = self.anonymous.get(f"/internal/v1/workers/jobs/{job_id}/input", params=params, headers=self.headers)
        self.assertEqual(ok.status_code, 200)
        body = ok.json()
        self.assertEqual(body["job_id"], job_id)
        self.assertEqual(len(body["plan"]["shots"]), 3)
        self.assertEqual(body["asset"]["kind"], "model")
        self.assertEqual(body["asset"]["download_url"], f"/internal/v1/workers/jobs/{job_id}/input/asset")
        self.assertNotIn("path", body["asset"])
        stale = self.anonymous.get(
            f"/internal/v1/workers/jobs/{job_id}/input",
            params={"worker_id": main.security.WORKER_ID, "lease_epoch": epoch + 5},
            headers=self.headers,
        )
        self.assertEqual(stale.status_code, 409)

    def test_asset_download_streams_the_bytes_and_checks_the_lease(self) -> None:
        job_id, epoch = self._claimed_job()
        params = {"worker_id": main.security.WORKER_ID, "lease_epoch": epoch}
        response = self.anonymous.get(
            f"/internal/v1/workers/jobs/{job_id}/input/asset", params=params, headers=self.headers
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, (PROJECT / "tests" / "fixtures" / "generic-product.glb").read_bytes())
        self.assertIn("generic-product", response.headers.get("content-disposition", ""))

    def test_artifact_upload_is_whitelisted_and_scoped(self) -> None:
        job_id, epoch = self._claimed_job()
        form = {"worker_id": main.security.WORKER_ID, "lease_epoch": str(epoch)}
        payload = b"0" * 4096
        video = self.anonymous.post(
            f"/internal/v1/workers/jobs/{job_id}/artifact",
            data={**form, "kind": "video"},
            files={"file": ("anything.mp4", payload, "video/mp4")},
            headers=self.headers,
        )
        self.assertEqual(video.status_code, 200)
        body = video.json()
        self.assertEqual(body["filename"], "preview.mp4")
        self.assertEqual(body["bytes"], len(payload))
        self.assertEqual(body["storage_reference"], f"{job_id}/preview.mp4")
        self.assertEqual((main.RUNS / job_id / "preview.mp4").read_bytes(), payload)

        manifest = self.anonymous.post(
            f"/internal/v1/workers/jobs/{job_id}/artifact",
            data={**form, "kind": "manifest"},
            files={"file": ("meta.json", b'{"job_id": "x"}', "application/json")},
            headers=self.headers,
        )
        self.assertEqual(manifest.status_code, 200)
        self.assertEqual(manifest.json()["filename"], "metadata.json")

        bad_kind = self.anonymous.post(
            f"/internal/v1/workers/jobs/{job_id}/artifact",
            data={**form, "kind": "shell"},
            files={"file": ("x.sh", b"echo hi", "text/plain")},
            headers=self.headers,
        )
        self.assertEqual(bad_kind.status_code, 422)
        empty = self.anonymous.post(
            f"/internal/v1/workers/jobs/{job_id}/artifact",
            data={**form, "kind": "video"},
            files={"file": ("x.mp4", b"", "video/mp4")},
            headers=self.headers,
        )
        self.assertEqual(empty.status_code, 422)

    def test_artifact_upload_rejects_oversized_and_stale_leases(self) -> None:
        job_id, epoch = self._claimed_job()
        form = {"worker_id": main.security.WORKER_ID, "lease_epoch": str(epoch)}
        with patch.dict(main.ARTIFACT_UPLOADS, {"video": ("preview.mp4", 1024)}):
            oversized = self.anonymous.post(
                f"/internal/v1/workers/jobs/{job_id}/artifact",
                data={**form, "kind": "video"},
                files={"file": ("x.mp4", b"0" * 2048, "video/mp4")},
                headers=self.headers,
            )
        self.assertEqual(oversized.status_code, 413)
        stale = self.anonymous.post(
            f"/internal/v1/workers/jobs/{job_id}/artifact",
            data={"worker_id": main.security.WORKER_ID, "lease_epoch": str(epoch + 3), "kind": "video"},
            files={"file": ("x.mp4", b"0" * 16, "video/mp4")},
            headers=self.headers,
        )
        self.assertEqual(stale.status_code, 409)


if __name__ == "__main__":
    unittest.main()
