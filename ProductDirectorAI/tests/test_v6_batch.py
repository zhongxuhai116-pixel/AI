"""V6-04：批次与变体（矩阵展开、相容性、去重、并发、暂停/取消/重试、状态聚合）。"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tests"))
sys.path.insert(0, str(PROJECT / "apps" / "api"))

import test_job_control as fixtures  # noqa: E402

from productdirector_api import batch as batch_rules  # noqa: E402

main = fixtures.main


class ExpansionUnitTests(unittest.TestCase):
    def _inputs(self):
        products = [{"id": "pv-1", "product_asset_id": "asset-1"}]
        plans = [{"plan_id": "plan-1", "product_asset_id": "asset-1"}]
        profiles = [{"profile_id": f"prof-{index}", "id": f"profv-{index}"} for index in range(1, 4)]
        return products, plans, profiles

    def test_matrix_expansion_count_is_server_side(self) -> None:
        products, plans, profiles = self._inputs()
        result = batch_rules.expand_matrix(
            product_versions=products, plan_versions=plans, profile_versions=profiles,
            variations_per_combination=2,
        )
        self.assertEqual(result["expanded_count"], 6)
        self.assertEqual(len(result["items"]), 6)
        self.assertEqual(result["duplicates"], [])
        variations = [item["variation"] for item in result["items"]]
        self.assertEqual(variations, [1, 2, 1, 2, 1, 2])
        # 缓存键稳定可复现
        again = batch_rules.expand_matrix(
            product_versions=products, plan_versions=plans, profile_versions=profiles,
            variations_per_combination=2,
        )
        self.assertEqual([item["cache_key"] for item in result["items"]],
                         [item["cache_key"] for item in again["items"]])

    def test_incompatible_product_is_reported_with_conflicts(self) -> None:
        products = [{"id": "pv-2", "product_asset_id": "asset-2"}]
        plans = [{"plan_id": "plan-1", "product_asset_id": "asset-1"}]
        profiles = [{"profile_id": "prof-1", "id": "profv-1"}]
        with self.assertRaises(batch_rules.ExpansionError) as ctx:
            batch_rules.expand_matrix(product_versions=products, plan_versions=plans,
                                      profile_versions=profiles, variations_per_combination=1)
        self.assertEqual(ctx.exception.code, "incompatible_combination")
        self.assertTrue(ctx.exception.conflicts)

    def test_over_limit_is_rejected(self) -> None:
        products = [{"id": f"pv-{i}", "product_asset_id": "a"} for i in range(10)]
        plans = [{"plan_id": "plan", "product_asset_id": "a"}]
        profiles = [{"profile_id": "p", "id": "pv"}]
        with self.assertRaises(batch_rules.ExpansionError) as ctx:
            batch_rules.expand_matrix(product_versions=products, plan_versions=plans,
                                      profile_versions=profiles, variations_per_combination=20)
        self.assertEqual(ctx.exception.code, "over_limit")

    def test_duplicate_items_are_flagged_not_merged(self) -> None:
        items = [{"plan_id": "plan", "profile_id": "prof", "seed": 7},
                 {"plan_id": "plan", "profile_id": "prof", "seed": 7}]
        result = batch_rules.expand_items(items)
        self.assertEqual(result["expanded_count"], 2)
        self.assertEqual(len(result["duplicates"]), 1)
        self.assertEqual(result["duplicates"][0]["duplicate_of"], 0)
        # 显式 request_item_key 表示确实要重复生产
        distinct = batch_rules.expand_items([
            {"plan_id": "plan", "profile_id": "prof", "seed": 7, "request_item_key": "a"},
            {"plan_id": "plan", "profile_id": "prof", "seed": 7, "request_item_key": "b"},
        ])
        self.assertEqual(distinct["duplicates"], [])

    def test_item_must_declare_plan_and_profile(self) -> None:
        with self.assertRaises(batch_rules.ExpansionError) as ctx:
            batch_rules.expand_items([{"plan_id": "plan"}])
        self.assertEqual(ctx.exception.code, "item_missing_field")

    def test_status_aggregation_and_summary(self) -> None:
        self.assertEqual(batch_rules.aggregate_status(["SUCCEEDED", "SUCCEEDED"]), "COMPLETED")
        self.assertEqual(batch_rules.aggregate_status(["SUCCEEDED", "FAILED"]), "PARTIAL_FAILED")
        self.assertEqual(batch_rules.aggregate_status(["FAILED", "CANCELLED"]), "FAILED")
        self.assertEqual(batch_rules.aggregate_status(["RUNNING", "PENDING"]), "RUNNING")
        self.assertEqual(batch_rules.aggregate_status(["PENDING", "PENDING"], paused=True), "PAUSED")
        summary = batch_rules.summarize([{"status": "SUCCEEDED"}, {"status": "FAILED"}])
        self.assertEqual(summary["total"], 2)
        self.assertFalse(summary["production_complete"])
        self.assertEqual(summary["succeeded"], 1)

    def test_retry_targets_only_failed_or_cancelled(self) -> None:
        items = [{"id": "a", "index": 0, "status": "SUCCEEDED"},
                 {"id": "b", "index": 1, "status": "FAILED"},
                 {"id": "c", "index": 2, "status": "CANCELLED"},
                 {"id": "d", "index": 3, "status": "PENDING"}]
        targets = batch_rules.select_retry_targets(items)
        self.assertEqual([item["id"] for item in targets], ["b", "c"])
        filtered = batch_rules.select_retry_targets(items, item_filter=["b"])
        self.assertEqual([item["id"] for item in filtered], ["b"])

    def test_publish_intent_defaults_are_conservative(self) -> None:
        intent = batch_rules.normalize_publish_intent(None)
        self.assertFalse(intent["auto_publish"])
        self.assertEqual(intent["requested_visibility"], "private")
        self.assertIn("不自动公开", intent["note"])


class BatchApiTests(unittest.TestCase):
    _create_asset = fixtures.JobControlAcceptanceTests._create_asset
    _create_plan = fixtures.JobControlAcceptanceTests._create_plan

    def setUp(self) -> None:
        fixtures.JobControlAcceptanceTests.setUp(self)
        self.asset = self._create_asset()
        self.plan = self._create_plan()
        approved = self.client.post(f"/api/v1/plans/{self.plan['id']}/approve", json={"approved": True})
        self.version_id = approved.json()["product_version_id"]

    tearDown = fixtures.JobControlAcceptanceTests.tearDown

    def _matrix(self, variations: int = 2) -> dict:
        return {
            "product_version_ids": [self.version_id],
            "plan_ids": [self.plan["id"]],
            "profile_ids": ["tiktok-mx-9x16-esmx", "youtube-shorts-9x16-en", "pinterest-2x3-en"],
            "variations_per_combination": variations,
        }

    def test_preview_expands_1x1x3x2_without_side_effects(self) -> None:
        before = len(self.client.get("/api/v1/batches").json())
        response = self.client.post("/api/v1/batches/preview", json={"matrix": self._matrix()})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["expanded_count"], 6)
        self.assertEqual(len(body["items"]), 6)
        self.assertEqual(body["mode"], "matrix")
        self.assertEqual(body["side_effects"], "none（预览不创建任务）")
        after = len(self.client.get("/api/v1/batches").json())
        self.assertEqual(before, after)

    def test_matrix_and_items_are_exclusive(self) -> None:
        response = self.client.post("/api/v1/batches/preview", json={
            "matrix": self._matrix(), "items": [{"plan_id": self.plan["id"], "profile_id": "p"}],
        })
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["detail"]["code"], "matrix_and_items_exclusive")

    def test_plan_must_be_approved_and_product_must_match(self) -> None:
        draft = self._create_plan()
        response = self.client.post("/api/v1/batches/preview", json={
            "matrix": {"product_version_ids": [self.version_id], "plan_ids": [draft["id"]],
                       "profile_ids": ["tiktok-mx-9x16-esmx"], "variations_per_combination": 1},
        })
        self.assertEqual(response.status_code, 409, response.text)
        other_asset = self._create_asset()
        other_plan = self._create_plan()
        # 另一个资产的产品版本与既有计划不匹配
        approved_other = self.client.post(f"/api/v1/plans/{other_plan['id']}/approve", json={"approved": True})
        mismatched = self.client.post("/api/v1/batches/preview", json={
            "matrix": {"product_version_ids": [self.version_id], "plan_ids": [other_plan["id"]],
                       "profile_ids": ["tiktok-mx-9x16-esmx"], "variations_per_combination": 1},
        })
        self.assertEqual(mismatched.status_code, 422, mismatched.text)
        self.assertEqual(mismatched.json()["detail"]["code"], "incompatible_combination")
        self.assertTrue(approved_other.json()["product_version_id"])

    def test_create_batch_is_idempotent_and_creates_items_and_runs(self) -> None:
        payload = {"name": "1×1×3×2", "matrix": self._matrix(), "idempotency_key": "batch-key-1"}
        with patch("productdirector_api.main.execute_job"):
            created = self.client.post("/api/v1/batches", json=payload)
        self.assertEqual(created.status_code, 202, created.text)
        batch = created.json()["batch"]
        self.assertEqual(batch["summary"]["total"], 6)
        self.assertEqual(batch["payload"]["expanded_count"], 6)
        with patch("productdirector_api.main.execute_job"):
            again = self.client.post("/api/v1/batches", json=payload)
        self.assertTrue(again.json()["reused_idempotent"])
        self.assertEqual(again.json()["batch"]["id"], batch["id"])
        items = self.client.get(f"/api/v1/batches/{batch['id']}/items").json()
        self.assertEqual(len(items["items"]), 6)
        self.assertEqual(items["summary"]["total"], 6)
        # 并发上限 2：前 2 项已调度（各自独立 Run），其余保持 PENDING 等待空位
        scheduled = [item for item in items["items"] if item["run_id"]]
        waiting = [item for item in items["items"] if not item["run_id"]]
        self.assertEqual(len(scheduled), 2)
        self.assertEqual(len(waiting), 4)
        self.assertEqual({item["status"] for item in scheduled}, {"QUEUED"})
        self.assertEqual({item["status"] for item in waiting}, {"PENDING"})
        self.assertEqual(len({item["run_id"] for item in scheduled}), 2)  # 每项独立 Run

    def test_pause_blocks_new_scheduling_and_resume_continues(self) -> None:
        with patch("productdirector_api.main.execute_job"), patch("productdirector_api.main.advance_batch"):
            batch = self.client.post("/api/v1/batches", json={"matrix": self._matrix()}).json()["batch"]
        paused = self.client.post(f"/api/v1/batches/{batch['id']}/pause", json={"reason": "人工暂停"})
        self.assertEqual(paused.status_code, 200, paused.text)
        self.assertEqual(paused.json()["batch"]["status"], "PAUSED")
        self.assertEqual(paused.json()["batch"]["revision"], 2)
        advanced = main.advance_batch(batch["id"])
        self.assertEqual(advanced["started"], [])  # 暂停期间不新开任务
        resumed = self.client.post(f"/api/v1/batches/{batch['id']}/resume", json={})
        self.assertEqual(resumed.status_code, 200, resumed.text)

    def test_cancel_scope_and_retry_failed(self) -> None:
        with patch("productdirector_api.main.execute_job"), patch("productdirector_api.main.advance_batch"):
            batch = self.client.post("/api/v1/batches", json={"matrix": self._matrix()}).json()["batch"]
        items = self.client.get(f"/api/v1/batches/{batch['id']}/items").json()["items"]
        # 第一项标记失败（模拟真实失败终态），第二项成功
        with main.connect() as db:
            db.execute("UPDATE batch_items SET status = 'FAILED', error = '渲染失败' WHERE id = ?", (items[0]["id"],))
            db.execute("UPDATE batch_items SET status = 'SUCCEEDED' WHERE id = ?", (items[1]["id"],))
        retried = self.client.post(f"/api/v1/batches/{batch['id']}/retry-failed", json={"reason": "重试失败项"})
        self.assertEqual(retried.status_code, 200, retried.text)
        body = retried.json()
        self.assertEqual([item["index"] for item in body["retried"]], [items[0]["index"]])
        self.assertIn(items[1]["index"], body["skipped_succeeded"])
        after = self.client.get(f"/api/v1/batches/{batch['id']}/items").json()["items"]
        statuses = {item["index"]: item["status"] for item in after}
        self.assertEqual(statuses[items[1]["index"]], "SUCCEEDED")  # 成功项未被重置
        # 失败项被重置并立即重新调度（PENDING 或已排到 QUEUED 都算已重试），错误信息清空
        self.assertIn(statuses[items[0]["index"]], {"PENDING", "QUEUED", "RUNNING", "SUCCEEDED"})
        self.assertIsNone(next(item for item in after if item["index"] == items[0]["index"])["error"])
        cancelled = self.client.post(f"/api/v1/batches/{batch['id']}/cancel",
                                     json={"scope": "all_unfinished", "reason": "收工"})
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertTrue(cancelled.json()["cancelled"])
        final = self.client.get(f"/api/v1/batches/{batch['id']}").json()
        self.assertEqual(final["status"], "PARTIAL_FAILED")

    def test_partial_failure_keeps_succeeded_items(self) -> None:
        with patch("productdirector_api.main.execute_job"), patch("productdirector_api.main.advance_batch"):
            batch = self.client.post("/api/v1/batches", json={"matrix": self._matrix()}).json()["batch"]
        items = self.client.get(f"/api/v1/batches/{batch['id']}/items").json()["items"]
        with main.connect() as db:
            for item in items[:5]:
                db.execute("UPDATE batch_items SET status = 'SUCCEEDED' WHERE id = ?", (item["id"],))
            db.execute("UPDATE batch_items SET status = 'FAILED', error = 'QA 驳回' WHERE id = ?", (items[5]["id"],))
        batch_state = self.client.get(f"/api/v1/batches/{batch['id']}").json()
        advanced = main.advance_batch(batch["id"])
        state = advanced["batch"]
        self.assertEqual(state["summary"]["succeeded"], 5)
        self.assertEqual(state["summary"]["failed"], 1)
        self.assertFalse(state["summary"]["production_complete"])
        self.assertIn(state["status"], {"PARTIAL_FAILED", "RUNNING"})
        self.assertEqual(batch_state["summary"]["total"], 6)

    def test_scheduled_items_reference_existing_jobs(self) -> None:
        """调度后 runs.job_id 必须指向真实存在的 jobs 行（PostgreSQL 有外键约束），
        且必须写入 run_jobs 队列行——Worker 只从队列领取任务，缺了它任务永远不会被执行。"""
        with patch("productdirector_api.main.execute_job"), patch("productdirector_api.main.advance_batch"):
            batch = self.client.post("/api/v1/batches", json={"matrix": self._matrix()}).json()["batch"]
        with patch("productdirector_api.main.execute_job"):
            main.advance_batch(batch["id"])
        items = self.client.get(f"/api/v1/batches/{batch['id']}/items").json()["items"]
        scheduled = [item for item in items if item["job_id"]]
        self.assertTrue(scheduled)
        with main.connect() as db:
            for item in scheduled:
                job = db.execute("SELECT id FROM jobs WHERE id = ?", (item["job_id"],)).fetchone()
                run = db.execute("SELECT id FROM runs WHERE id = ?", (item["run_id"],)).fetchone()
                queue_row = db.execute("SELECT id FROM run_jobs WHERE job_id = ?", (item["job_id"],)).fetchone()
                self.assertIsNotNone(job, f"job 不存在: {item['job_id']}")
                self.assertIsNotNone(run, f"run 不存在: {item['run_id']}")
                self.assertIsNotNone(queue_row, f"缺少 run_jobs 队列行: {item['job_id']}")
        # 队列必须可被领取，否则任务永远不会被执行
        claim = main.claim_job("test-worker", scheduled[0]["job_id"])
        self.assertTrue(claim.get("claimed"), claim)

    def test_scheduling_failure_is_recorded_visibly(self) -> None:
        """调度失败不能静默：该项记录 FAILED + 错误，其他项继续尝试。"""
        with patch("productdirector_api.main.execute_job"), patch("productdirector_api.main.advance_batch"):
            batch = self.client.post("/api/v1/batches", json={"matrix": self._matrix()}).json()["batch"]
        from fastapi import HTTPException as FastApiHTTPException

        calls = {"count": 0}
        original = main.start_batch_item_run

        def flaky(db, batch_row, item_row):
            calls["count"] += 1
            if calls["count"] == 1:
                raise FastApiHTTPException(409, "计划缺少冻结合同（测试注入）")
            return original(db, batch_row, item_row)

        with patch("productdirector_api.main.start_batch_item_run", side_effect=flaky), \
                patch("productdirector_api.main.execute_job"):
            main.advance_batch(batch["id"])
        items = self.client.get(f"/api/v1/batches/{batch['id']}/items").json()["items"]
        failed = [item for item in items if item["status"] == "FAILED"]
        started = [item for item in items if item["status"] in ("QUEUED", "RUNNING", "SUCCEEDED")]
        self.assertEqual(len(failed), 1)
        self.assertIn("调度失败", failed[0]["error"])
        self.assertTrue(started, "后续项应继续调度，而不是整体卡住")

    def test_items_mode_dedupes_only_when_key_missing(self) -> None:
        payload = {"items": [
            {"plan_id": self.plan["id"], "profile_id": "tiktok-mx-9x16-esmx", "seed": 3},
            {"plan_id": self.plan["id"], "profile_id": "tiktok-mx-9x16-esmx", "seed": 3},
        ]}
        preview = self.client.post("/api/v1/batches/preview", json=payload)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.json()["mode"], "items")
        self.assertEqual(len(preview.json()["duplicates"]), 1)
        with patch("productdirector_api.main.execute_job"), patch("productdirector_api.main.advance_batch"):
            created = self.client.post("/api/v1/batches", json=payload)
        self.assertEqual(created.status_code, 202, created.text)
        self.assertEqual(created.json()["batch"]["summary"]["total"], 2)

    def test_reconcile_requeues_item_with_missing_queue_row(self) -> None:
        """真实缺陷回归：批次项的队列行缺失时，项会停在 QUEUED 永不执行（产线已出现 2 例）。"""
        payload = {"items": [{"plan_id": self.plan["id"], "profile_id": "tiktok-mx-9x16-esmx"}]}
        with patch("productdirector_api.main.execute_job"), patch("productdirector_api.main.advance_batch"):
            created = self.client.post("/api/v1/batches", json=payload)
        batch_id = created.json()["batch"]["id"]
        main.advance_batch(batch_id)
        items = self.client.get(f"/api/v1/batches/{batch_id}/items").json()["items"]
        job_id = items[0]["job_id"]
        self.assertIsNotNone(job_id)
        with main.connect() as db:
            queue_rows = db.execute("SELECT count(*) AS n FROM run_jobs WHERE job_id = ?", (job_id,)).fetchone()["n"]
        self.assertEqual(queue_rows, 1)
        # 模拟旧版本留下的残缺状态：队列行被漏写（jobs/runs 仍在、状态停在 QUEUED）
        with main.connect() as db:
            db.execute("DELETE FROM run_jobs WHERE job_id = ?", (job_id,))
            db.execute("UPDATE jobs SET status = 'QUEUED', stage = 'QUEUED', progress = 0 WHERE id = ?", (job_id,))
            db.execute("UPDATE batch_items SET status = 'QUEUED' WHERE id = ?", (items[0]["id"],))
        # 读取批次触发对账 → 必须补建队列行，否则该批次永远卡住
        self.client.get(f"/api/v1/batches/{batch_id}")
        with main.connect() as db:
            restored = db.execute("SELECT * FROM run_jobs WHERE job_id = ?", (job_id,)).fetchone()
            attempts = db.execute("SELECT count(*) AS n FROM job_attempts WHERE run_job_id = ?",
                                 (restored["id"],)).fetchone()["n"] if restored else 0
        self.assertIsNotNone(restored, "对账必须补建缺失的 run_jobs 队列行")
        self.assertEqual(restored["status"], "QUEUED")
        self.assertEqual(attempts, 1)
        # 幂等：重复对账不会再插入第二行
        self.client.get(f"/api/v1/batches/{batch_id}")
        with main.connect() as db:
            again = db.execute("SELECT count(*) AS n FROM run_jobs WHERE job_id = ?", (job_id,)).fetchone()["n"]
        self.assertEqual(again, 1)

    def test_over_limit_returns_422_with_count(self) -> None:        # 展开上限 100：20 个变体是允许的（在 100 以内）
        allowed = self.client.post("/api/v1/batches/preview", json={
            "matrix": {"product_version_ids": [self.version_id], "plan_ids": [self.plan["id"]],
                       "profile_ids": ["tiktok-mx-9x16-esmx"], "variations_per_combination": 20},
        })
        self.assertEqual(allowed.status_code, 200, allowed.text)
        self.assertEqual(allowed.json()["expanded_count"], 20)
        # items[] 超过 100 行在请求模型层直接 422
        too_many = self.client.post("/api/v1/batches/preview", json={
            "items": [{"plan_id": self.plan["id"], "profile_id": "tiktok-mx-9x16-esmx"} for _ in range(101)],
        })
        self.assertEqual(too_many.status_code, 422, too_many.text)


if __name__ == "__main__":
    unittest.main()
