from __future__ import annotations

from pathlib import Path
from typing import Any

from infra.logging.event_logger import EventLogger
from infra.utils.ids import generate_run_id
from infra.utils.io import append_jsonl, ensure_dir


class RunLogger:
    def __init__(self, log_root: str | Path) -> None:
        self.log_root = ensure_dir(log_root)
        self.event_logger = EventLogger(self.log_root)

    def start_run(self, run_type: str, config_hash: str) -> str:
        run_id = generate_run_id(prefix=run_type)
        append_jsonl(
            self.log_root / "pipeline_runs.jsonl",
            {
                "run_id": run_id,
                "run_type": run_type,
                "config_hash": config_hash,
                "status": "STARTED",
            },
        )
        return run_id

    def finish_run(self, run_id: str, status: str, summary: dict[str, Any] | None = None) -> None:
        append_jsonl(
            self.log_root / "pipeline_runs.jsonl",
            {
                "run_id": run_id,
                "status": status,
                "summary": summary or {},
            },
        )

    def log_event(
        self,
        *,
        run_id: str,
        module: str,
        level: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.event_logger.log(
            run_id=run_id,
            module=module,
            level=level,
            message=message,
            payload=payload,
        )

