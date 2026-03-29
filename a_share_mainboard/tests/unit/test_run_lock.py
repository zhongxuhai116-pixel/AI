from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from infra.exceptions import RuntimeBusyError
from infra.runtime import RunLock


def test_run_lock_blocks_concurrent_access(tmp_path):
    lock_path = tmp_path / "runtime.lock"
    lock = RunLock(lock_path=lock_path, stale_seconds=3600)

    with lock.acquire(command="daily", parameters={"trade_date": "2026-03-29"}):
        with pytest.raises(RuntimeBusyError):
            with lock.acquire(command="validate"):
                pass

    assert not lock_path.exists()


def test_run_lock_reclaims_stale_file(tmp_path):
    lock_path = tmp_path / "runtime.lock"
    stale_payload = {
        "lock_id": "lock_stale",
        "pid": 1,
        "command": "daily",
        "parameters": {},
        "acquired_at_utc": (
            datetime.now(timezone.utc) - timedelta(hours=8)
        ).isoformat(),
        "lock_path": str(lock_path),
    }
    lock_path.write_text(json.dumps(stale_payload), encoding="utf-8")

    lock = RunLock(lock_path=lock_path, stale_seconds=60)
    with lock.acquire(command="validate", parameters={"horizon": 10}):
        assert lock_path.exists()

    assert not lock_path.exists()
