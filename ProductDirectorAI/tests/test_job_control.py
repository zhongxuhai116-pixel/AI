from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "apps" / "api"))

from productdirector_api import main  # noqa: E402


class JobControlAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_db_path = main.DB_PATH
        self._original_var = main.VAR
        self._original_uploads = main.UPLOADS
        self._original_runs = main.RUNS
        self._workdir = Path(tempfile.mkdtemp(prefix="pdapi-test-"))
        main.VAR = self._workdir
        main.DB_PATH = main.VAR / "productdirector.db"
        main.UPLOADS = main.VAR / "uploads"
        main.RUNS = main.VAR / "runs"
        for folder in (main.VAR, main.UPLOADS, main.RUNS):
            folder.mkdir(parents=True, exist_ok=True)
        main.initialize_db()
        if "DB_PATH" in os.environ:
            self._original_db_env = os.environ["DB_PATH"]
        else:
            self._original_db_env = None
        os.environ["DB_PATH"] = str(main.DB_PATH)
        self.client = TestClient(main.app)
        self.asset_file = (PROJECT / "tests" / "fixtures" / "generic-product.glb").read_bytes()

    def tearDown(self) -> None:
        self.client.close()
        main.DB_PATH = self._original_db_path
        main.VAR = self._original_var
        main.UPLOADS = self._original_uploads
        main.RUNS = self._original_runs
        if self._original_db_env is None:
            os.environ.pop("DB_PATH", None)
        else:
            os.environ["DB_PATH"] = self._original_db_env
        main.initialize_db()
        if self._workdir.exists():
            shutil.rmtree(self._workdir, ignore_errors=True)

    def _create_asset(self) -> dict:
        asset = self.client.post(
            "/api/v1/assets",
            files={"file": ("generic-product.glb", self.asset_file, "model/gltf-binary")},
        )
        self.assertEqual(asset.status_code, 201)
        return asset.json()

    def _create_plan(self, project_id: str = main.DEFAULT_PROJECT_ID, owner_id: str = main.DEFAULT_OWNER_ID) -> dict:
        asset = self._create_asset()
        plan = self.client.post(
            "/api/v1/plans/template",
            json={
                "product_asset_id": asset["id"],
                "intent": "V1 可靠任务验收",
                "project_id": project_id,
                "owner_id": owner_id,
            },
        )
        self.assertEqual(plan.status_code, 201)
        return plan.json()

    def _ensure_contract_count(self, expected: int) -> None:
        with main.connect() as db:
            count = db.execute("SELECT COUNT(*) AS c FROM plan_contracts").fetchone()["c"]
        self.assertEqual(count, expected)

    def test_a02_contract_version_is_immutable_on_plan_update(self) -> None:
        plan = self._create_plan()
        with main.connect() as db:
            row = db.execute("SELECT * FROM plan_contracts WHERE plan_id = ?", (plan["id"],)).fetchall()
            self.assertEqual(len(row), 1)
            first_version = row[0]["version"]
            self.assertEqual(first_version, 1)

        payload = plan["shots"]
        payload[0]["duration_frames"] = 48
        payload[1]["duration_frames"] = 48
        payload[2]["duration_frames"] = 48
        update = self.client.patch(
            f"/api/v1/plans/{plan['id']}",
            json={
                "intent": "A02 计划更新验证",
                "shots": payload,
                "owner_id": main.DEFAULT_OWNER_ID,
                "project_id": main.DEFAULT_PROJECT_ID,
            },
        )
        self.assertEqual(update.status_code, 200)

        with main.connect() as db:
            row = db.execute(
                "SELECT * FROM plan_contracts WHERE plan_id = ? ORDER BY version ASC", (plan["id"],)
            ).fetchall()
            versions = [r["version"] for r in row]
        self.assertEqual(versions, [1, 2])
        self.assertEqual(update.json()["contract_version"], 2)

    def test_a03_run_idempotency_conflict(self) -> None:
        plan = self._create_plan()
        self.client.post(f"/api/v1/plans/{plan['id']}/approve", json={"approved": True})

        with patch("productdirector_api.main.execute_job") as mocked_execute_job:
            mocked_execute_job.side_effect = (
                lambda job_id: main.update_job(
                    job_id,
                    status="SUCCEEDED",
                    stage="ARTIFACT",
                    progress=100,
                    error=None,
                )
            )
            first = self.client.post("/api/v1/runs", json={"plan_id": plan["id"], "idempotency_key": "dup-key"})
            second = self.client.post("/api/v1/runs", json={"plan_id": plan["id"], "idempotency_key": "dup-key"})

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(first.json()["run_id"], second.json()["run_id"])
        self.assertEqual(first.json()["job_id"], second.json()["job_id"])
        self.assertTrue(first.json()["created"])
        self.assertFalse(second.json()["created"])
        self.assertTrue(second.json()["reused_idempotent"])

        self.client.patch(
            f"/api/v1/plans/{plan['id']}",
            json={
                "intent": "A03 更新到新镜头",
                "shots": plan["shots"],
                "owner_id": main.DEFAULT_OWNER_ID,
                "project_id": main.DEFAULT_PROJECT_ID,
            },
        )

        conflict = self.client.post("/api/v1/runs", json={"plan_id": plan["id"], "idempotency_key": "dup-key"})
        self.assertEqual(conflict.status_code, 409)

    def test_a03_run_access_requires_project_contract_context(self) -> None:
        plan = self._create_plan()
        with main.connect() as db:
            now = main.utc_now()
            db.execute(
                "INSERT INTO projects(id, owner_id, workspace_id, name, created_at) VALUES (?, ?, ?, ?, ?)",
                ("project-other", main.DEFAULT_OWNER_ID, "workspace-default", "Other Project", now),
            )
            db.commit()

        self.client.post(f"/api/v1/plans/{plan['id']}/approve", json={"approved": True})
        denied = self.client.post(
            "/api/v1/runs",
            json={"plan_id": plan["id"], "owner_id": main.DEFAULT_OWNER_ID, "project_id": "project-other"},
        )
        self.assertEqual(denied.status_code, 403)

    def test_a04_worker_lease_blocks_stale_completion(self) -> None:
        plan = self._create_plan()
        self.client.post(f"/api/v1/plans/{plan['id']}/approve", json={"approved": True})

        with patch("productdirector_api.main.execute_job"):
            created = self.client.post("/api/v1/runs", json={"plan_id": plan["id"], "idempotency_key": "a04-lease"})
        self.assertEqual(created.status_code, 202)
        job_id = created.json()["job_id"]

        first = self.client.post(
            "/internal/v1/workers/claim",
            json={"worker_id": "worker-a", "job_id": job_id},
        )
        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.json()["claimed"])
        first_epoch = first.json()["lease_epoch"]

        expired = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        with main.connect() as db:
            db.execute(
                "UPDATE run_jobs SET lease_expires_at = ? WHERE job_id = ?",
                (expired, job_id),
            )

        second = self.client.post(
            "/internal/v1/workers/claim",
            json={"worker_id": "worker-b", "job_id": job_id},
        )
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["claimed"])
        self.assertGreater(second.json()["lease_epoch"], first_epoch)

        stale = self.client.post(
            f"/internal/v1/workers/jobs/{job_id}/complete",
            json={"worker_id": "worker-a", "lease_epoch": first_epoch},
        )
        self.assertEqual(stale.status_code, 409)

        fresh = self.client.post(
            f"/internal/v1/workers/jobs/{job_id}/fail",
            json={"worker_id": "worker-b", "lease_epoch": second.json()["lease_epoch"], "error": "forced failure"},
        )
        self.assertEqual(fresh.status_code, 200)
        self.assertEqual(fresh.json()["status"], "FAILED")

    def test_a04_events_can_resume_after_last_event_id(self) -> None:
        plan = self._create_plan()
        self.client.post(f"/api/v1/plans/{plan['id']}/approve", json={"approved": True})

        with patch("productdirector_api.main.execute_job"):
            created = self.client.post("/api/v1/runs", json={"plan_id": plan["id"], "idempotency_key": "a04-events"})
        job_id = created.json()["job_id"]

        claim = self.client.post("/internal/v1/workers/claim", json={"worker_id": "worker-a", "job_id": job_id})
        self.assertTrue(claim.json()["claimed"])
        heartbeat = self.client.post(
            f"/internal/v1/workers/jobs/{job_id}/heartbeat",
            json={"worker_id": "worker-a", "lease_epoch": claim.json()["lease_epoch"]},
        )
        self.assertEqual(heartbeat.status_code, 200)

        events = self.client.get(f"/api/v1/jobs/{job_id}/events")
        self.assertEqual(events.status_code, 200)
        self.assertIn("event: job.created", events.text)
        self.assertIn("event: worker.claimed", events.text)
        self.assertIn("event: worker.heartbeat", events.text)

        resumed = self.client.get(f"/api/v1/jobs/{job_id}/events", headers={"Last-Event-ID": "2"})
        self.assertEqual(resumed.status_code, 200)
        self.assertNotIn("event: job.created", resumed.text)
        self.assertIn("event: worker.heartbeat", resumed.text)

    def test_a04_reconcile_requeues_expired_running_job(self) -> None:
        plan = self._create_plan()
        self.client.post(f"/api/v1/plans/{plan['id']}/approve", json={"approved": True})

        with patch("productdirector_api.main.execute_job"):
            created = self.client.post("/api/v1/runs", json={"plan_id": plan["id"], "idempotency_key": "a04-reconcile"})
        job_id = created.json()["job_id"]

        first = self.client.post("/internal/v1/workers/claim", json={"worker_id": "worker-a", "job_id": job_id})
        self.assertTrue(first.json()["claimed"])
        expired = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        with main.connect() as db:
            db.execute("UPDATE run_jobs SET lease_expires_at = ? WHERE job_id = ?", (expired, job_id))

        reconciled = self.client.post("/internal/v1/workers/reconcile")
        self.assertEqual(reconciled.status_code, 200)
        self.assertEqual(reconciled.json()["reconciled"], 1)
        self.assertEqual(self.client.get(f"/api/v1/jobs/{job_id}").json()["status"], "QUEUED")

        second = self.client.post("/internal/v1/workers/claim", json={"worker_id": "worker-b", "job_id": job_id})
        self.assertTrue(second.json()["claimed"])
        self.assertGreater(second.json()["lease_epoch"], first.json()["lease_epoch"])

    def test_a04_worker_once_executes_claimed_job(self) -> None:
        plan = self._create_plan()
        self.client.post(f"/api/v1/plans/{plan['id']}/approve", json={"approved": True})

        with patch("productdirector_api.main.execute_job"):
            created = self.client.post("/api/v1/runs", json={"plan_id": plan["id"], "idempotency_key": "a04-worker-once"})
        job_id = created.json()["job_id"]
        run_dir = main.RUNS / job_id

        def fake_render(job_id_arg, asset, run_dir_arg, output, plan_path, output_spec):
            output.write_bytes(b"0" * 2048)

        fake_probe = Mock(
            returncode=0,
            stdout=json.dumps(
                {
                    "streams": [{"width": 540, "height": 960, "r_frame_rate": "24/1", "nb_frames": "144"}],
                    "format": {"duration": "6.000"},
                }
            ),
            stderr="",
        )
        original_ffprobe = main.FFPROBE
        main.FFPROBE = "ffprobe"
        try:
            with patch("productdirector_api.main.render_glb_job", side_effect=fake_render), patch(
                "productdirector_api.main.subprocess.run", return_value=fake_probe
            ):
                result = main.run_worker_once("worker-cli-test")
        finally:
            main.FFPROBE = original_ffprobe

        self.assertTrue(result["claimed"])
        self.assertEqual(result["job_id"], job_id)
        self.assertEqual(result["status"], "SUCCEEDED")
        self.assertTrue((run_dir / "metadata.json").exists())


if __name__ == "__main__":
    unittest.main()
