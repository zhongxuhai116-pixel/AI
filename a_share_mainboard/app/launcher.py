from __future__ import annotations

import argparse
from pathlib import Path

from app._bootstrap import bootstrap_paths

PROJECT_ROOT = bootstrap_paths()

from app.daily_workflow import DailyWorkflow
from app.rebuild_workflow import RebuildWorkflow
from app.validate_workflow import ValidateWorkflow
from data.storage.duckdb_client import DuckDBClient
from data.storage.table_bootstrap import bootstrap_core_tables
from infra.config.loader import load_settings
from infra.logging.run_logger import RunLogger


def _build_runtime(project_root: Path) -> tuple[DuckDBClient, RunLogger, object]:
    settings = load_settings(project_root / "config")
    db_client = DuckDBClient(project_root / settings.data.duckdb_path)
    bootstrap_core_tables(db_client)
    run_logger = RunLogger(project_root / settings.app.log_root)
    return db_client, run_logger, settings


def run_daily_cli(trade_date: str) -> dict:
    db_client, run_logger, settings = _build_runtime(PROJECT_ROOT)
    try:
        workflow = DailyWorkflow(settings=settings, run_logger=run_logger, db_client=db_client)
        return workflow.run(trade_date=trade_date)
    finally:
        db_client.close()


def run_validate_cli(start_date: str, end_date: str, horizon: int) -> dict:
    db_client, run_logger, settings = _build_runtime(PROJECT_ROOT)
    try:
        workflow = ValidateWorkflow(settings=settings, run_logger=run_logger, db_client=db_client)
        return workflow.run(start_date=start_date, end_date=end_date, horizon=horizon)
    finally:
        db_client.close()


def run_rebuild_cli(start_date: str, end_date: str) -> dict:
    db_client, run_logger, settings = _build_runtime(PROJECT_ROOT)
    try:
        workflow = RebuildWorkflow(settings=settings, run_logger=run_logger, db_client=db_client)
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
    else:
        result = run_rebuild_cli(start_date=args.start_date, end_date=args.end_date)

    print(result)


if __name__ == "__main__":
    main()
