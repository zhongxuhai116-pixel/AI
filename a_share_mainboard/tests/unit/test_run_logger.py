from __future__ import annotations

import json

from infra.logging.run_logger import RunLogger


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_run_logger_writes_run_meta_and_events(tmp_path):
    emitted = []
    logger = RunLogger(tmp_path, event_sink=emitted.append, retention_days=30)

    run_id = logger.start_run(run_type="daily", config_hash="cfg-hash")
    logger.log_event(
        run_id=run_id,
        module="daily_workflow",
        level="INFO",
        message="workflow started",
        payload={"trade_date": "2026-03-29"},
    )
    logger.finish_run(run_id=run_id, status="SUCCESS", summary={"count": 5})

    run_meta_path = tmp_path / "runs" / f"{run_id}.json"
    run_event_path = tmp_path / "runs" / f"{run_id}.events.jsonl"
    pipeline_runs_path = tmp_path / "pipeline_runs.jsonl"
    pipeline_events_path = tmp_path / "pipeline_events.jsonl"

    assert run_meta_path.exists()
    assert run_event_path.exists()
    assert pipeline_runs_path.exists()
    assert pipeline_events_path.exists()

    run_meta = json.loads(run_meta_path.read_text(encoding="utf-8"))
    assert run_meta["run_id"] == run_id
    assert run_meta["status"] == "SUCCESS"
    assert run_meta["duration_seconds"] is not None
    assert run_meta["summary"]["count"] == 5

    pipeline_runs = _read_jsonl(pipeline_runs_path)
    assert pipeline_runs[0]["event"] == "RUN_STARTED"
    assert pipeline_runs[1]["event"] == "RUN_FINISHED"

    pipeline_events = _read_jsonl(pipeline_events_path)
    assert pipeline_events[0]["module"] == "daily_workflow"
    assert pipeline_events[0]["message"] == "workflow started"

    assert len(emitted) >= 3
