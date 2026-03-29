from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from infra.utils.io import append_jsonl, ensure_dir


class EventLogger:
    def __init__(self, log_root: str | Path) -> None:
        self.log_root = ensure_dir(log_root)
        self.run_log_root = ensure_dir(self.log_root / "runs")

    def log(
        self,
        *,
        run_id: str,
        module: str,
        level: str,
        message: str,
        timestamp_utc: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        event = {
            "timestamp_utc": timestamp_utc or datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "module": module,
            "level": level,
            "message": message,
            "payload": payload or {},
        }
        append_jsonl(self.log_root / "pipeline_events.jsonl", event)
        append_jsonl(self.run_log_root / f"{run_id}.events.jsonl", event)
