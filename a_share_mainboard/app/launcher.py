from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from app._bootstrap import bootstrap_paths

PROJECT_ROOT = bootstrap_paths()

from app.daily_workflow import DailyWorkflow
from app.rebuild_workflow import RebuildWorkflow
from app.validate_rolling_workflow import RollingValidateWorkflow
from app.validate_workflow import ValidateWorkflow
from data.storage.duckdb_client import DuckDBClient
from data.storage.table_bootstrap import bootstrap_core_tables
from infra.config.loader import load_settings
from infra.logging.run_logger import RunLogger
from infra.runtime import RunLock


def _build_runtime(
    project_root: Path,
    *,
    event_sink=None,
) -> tuple[DuckDBClient, RunLogger, object, RunLock]:
    settings = load_settings(project_root / "config")
    db_client = DuckDBClient(project_root / settings.data.duckdb_path)
    bootstrap_core_tables(db_client)
    run_logger = RunLogger(
        project_root / settings.app.log_root,
        event_sink=event_sink,
        retention_days=settings.app.log_retention_days,
    )
    run_lock = RunLock(
        project_root / settings.app.run_lock_path,
        stale_seconds=settings.app.run_lock_stale_seconds,
    )
    return db_client, run_logger, settings, run_lock


def run_daily_cli(trade_date: str, *, event_sink=None) -> dict[str, Any]:
    db_client, run_logger, settings, run_lock = _build_runtime(
        PROJECT_ROOT,
        event_sink=event_sink,
    )
    try:
        with run_lock.acquire(command="daily", parameters={"trade_date": trade_date}):
            workflow = DailyWorkflow(
                settings=settings,
                run_logger=run_logger,
                db_client=db_client,
            )
            return workflow.run(trade_date=trade_date)
    finally:
        db_client.close()


def run_validate_cli(
    start_date: str,
    end_date: str,
    horizon: int,
    *,
    event_sink=None,
) -> dict[str, Any]:
    db_client, run_logger, settings, run_lock = _build_runtime(
        PROJECT_ROOT,
        event_sink=event_sink,
    )
    try:
        with run_lock.acquire(
            command="validate",
            parameters={
                "start_date": start_date,
                "end_date": end_date,
                "horizon": horizon,
            },
        ):
            workflow = ValidateWorkflow(
                settings=settings,
                run_logger=run_logger,
                db_client=db_client,
            )
            return workflow.run(start_date=start_date, end_date=end_date, horizon=horizon)
    finally:
        db_client.close()


def run_validate_rolling_cli(
    *,
    start_date: str,
    end_date: str,
    horizon: int,
    window_size: int,
    step_size: int,
    event_sink=None,
) -> dict[str, Any]:
    db_client, run_logger, settings, run_lock = _build_runtime(
        PROJECT_ROOT,
        event_sink=event_sink,
    )
    try:
        with run_lock.acquire(
            command="validate_rolling",
            parameters={
                "start_date": start_date,
                "end_date": end_date,
                "horizon": horizon,
                "window_size": window_size,
                "step_size": step_size,
            },
        ):
            workflow = RollingValidateWorkflow(
                settings=settings,
                run_logger=run_logger,
                db_client=db_client,
            )
            return workflow.run(
                start_date=start_date,
                end_date=end_date,
                horizon=horizon,
                window_size=window_size,
                step_size=step_size,
            )
    finally:
        db_client.close()


def run_rebuild_cli(start_date: str, end_date: str, *, event_sink=None) -> dict[str, Any]:
    db_client, run_logger, settings, run_lock = _build_runtime(
        PROJECT_ROOT,
        event_sink=event_sink,
    )
    try:
        with run_lock.acquire(
            command="rebuild",
            parameters={"start_date": start_date, "end_date": end_date},
        ):
            workflow = RebuildWorkflow(
                settings=settings,
                run_logger=run_logger,
                db_client=db_client,
            )
            return workflow.run(start_date=start_date, end_date=end_date)
    finally:
        db_client.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="A-share mainboard research launcher")
    subparsers = parser.add_subparsers(dest="command", required=True)

    daily_parser = subparsers.add_parser("daily", help="Run daily workflow")
    daily_parser.add_argument("--trade-date", required=True)

    validate_parser = subparsers.add_parser("validate", help="Run validation workflow")
    validate_parser.add_argument("--start-date", required=True)
    validate_parser.add_argument("--end-date", required=True)
    validate_parser.add_argument("--horizon", type=int, default=5)

    rolling_parser = subparsers.add_parser(
        "validate-rolling",
        help="Run rolling validation workflow",
    )
    rolling_parser.add_argument("--start-date", required=True)
    rolling_parser.add_argument("--end-date", required=True)
    rolling_parser.add_argument("--horizon", type=int, default=5)
    rolling_parser.add_argument("--window-size", type=int, default=20)
    rolling_parser.add_argument("--step-size", type=int, default=5)

    rebuild_parser = subparsers.add_parser("rebuild", help="Run rebuild workflow")
    rebuild_parser.add_argument("--start-date", required=True)
    rebuild_parser.add_argument("--end-date", required=True)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "daily":
        result = run_daily_cli(trade_date=args.trade_date)
    elif args.command == "validate":
        result = run_validate_cli(
            start_date=args.start_date,
            end_date=args.end_date,
            horizon=args.horizon,
        )
    elif args.command == "validate-rolling":
        result = run_validate_rolling_cli(
            start_date=args.start_date,
            end_date=args.end_date,
            horizon=args.horizon,
            window_size=args.window_size,
            step_size=args.step_size,
        )
    else:
        result = run_rebuild_cli(start_date=args.start_date, end_date=args.end_date)

    print(result)


if __name__ == "__main__":
    main()
