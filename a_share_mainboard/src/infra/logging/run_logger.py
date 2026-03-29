from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from infra.logging.event_logger import EventLogger
from infra.utils.ids import generate_run_id
from infra.utils.io import append_jsonl, ensure_dir, write_json

EventSink = Callable[[dict[str, Any]], None]


class RunLogger:
    def __init__(
        self,
        log_root: str | Path,
        *,
        event_sink: EventSink | None = None,
        retention_days: int = 30,
    ) -> None:
        self.log_root = ensure_dir(log_root)
        self.runs_root = ensure_dir(self.log_root / "runs")
        self.event_logger = EventLogger(self.log_root)
        self.event_sink = event_sink
        self.retention_days = max(int(retention_days), 1)
        self._active_run_meta: dict[str, dict[str, Any]] = {}
        self._cleanup_run_artifacts()

    def start_run(self, run_type: str, config_hash: str) -> str:
        run_id = generate_run_id(prefix=run_type)
        started_at = _utc_now_iso()
        run_meta = {
            "run_id": run_id,
            "run_type": run_type,
            "config_hash": config_hash,
            "status": "STARTED",
            "started_at_utc": started_at,
            "finished_at_utc": None,
            "duration_seconds": None,
            "summary": {},
        }
        self._active_run_meta[run_id] = run_meta
        write_json(self.runs_root / f"{run_id}.json", run_meta)
        append_jsonl(
            self.log_root / "pipeline_runs.jsonl",
            {
                "event": "RUN_STARTED",
                "timestamp_utc": started_at,
                "run_id": run_id,
                "run_type": run_type,
                "config_hash": config_hash,
                "status": "STARTED",
            },
        )
        self._emit(
            {
                "type": "run",
                "event": "RUN_STARTED",
                "timestamp_utc": started_at,
                "run_id": run_id,
                "run_type": run_type,
                "status": "STARTED",
            }
        )
        return run_id

    def finish_run(self, run_id: str, status: str, summary: dict[str, Any] | None = None) -> None:
        finished_at = _utc_now_iso()
        run_meta = self._active_run_meta.get(run_id) or self._load_run_meta(run_id)
        started_at = run_meta.get("started_at_utc")
        duration = _duration_seconds(started_at, finished_at)
        run_meta.update(
            {
                "status": status,
                "finished_at_utc": finished_at,
                "duration_seconds": duration,
                "summary": summary or {},
            }
        )
        write_json(self.runs_root / f"{run_id}.json", run_meta)
        append_jsonl(
            self.log_root / "pipeline_runs.jsonl",
            {
                "event": "RUN_FINISHED",
                "timestamp_utc": finished_at,
                "run_id": run_id,
                "status": status,
                "duration_seconds": duration,
                "summary": summary or {},
            },
        )
        self._active_run_meta.pop(run_id, None)
        self._emit(
            {
                "type": "run",
                "event": "RUN_FINISHED",
                "timestamp_utc": finished_at,
                "run_id": run_id,
                "status": status,
                "duration_seconds": duration,
            }
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
        event = {
            "type": "event",
            "timestamp_utc": _utc_now_iso(),
            "run_id": run_id,
            "module": module,
            "level": level,
            "message": message,
            "payload": payload or {},
        }
        self.event_logger.log(
            run_id=run_id,
            module=module,
            level=level,
            message=message,
            timestamp_utc=event["timestamp_utc"],
            payload=payload,
        )
        self._emit(event)

    def _load_run_meta(self, run_id: str) -> dict[str, Any]:
        run_meta_path = self.runs_root / f"{run_id}.json"
        if run_meta_path.exists():
            try:
                import json

                return json.loads(run_meta_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {
            "run_id": run_id,
            "run_type": "unknown",
            "config_hash": "unknown",
            "status": "UNKNOWN",
            "started_at_utc": None,
            "finished_at_utc": None,
            "duration_seconds": None,
            "summary": {},
        }

    def _cleanup_run_artifacts(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        for run_file in self.runs_root.glob("*.json"):
            try:
                modified_at = datetime.fromtimestamp(
                    run_file.stat().st_mtime,
                    tz=timezone.utc,
                )
            except OSError:
                continue
            if modified_at >= cutoff:
                continue
            try:
                run_file.unlink()
            except FileNotFoundError:
                pass
            events_file = self.runs_root / f"{run_file.stem}.events.jsonl"
            try:
                events_file.unlink()
            except FileNotFoundError:
                pass

    def _emit(self, payload: dict[str, Any]) -> None:
        if self.event_sink is None:
            return
        try:
            self.event_sink(payload)
        except Exception:
            return


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _duration_seconds(started_at: str | None, finished_at: str) -> float | None:
    if not started_at:
        return None
    try:
        start = _parse_iso_datetime(started_at)
        end = _parse_iso_datetime(finished_at)
    except ValueError:
        return None
    return max((end - start).total_seconds(), 0.0)


def _parse_iso_datetime(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
