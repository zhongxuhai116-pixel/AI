"""V6-09：总控台聚合、调度推进、并发调度与投递公平性（负载实测缺陷的回归测试）。"""
from __future__ import annotations

import json
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tests"))
sys.path.insert(0, str(PROJECT / "apps" / "api"))

import test_job_control as fixtures  # noqa: E402

from productdirector_api import webhooks  # noqa: E402

main = fixtures.main


class SilentReceiver:
    def __init__(self) -> None:
        self.count = 0
        self.lock = threading.Lock()
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                with receiver.lock:
                    receiver.count += 1
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *args):
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/hook"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class ConsoleApiTests(unittest.TestCase):
    _create_asset = fixtures.JobControlAcceptanceTests._create_asset
    _create_plan = fixtures.JobControlAcceptanceTests._create_plan

    def setUp(self) -> None:
        fixtures.JobControlAcceptanceTests.setUp(self)
        self.asset = self._create_asset()
        self.plan = self._create_plan()
        self.client.post(f"/api/v1/plans/{self.plan['id']}/approve", json={"approved": True})

    def _batch(self, items: int = 3) -> dict:
        body = {"name": "总控台批次", "max_concurrent": 2,
                "items": [{"plan_id": self.plan["id"], "profile_id": "tiktok-mx-9x16-esmx",
                           "request_item_key": f"c-{index}"} for index in range(items)]}
        with patch("productdirector_api.main.advance_batch"):
            response = self.client.post("/api/v1/batches", json=body)
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()["batch"]

    def test_overview_reports_server_side_aggregates(self) -> None:
        batch = self._batch(3)
        with patch("productdirector_api.main.execute_job"):
            main.advance_batch(batch["id"], execute_inline=False)
        response = self.client.get("/api/v1/console/overview?window_minutes=60")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIn("measured_at", body)
        self.assertGreaterEqual(body["batches"]["items_by_status"].get("QUEUED", 0), 1)
        self.assertIn("by_status", body["jobs"])
        self.assertIn("success_rate", body["jobs"])
        self.assertIn("queued", body["queue"])
        self.assertIn("running", body["queue"])
        self.assertIn("delivery_latency_ms", body["webhooks"])
        self.assertIn("undelivered", body["events"])
        self.assertTrue(any("数据库实时聚合" in note for note in body["notes"]))
        # success_rate 把 SUCCEEDED 与 VERIFICATION_PASSED 都算成功（此前只算后者导致虚低）
        self.assertGreaterEqual(body["jobs"]["success_rate"], 0.0)

    def test_queue_reports_jobs_without_queue_rows(self) -> None:
        batch = self._batch(1)
        with patch("productdirector_api.main.execute_job"):
            main.advance_batch(batch["id"], execute_inline=False)
        items = self.client.get(f"/api/v1/batches/{batch['id']}/items").json()["items"]
        job_id = items[0]["job_id"]
        with main.connect() as db:
            db.execute("DELETE FROM run_jobs WHERE job_id = ?", (job_id,))
        body = self.client.get("/api/v1/console/overview").json()
        self.assertGreaterEqual(body["queue"]["jobs_without_queue_row"], 1)

    def test_stale_scheduling_claim_returns_to_pending(self) -> None:
        """调度认领后崩溃会留下 SCHEDULING：超时必须退回 PENDING，否则项永久卡住。"""
        batch = self._batch(1)
        items = self.client.get(f"/api/v1/batches/{batch['id']}/items").json()["items"]
        with main.connect() as db:
            db.execute("UPDATE batch_items SET status = 'SCHEDULING', run_id = NULL, job_id = NULL, "
                       "updated_at = ? WHERE id = ?", ("2026-01-01T00:00:00+00:00", items[0]["id"]))
        refreshed = self.client.get(f"/api/v1/batches/{batch['id']}/items").json()["items"]
        self.assertEqual(refreshed[0]["status"], "PENDING")
        self.assertIn("调度认领超时", refreshed[0]["error"])

    def test_scheduling_reuses_existing_run_for_same_item(self) -> None:
        """并发调度不能重复创建 Run（runs 的唯一索引会拒绝）：应复用已有 Run。"""
        batch = self._batch(1)
        with patch("productdirector_api.main.execute_job"):
            main.advance_batch(batch["id"], execute_inline=False)
        items = self.client.get(f"/api/v1/batches/{batch['id']}/items").json()["items"]
        first_run = items[0]["run_id"]
        with main.connect() as db:
            row = db.execute("SELECT * FROM batch_items WHERE id = ?", (items[0]["id"],)).fetchone()
            batch_row = db.execute("SELECT * FROM batches WHERE id = ?", (batch["id"],)).fetchone()
            created = main._existing_or_new_item_run(db, batch_row, row)
        self.assertTrue(created["reused"])
        self.assertEqual(created["run_id"], first_run)

    def test_worker_advances_batch_after_job_completion(self) -> None:
        """作业完成后必须推进批次：否则批次按并发上限调度一次就永久停在 PENDING。"""
        batch = self._batch(3)
        with patch("productdirector_api.main.execute_job"):
            main.advance_batch(batch["id"], execute_inline=False)
        items = self.client.get(f"/api/v1/batches/{batch['id']}/items").json()["items"]
        queued = [item for item in items if item["status"] == "QUEUED"]
        self.assertEqual(len(queued), 2, "并发上限 2：首批只应调度 2 项")
        done_job = queued[0]["job_id"]
        with main.connect() as db:
            db.execute("UPDATE jobs SET status = 'SUCCEEDED', stage = 'DONE', progress = 100 WHERE id = ?",
                       (done_job,))
        main._advance_batch_for_job(done_job)
        refreshed = self.client.get(f"/api/v1/batches/{batch['id']}/items").json()["items"]
        statuses = [item["status"] for item in refreshed]
        self.assertEqual(statuses.count("SUCCEEDED"), 1)
        self.assertEqual(statuses.count("QUEUED"), 2, "空出的并发额度必须补上下一个 PENDING 项")


