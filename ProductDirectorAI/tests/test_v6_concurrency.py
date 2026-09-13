"""V6 并发写入：job_events 序号分配在并发下不得丢事件或撞唯一键。"""
from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tests"))
sys.path.insert(0, str(PROJECT / "apps" / "api"))

import test_job_control as fixtures  # noqa: E402

main = fixtures.main


class JobEventConcurrencyTests(unittest.TestCase):
    _create_asset = fixtures.JobControlAcceptanceTests._create_asset
    _create_plan = fixtures.JobControlAcceptanceTests._create_plan

    def setUp(self) -> None:
        fixtures.JobControlAcceptanceTests.setUp(self)
        self.asset = self._create_asset()
        self.plan = self._create_plan()

    tearDown = fixtures.JobControlAcceptanceTests.tearDown

    def test_concurrent_event_appends_keep_sequences_unique(self) -> None:
        """多个写入者同时追加事件：序号唯一且连续，不因唯一键冲突丢事件。"""
        with main.connect() as db:
            job_id = str(__import__("uuid").uuid4())
            now = main.utc_now()
            db.execute(
                "INSERT INTO jobs VALUES (?, ?, ?, ?, 'QUEUED', 'PREPARE', 0, NULL, NULL, NULL, 0, ?, ?)",
                (job_id, self.plan["id"], self.asset["id"], self.asset["kind"], now, now),
            )
        errors: list[str] = []

        def writer(index: int) -> None:
            for round_index in range(8):
                try:
                    with main.connect() as db:
                        main.append_job_event(db, job_id, "worker.progress", {"writer": index, "round": round_index})
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{type(exc).__name__}: {exc}")

        threads = [threading.Thread(target=writer, args=(index,)) for index in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [], errors)
        with main.connect() as db:
            rows = db.execute("SELECT sequence FROM job_events WHERE job_id = ? ORDER BY sequence", (job_id,)).fetchall()
        sequences = [row["sequence"] for row in rows]
        self.assertEqual(len(sequences), 32)
        self.assertEqual(sequences, list(range(1, 33)))

    def test_append_event_retries_on_duplicate_sequence(self) -> None:
        """模拟唯一键冲突：第一次插入抛冲突后应重算序号并成功写入（不丢事件）。"""
        with main.connect() as db:
            job_id = str(__import__("uuid").uuid4())
            now = main.utc_now()
            db.execute(
                "INSERT INTO jobs VALUES (?, ?, ?, ?, 'QUEUED', 'PREPARE', 0, NULL, NULL, NULL, 0, ?, ?)",
                (job_id, self.plan["id"], self.asset["id"], self.asset["kind"], now, now),
            )
            db.execute(
                "INSERT INTO job_events (id, job_id, sequence, event_type, status, stage, progress, payload, created_at) "
                "VALUES (?, ?, 1, 'seed', 'QUEUED', 'PREPARE', 0, '{}', ?)",
                (str(__import__("uuid").uuid4()), job_id, now),
            )

            class FlakyConnection:
                """第一次写 job_events 抛唯一键冲突，之后透传真实连接。"""

                def __init__(self, real):
                    self.real = real
                    self.failed = False

                def execute(self, sql, params=()):
                    if "INSERT INTO job_events" in sql and not self.failed and "seed" not in str(params):
                        self.failed = True
                        raise Exception(
                            'duplicate key value violates unique constraint "job_events_job_id_sequence_key"'
                        )
                    return self.real.execute(sql, params)

            flaky = FlakyConnection(db)
            main.append_job_event(flaky, job_id, "retry.proof", {"ok": True})
            self.assertTrue(flaky.failed, "测试未真正触发冲突路径")
            rows = db.execute("SELECT sequence, event_type FROM job_events WHERE job_id = ? ORDER BY sequence",
                              (job_id,)).fetchall()
        self.assertEqual([row["sequence"] for row in rows], [1, 2])
        self.assertEqual(rows[1]["event_type"], "retry.proof")


if __name__ == "__main__":
    unittest.main()