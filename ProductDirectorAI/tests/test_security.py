"""A05 negative/positive HTTP tests with real authentication middleware."""
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.fernet import Fernet
from fastapi import HTTPException
from fastapi.testclient import TestClient

import test_job_control as fixtures
main = fixtures.main


class SecurityAcceptanceTests(unittest.TestCase):
    _create_asset = fixtures.JobControlAcceptanceTests._create_asset
    _create_plan = fixtures.JobControlAcceptanceTests._create_plan

    def setUp(self):
        fixtures.JobControlAcceptanceTests.setUp(self)
        main.app.dependency_overrides.clear()
        self._worker_identity.stop()
        self.worker_token = "test-only-worker-" + "y" * 32
        worker_config = patch.object(main.security, "WORKER_TOKEN", self.worker_token)
        worker_config.start()
        self.addCleanup(worker_config.stop)
        self.anonymous = TestClient(main.app, base_url="http://127.0.0.1:8000")
        self.addCleanup(self.anonymous.close)
        self.origin = {"Origin": "http://127.0.0.1:4173"}

    tearDown = fixtures.JobControlAcceptanceTests.tearDown

    def test_unauthenticated_and_worker_cannot_read_owner_data(self):
        for path in ["/api/v1/health", "/api/v1/assets", "/api/v1/jobs", "/api/v1/providers/minimax/status", "/openapi.json"]:
            self.assertEqual(self.anonymous.get(path).status_code, 401, path)
            self.assertEqual(self.anonymous.get(path, headers={"Authorization": f"Bearer {self.worker_token}"}).status_code, 401)
        with patch.object(main.security, "OWNER_TOKEN", ""):
            self.assertEqual(self.anonymous.get("/api/v1/jobs").status_code, 503)

    def test_session_csrf_expiry_logout_and_rotation(self):
        self.assertEqual(self.anonymous.post("/api/v1/session", json={"token": main.security.OWNER_TOKEN}).status_code, 403)
        response = self.anonymous.post("/api/v1/session", headers=self.origin, json={"token": main.security.OWNER_TOKEN})
        self.assertEqual(response.status_code, 200)
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertIn("SameSite=strict", response.headers["set-cookie"])
        token = response.json()["csrf_token"]
        uploaded = self.anonymous.post("/api/v1/assets", headers={**self.origin, "X-CSRF-Token": token},
                                       files={"file": ("fixture.glb", self.asset_file, "model/gltf-binary")})
        self.assertEqual(uploaded.status_code, 201)
        self.assertEqual(self.anonymous.get("/api/v1/assets").status_code, 200)
        for headers in [self.origin, {**self.origin, "X-CSRF-Token": "wrong"}, {"X-CSRF-Token": token},
                        {"Origin": "https://evil.example", "X-CSRF-Token": token, "User-Agent": "testclient"}]:
            self.assertEqual(self.anonymous.delete("/api/v1/session", headers=headers).status_code, 403)
        with main.connect() as db:
            stored = db.execute("SELECT * FROM auth_sessions").fetchone()
        self.assertNotEqual(stored["token_hash"], self.anonymous.cookies.get(main.security.COOKIE))
        with patch.object(main.security, "OWNER_TOKEN", "rotated-" + "z" * 32):
            self.assertEqual(self.anonymous.get("/api/v1/assets").status_code, 401)
        self.assertEqual(self.anonymous.delete("/api/v1/session", headers={**self.origin, "X-CSRF-Token": token}).status_code, 200)
        self.assertEqual(self.anonymous.get("/api/v1/assets").status_code, 401)
        self.anonymous.post("/api/v1/session", headers=self.origin, json={"token": main.security.OWNER_TOKEN})
        with main.connect() as db:
            db.execute("UPDATE auth_sessions SET expires_at = ?", (time.time() - 1,))
        self.assertEqual(self.anonymous.get("/api/v1/assets").status_code, 401)

    def test_worker_is_fail_closed_and_identity_bound(self):
        url = "/internal/v1/workers/claim"
        body = {"worker_id": main.security.WORKER_ID}
        self.assertEqual(self.anonymous.post(url, json=body).status_code, 401)
        self.assertEqual(self.client.post(url, json=body).status_code, 401)
        headers = {"Authorization": f"Bearer {self.worker_token}"}
        self.assertEqual(self.anonymous.post(url, json=body, headers=headers).status_code, 200)
        self.assertEqual(self.anonymous.post(url, json={"worker_id": "impostor"}, headers=headers).status_code, 403)
        self.assertEqual(self.anonymous.post(url, json=body, headers={**headers, **self.origin}).status_code, 403)
        with patch.object(main.security, "WORKER_TOKEN", ""):
            self.assertEqual(self.anonymous.post(url, json=body).status_code, 503)

    def test_owner_project_and_asset_boundaries(self):
        plan = self._create_plan()
        self.client.post(f"/api/v1/plans/{plan['id']}/approve", json={"approved": True})
        with patch.object(main, "execute_job"):
            result = self.client.post("/api/v1/runs", json={"plan_id": plan["id"]})
        job_id = result.json()["job_id"]
        with main.connect() as db:
            db.execute("INSERT INTO projects (id, owner_id, workspace_id, name, created_at) VALUES ('other-project', ?, ?, 'Other', ?)",
                       (main.DEFAULT_OWNER_ID, main.DEFAULT_WORKSPACE_ID, main.utc_now()))
        self.assertEqual(self.client.get("/api/v1/jobs", params={"project_id": "other-project"}).json(), [])
        for suffix in ["", "/events", "/video", "/manifest"]:
            self.assertEqual(self.client.get(f"/api/v1/jobs/{job_id}{suffix}", params={"project_id": "other-project"}).status_code, 403)
        self.assertEqual(self.client.post(f"/api/v1/jobs/{job_id}/cancel", params={"project_id": "other-project"}).status_code, 403)
        self.assertEqual(self.client.get("/api/v1/jobs", params={"owner_id": "forged"}).status_code, 403)
        asset_id = plan["product_asset_id"]
        self.assertEqual(self.client.get(f"/api/v1/assets/{asset_id}/content").content, self.asset_file)
        self.assertNotIn("path", self.client.get("/api/v1/assets").json()[0])
        with main.connect() as db:
            db.execute("UPDATE assets SET owner_id = 'other-owner' WHERE id = ?", (asset_id,))
        self.assertEqual(self.client.get(f"/api/v1/assets/{asset_id}/content").status_code, 403)
        self.assertEqual(self.client.get("/api/v1/assets").json(), [])
        self.assertEqual(self.client.post("/api/v1/plans/template", json={"product_asset_id": asset_id, "intent": "test"}).status_code, 403)

    def test_artifact_scope_and_relative_reference(self):
        own = main.RUNS / "job-a"
        other = main.RUNS / "job-b"
        own.mkdir(); other.mkdir()
        (own / "preview.mp4").write_bytes(b"fixture")
        (other / "preview.mp4").write_bytes(b"other fixture")
        self.assertEqual(main._resolve_job_artifact_path("job-a", "job-a/preview.mp4"), own / "preview.mp4")
        for path in [str(other / "preview.mp4"), "job-a/../job-b/preview.mp4", "job-b/preview.mp4"]:
            with self.assertRaises(HTTPException) as caught:
                main._resolve_job_artifact_path("job-a", path)
            self.assertEqual(caught.exception.status_code, 403)

    def test_linux_credentials_require_key_and_reject_corruption(self):
        # Patch the module reference, not global os.name/Path platform behavior.
        env = {"PRODUCTDIRECTOR_SECRET_KEY": Fernet.generate_key().decode()}
        with patch.object(main, "os", SimpleNamespace(name="posix", getenv=lambda key, default="": env.get(key, default))):
            encrypted = main.protect_secret("fixture-only-secret")
            self.assertNotIn(b"fixture-only-secret", encrypted)
            self.assertEqual(main.unprotect_secret(encrypted), "fixture-only-secret")
            for value in [b"windows-dpapi", b"linuxv1:legacy", encrypted[:-1] + b"!"]:
                with self.assertRaises(HTTPException): main.unprotect_secret(value)
            env.clear()
            with self.assertRaises(HTTPException) as caught: main.protect_secret("fixture")
            self.assertEqual(caught.exception.status_code, 503)

    def test_provider_base_rejects_untrusted_hosts(self):
        self.assertEqual(main._normalize_minimax_base_url("https://api.minimaxi.com/v1/"), "https://api.minimaxi.com")
        for value in ["http://127.0.0.1", "https://api.minimaxi.com.evil.example", "https://api.minimaxi.com@evil.example", "https://api.minimaxi.com/?key=secret"]:
            with self.assertRaises(HTTPException): main._normalize_minimax_base_url(value)