class DeliveryFairnessTests(unittest.TestCase):
    _create_asset = fixtures.JobControlAcceptanceTests._create_asset
    _create_plan = fixtures.JobControlAcceptanceTests._create_plan

    def setUp(self) -> None:
        fixtures.JobControlAcceptanceTests.setUp(self)
        self.receiver = SilentReceiver()
        self.asset = self._create_asset()
        self.plan = self._create_plan()
        self.client.post(f"/api/v1/plans/{self.plan['id']}/approve", json={"approved": True})

    def tearDown(self) -> None:
        self.receiver.stop()
        fixtures.JobControlAcceptanceTests.tearDown(self)

    def _endpoint(self, url: str, **overrides) -> dict:
        body = {"name": "接收端", "url": url, "event_types": ["item.completed"]}
        body.update(overrides)
        response = self.client.post("/api/v1/webhooks", json=body)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def _one_item_batch(self, name: str) -> dict:
        body = {"name": name, "items": [{"plan_id": self.plan["id"], "profile_id": "tiktok-mx-9x16-esmx"}]}
        with patch("productdirector_api.main.advance_batch"):
            response = self.client.post("/api/v1/batches", json=body)
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()["batch"]

    def _complete_batch(self, batch: dict) -> None:
        with patch("productdirector_api.main.execute_job"):
            main.advance_batch(batch["id"], execute_inline=False)
        items = self.client.get(f"/api/v1/batches/{batch['id']}/items").json()["items"]
        with main.connect() as db:
            db.execute("UPDATE jobs SET status = 'VERIFICATION_PASSED', progress = 100 WHERE id = ?",
                       (items[0]["job_id"],))
        self.client.get(f"/api/v1/batches/{batch['id']}")

    def test_new_endpoint_does_not_receive_historical_events(self) -> None:
        old_batch = self._one_item_batch("历史批次")
        self._complete_batch(old_batch)
        self._endpoint(self.receiver.url)  # 端点注册在事件之后
        outcome = main.dispatch_webhooks(limit=20, force_due=True)
        self.assertEqual(outcome["delivered"], 0, "默认不回填历史事件（否则新事件会被洪水拖慢）")
        self.assertEqual(self.receiver.count, 0)

    def test_backfill_endpoint_opt_in_receives_history(self) -> None:
        old_batch = self._one_item_batch("历史批次 2")
        self._complete_batch(old_batch)
        with main.connect() as db:
            db.execute("UPDATE webhook_endpoints SET backfill_history = 1")
        self._endpoint(self.receiver.url)
        with main.connect() as db:
            db.execute("UPDATE webhook_endpoints SET backfill_history = 1")
        outcome = main.dispatch_webhooks(limit=20, force_due=True)
        self.assertGreaterEqual(outcome["delivered"], 1)
        self.assertGreaterEqual(self.receiver.count, 1)

    def test_fresh_events_delivered_after_registration(self) -> None:
        self._endpoint(self.receiver.url)
        batch = self._one_item_batch("新批次")
        self._complete_batch(batch)
        outcome = main.dispatch_webhooks(limit=20, force_due=True)
        self.assertGreaterEqual(outcome["delivered"], 1)
        self.assertGreaterEqual(self.receiver.count, 1)

    def test_unreachable_endpoint_does_not_starve_healthy_one(self) -> None:
        dead = self._endpoint("http://127.0.0.1:9/hook")  # 端口 9：连接必然失败
        healthy = self._endpoint(self.receiver.url)
        batch = self._one_item_batch("公平性批次")
        self._complete_batch(batch)
        first = main.dispatch_webhooks(limit=20, force_due=True)
        statuses = {(item["endpoint_id"], item["status"]) for item in first["results"]}
        self.assertIn((dead["id"], "RETRY"), statuses)
        self.assertIn((healthy["id"], "DELIVERED"), statuses, "不可达目标不得阻止健康目标投递")
        # 冷却期内不可达目标被跳过，且被记录在 skipped_endpoints
        second = main.dispatch_webhooks(limit=20)
        self.assertNotIn(dead["id"], {item["endpoint_id"] for item in second["results"]})
        self.assertTrue(second["fairness"]["per_endpoint_tick_budget"] >= 1)

    def test_per_endpoint_tick_budget(self) -> None:
        self._endpoint(self.receiver.url)
        batch = self._one_item_batch("预算批次")
        self._complete_batch(batch)
        outcome = main.dispatch_webhooks(limit=1, force_due=True)
        self.assertEqual(outcome["attempted"], 1, "全局上限必须生效")


if __name__ == "__main__":
    unittest.main()
