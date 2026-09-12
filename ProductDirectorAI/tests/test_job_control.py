from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "apps" / "api"))

from productdirector_api import main  # noqa: E402

# 租约/恢复用例聚焦调度语义，质量门在这里显式 mock；媒体质量门本身由
# test_media_quality.py 用真实 FFmpeg 产物验证。
QA_PASS = {"passed": True, "failures": [], "samples": [], "black_seconds_ratio": 0.0}


class JobControlAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._owner_token = patch.object(main.security, "OWNER_TOKEN", "test-only-owner-" + "x" * 32)
        self._owner_token.start()
        self.addCleanup(self._owner_token.stop)
        # These tests exercise lease contracts. Security boundary tests use real dependencies.
        main.app.dependency_overrides[main._ensure_worker_token] = lambda: None
        self.addCleanup(main.app.dependency_overrides.clear)
        self._worker_identity = patch.object(main.security, "check_worker_id")
        self._worker_identity.start()
        self.addCleanup(self._worker_identity.stop)
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
        self.client = TestClient(main.app, headers={"Authorization": f"Bearer {main.security.OWNER_TOKEN}"})
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
            ), patch("productdirector_api.main.media_quality_report", return_value=dict(QA_PASS)):
                result = main.run_worker_once("worker-cli-test")
        finally:
            main.FFPROBE = original_ffprobe

        self.assertTrue(result["claimed"])
        self.assertEqual(result["job_id"], job_id)
        self.assertEqual(result["status"], "SUCCEEDED")
        self.assertTrue((run_dir / "metadata.json").exists())
        video = self.client.get(f"/api/v1/jobs/{job_id}/video")
        self.assertEqual(video.status_code, 200)
        self.assertEqual(video.content, b"0" * 2048)
        self.assertEqual(self.client.get(f"/api/v1/jobs/{job_id}/manifest").json()["job_id"], job_id)


    # --- A04 真实中断与恢复：竞争窗口、长任务续租、进程被杀后重领 ---

    def _create_queued_job(self, idempotency_key: str) -> str:
        plan = self._create_plan()
        self.client.post(f"/api/v1/plans/{plan['id']}/approve", json={"approved": True})
        with patch("productdirector_api.main.execute_job"):
            created = self.client.post(
                "/api/v1/runs", json={"plan_id": plan["id"], "idempotency_key": idempotency_key}
            )
        self.assertEqual(created.status_code, 202)
        return created.json()["job_id"]

    def _expire_lease(self, job_id: str, seconds: int = 5) -> None:
        expired = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
        with main.connect() as db:
            db.execute("UPDATE run_jobs SET lease_expires_at = ? WHERE job_id = ?", (expired, job_id))

    def _lease_row(self, job_id: str) -> sqlite3.Row:
        with main.connect() as db:
            return db.execute(
                "SELECT * FROM run_jobs WHERE job_id = ? ORDER BY created_at DESC, attempt DESC LIMIT 1",
                (job_id,),
            ).fetchone()

    def _fake_render_tools(self, delay: float = 0.0, started: threading.Event | None = None):
        """mock 渲染与 ffprobe，避免把本地测试写进真实 Blender 依赖。"""

        def fake_render(job_id_arg, asset, run_dir_arg, output, plan_path, output_spec):
            if started is not None:
                started.set()
            if delay:
                time.sleep(delay)
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
        return fake_render, fake_probe

    def test_a04_cancel_wins_over_late_completion(self) -> None:
        job_id = self._create_queued_job("a04-cancel-race")
        claim = self.client.post("/internal/v1/workers/claim", json={"worker_id": "worker-a", "job_id": job_id})
        self.assertTrue(claim.json()["claimed"])

        cancelled = self.client.post(f"/api/v1/jobs/{job_id}/cancel")
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["status"], "CANCEL_REQUESTED")

        # 渲染刚结束的迟到完成回报不能把用户已取消的任务改回成功。
        late = self.client.post(
            f"/internal/v1/workers/jobs/{job_id}/complete",
            json={"worker_id": "worker-a", "lease_epoch": claim.json()["lease_epoch"], "output_path": "preview.mp4"},
        )
        self.assertEqual(late.status_code, 200)
        self.assertEqual(late.json()["status"], "CANCELLED")
        self.assertEqual(late.json()["progress"], 0)
        self.assertEqual(self.client.get(f"/api/v1/jobs/{job_id}/video").status_code, 409)

        # 终态之后迟到的进度写入同样不能把任务改回运行中。
        main.update_job(job_id, status="RUNNING", stage="RENDER", progress=50)
        job = self.client.get(f"/api/v1/jobs/{job_id}").json()
        self.assertEqual(job["status"], "CANCELLED")
        self.assertEqual(job["progress"], 0)

    def test_a04_reclaimed_job_ignores_stale_worker_writes(self) -> None:
        job_id = self._create_queued_job("a04-stale-writes")
        first = self.client.post("/internal/v1/workers/claim", json={"worker_id": "worker-a", "job_id": job_id}).json()
        self.assertTrue(first["claimed"])
        self._expire_lease(job_id)
        second = self.client.post("/internal/v1/workers/claim", json={"worker_id": "worker-b", "job_id": job_id}).json()
        self.assertTrue(second["claimed"])
        lease_before = self._lease_row(job_id)

        stale_complete = self.client.post(
            f"/internal/v1/workers/jobs/{job_id}/complete",
            json={"worker_id": "worker-a", "lease_epoch": first["lease_epoch"], "output_path": "preview.mp4"},
        )
        self.assertEqual(stale_complete.status_code, 409)
        stale_heartbeat = self.client.post(
            f"/internal/v1/workers/jobs/{job_id}/heartbeat",
            json={"worker_id": "worker-a", "lease_epoch": first["lease_epoch"]},
        )
        self.assertEqual(stale_heartbeat.status_code, 409)

        # 直接调用内部更新路径也必须拒绝旧 epoch，且不能顺带释放新 worker 的租约。
        with self.assertRaises(HTTPException) as caught:
            main.update_job_with_lease(job_id, "worker-a", first["lease_epoch"], progress=99)
        self.assertEqual(caught.exception.status_code, 409)
        main.release_worker_lease(job_id, "worker-a", first["lease_epoch"], "worker.failed")

        lease_after = self._lease_row(job_id)
        self.assertEqual(lease_after["lease_owner"], "worker-b")
        self.assertEqual(lease_after["lease_epoch"], lease_before["lease_epoch"])
        self.assertEqual(lease_after["lease_expires_at"], lease_before["lease_expires_at"])
        job = self.client.get(f"/api/v1/jobs/{job_id}").json()
        self.assertEqual(job["status"], "RUNNING")
        self.assertEqual(job["progress"], 0)
        heartbeat = self.client.post(
            f"/internal/v1/workers/jobs/{job_id}/heartbeat",
            json={"worker_id": "worker-b", "lease_epoch": second["lease_epoch"]},
        )
        self.assertEqual(heartbeat.status_code, 200)

    def test_a04_long_render_renews_lease_and_blocks_rival_claim(self) -> None:
        job_id = self._create_queued_job("a04-long-render")
        original_ffprobe = main.FFPROBE
        original_lease_seconds = main.LEASE_SECONDS
        original_heartbeat = main.HEARTBEAT_SECONDS
        main.FFPROBE = "ffprobe"
        main.LEASE_SECONDS = 1
        main.HEARTBEAT_SECONDS = 0.2
        started = threading.Event()
        fake_render, fake_probe = self._fake_render_tools(delay=2.0, started=started)
        worker: threading.Thread | None = None
        try:
            claim = main.claim_job("worker-long", job_id)
            self.assertTrue(claim["claimed"])
            worker = threading.Thread(
                target=main.execute_claimed_job,
                args=(job_id, "worker-long", int(claim["lease_epoch"])),
                daemon=True,
            )
            with patch("productdirector_api.main.render_glb_job", side_effect=fake_render), patch(
                "productdirector_api.main.subprocess.run", return_value=fake_probe
            ), patch("productdirector_api.main.media_quality_report", return_value=dict(QA_PASS)):
                worker.start()
                self.assertTrue(started.wait(5), "mock 渲染未启动")
                # 已超过一个 LEASE_SECONDS：心跳缺失时第二个 worker 会合法抢走任务。
                time.sleep(1.4)
                rival = main.claim_job("worker-rival", job_id)
                self.assertFalse(rival.get("claimed"))
                worker.join(timeout=15)
        finally:
            main.FFPROBE = original_ffprobe
            main.LEASE_SECONDS = original_lease_seconds
            main.HEARTBEAT_SECONDS = original_heartbeat
        if worker is not None:
            self.assertFalse(worker.is_alive(), "worker 线程未结束")
        job = self.client.get(f"/api/v1/jobs/{job_id}").json()
        self.assertEqual(job["status"], "SUCCEEDED")
        self.assertEqual(job["progress"], 100)
        self.assertIsNone(self._lease_row(job_id)["lease_owner"])

    def test_a04_render_without_heartbeat_loses_lease_to_rival(self) -> None:
        """负向对照：证明上面的续租断言确实由心跳线程带来。"""
        job_id = self._create_queued_job("a04-no-heartbeat")
        original_ffprobe = main.FFPROBE
        original_lease_seconds = main.LEASE_SECONDS
        main.FFPROBE = "ffprobe"
        main.LEASE_SECONDS = 1
        started = threading.Event()
        fake_render, fake_probe = self._fake_render_tools(delay=2.0, started=started)
        worker: threading.Thread | None = None

        class NoHeartbeat:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def start(self) -> None:
                pass

            def stop(self) -> None:
                pass

        try:
            claim = main.claim_job("worker-slow", job_id)
            self.assertTrue(claim["claimed"])
            worker = threading.Thread(
                target=main.execute_claimed_job,
                args=(job_id, "worker-slow", int(claim["lease_epoch"])),
                daemon=True,
            )
            with patch.object(main, "HeartbeatKeeper", NoHeartbeat), patch(
                "productdirector_api.main.render_glb_job", side_effect=fake_render
            ), patch("productdirector_api.main.subprocess.run", return_value=fake_probe), patch(
                "productdirector_api.main.media_quality_report", return_value=dict(QA_PASS)
            ):
                worker.start()
                self.assertTrue(started.wait(5), "mock 渲染未启动")
                time.sleep(1.4)
                rival = main.claim_job("worker-rival", job_id)
                self.assertTrue(rival.get("claimed"), "无心跳时租约应已过期并可被重领")
                worker.join(timeout=15)
        finally:
            main.FFPROBE = original_ffprobe
            main.LEASE_SECONDS = original_lease_seconds
        if worker is not None:
            self.assertFalse(worker.is_alive(), "worker 线程未结束")
        # 失去租约的旧 worker 不得覆盖由新 worker 持有的任务。
        job = self.client.get(f"/api/v1/jobs/{job_id}").json()
        self.assertEqual(job["status"], "RUNNING")
        self.assertEqual(self._lease_row(job_id)["lease_owner"], "worker-rival")

    def test_a04_killed_worker_is_reconciled_and_finished_by_new_worker(self) -> None:
        job_id = self._create_queued_job("a04-kill-recover")
        first = self.client.post("/internal/v1/workers/claim", json={"worker_id": "worker-a", "job_id": job_id}).json()
        self.assertTrue(first["claimed"])

        # 模拟 worker 进程被杀：没有任何完成/失败回报，租约自然过期。
        self._expire_lease(job_id)
        reconciled = self.client.post("/internal/v1/workers/reconcile")
        self.assertEqual(reconciled.status_code, 200)
        self.assertEqual(reconciled.json()["reconciled"], 1)
        self.assertEqual(self.client.get(f"/api/v1/jobs/{job_id}").json()["status"], "QUEUED")

        fake_render, fake_probe = self._fake_render_tools()
        original_ffprobe = main.FFPROBE
        main.FFPROBE = "ffprobe"
        try:
            with patch("productdirector_api.main.render_glb_job", side_effect=fake_render), patch(
                "productdirector_api.main.subprocess.run", return_value=fake_probe
            ), patch("productdirector_api.main.media_quality_report", return_value=dict(QA_PASS)):
                result = main.run_worker_once("worker-b")
        finally:
            main.FFPROBE = original_ffprobe
        self.assertTrue(result["claimed"])
        self.assertEqual(result["status"], "SUCCEEDED")
        self.assertEqual(self.client.get(f"/api/v1/jobs/{job_id}/video").status_code, 200)
        self.assertEqual(self.client.get(f"/api/v1/jobs/{job_id}/manifest").json()["job_id"], job_id)

        # 被替换的旧 worker 迟到回报必须被拒绝，且不能把已完成任务改回失败。
        stale = self.client.post(
            f"/internal/v1/workers/jobs/{job_id}/fail",
            json={"worker_id": "worker-a", "lease_epoch": first["lease_epoch"], "error": "stale worker"},
        )
        self.assertEqual(stale.status_code, 409)
        final = self.client.get(f"/api/v1/jobs/{job_id}").json()
        self.assertEqual(final["status"], "SUCCEEDED")
        self.assertIsNone(final["error"])


if __name__ == "__main__":
    unittest.main()
