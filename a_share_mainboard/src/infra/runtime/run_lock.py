from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from infra.exceptions import RuntimeBusyError
from infra.utils.io import ensure_dir
from infra.utils.ids import generate_call_id


class RunLock:
    def __init__(self, lock_path: str | Path, stale_seconds: int = 6 * 60 * 60) -> None:
        self.lock_path = Path(lock_path)
        self.stale_seconds = max(int(stale_seconds), 60)
        ensure_dir(self.lock_path.parent)

    @contextmanager
    def acquire(
        self,
        *,
        command: str,
        parameters: dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]:
        metadata = {
            "lock_id": generate_call_id(prefix="lock"),
            "pid": os.getpid(),
            "command": command,
            "parameters": parameters or {},
            "acquired_at_utc": _utc_now_iso(),
            "lock_path": str(self.lock_path),
        }
        self._acquire(metadata)
        try:
            yield metadata
        finally:
            self._release(metadata["lock_id"])

    def _acquire(self, metadata: dict[str, Any]) -> None:
        while True:
            try:
                fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(metadata, handle, ensure_ascii=False, indent=2)
                return
            except FileExistsError:
                existing = self._read_existing_lock()
                if self._is_stale(existing) or self._is_orphaned(existing):
                    try:
                        self.lock_path.unlink()
                    except FileNotFoundError:
                        pass
                    continue

                holder = existing.get("command", "unknown")
                holder_pid = existing.get("pid", "unknown")
                holder_time = existing.get("acquired_at_utc", "unknown")
                raise RuntimeBusyError(
                    "Another workflow is running "
                    f"(command={holder}, pid={holder_pid}, started={holder_time})."
                )

    def _release(self, lock_id: str) -> None:
        if not self.lock_path.exists():
            return
        try:
            current = self._read_existing_lock()
        except Exception:
            current = {}
        if current.get("lock_id") != lock_id:
            return
        try:
            self.lock_path.unlink()
        except FileNotFoundError:
            return

    def _read_existing_lock(self) -> dict[str, Any]:
        if not self.lock_path.exists():
            return {}
        try:
            return json.loads(self.lock_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _is_stale(self, payload: dict[str, Any]) -> bool:
        raw_value = payload.get("acquired_at_utc")
        if not raw_value or not isinstance(raw_value, str):
            return True
        try:
            acquired_at = _parse_iso_datetime(raw_value)
        except ValueError:
            return True
        age_seconds = (datetime.now(timezone.utc) - acquired_at).total_seconds()
        return age_seconds > self.stale_seconds

    @staticmethod
    def _is_orphaned(payload: dict[str, Any]) -> bool:
        raw_pid = payload.get("pid")
        if raw_pid is None:
            return True
        try:
            pid = int(raw_pid)
        except (TypeError, ValueError):
            return True
        if pid <= 0:
            return True
        if pid == os.getpid():
            return False
        try:
            os.kill(pid, 0)
            return False
        except OSError:
            return True


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso_datetime(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
