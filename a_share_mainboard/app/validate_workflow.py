from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from data.storage.repositories import MarketRepository, ResearchRepository
from data.storage.duckdb_client import DuckDBClient
from infra.config.settings import Settings
from infra.logging.run_logger import RunLogger
from infra.utils.io import write_text
from strategy.validation.execution_model import ExecutionModel
from strategy.validation.validation_engine import ValidationEngine
from strategy.validation.validation_reporter import ValidationReporter


@dataclass(slots=True)
class ValidateWorkflow:
    settings: Settings
    run_logger: RunLogger
    db_client: DuckDBClient

    def run(self, start_date: str, end_date: str, horizon: int) -> dict[str, Any]:
        run_id = self.run_logger.start_run(run_type="validate", config_hash="phase1-min-validation")
        market_repo = MarketRepository(self.db_client)
        research_repo = ResearchRepository(self.db_client)

        try:
            self.run_logger.log_event(
                run_id=run_id,
                module="validate_workflow",
                level="INFO",
                message="Validation workflow started",
                payload={"start_date": start_date, "end_date": end_date, "horizon": horizon},
            )

            benchmark_index = (
                self.settings.data.index_codes[0]
                if self.settings.data.index_codes
                else "sh000001"
            )
            validation_result = ValidationEngine(
                market_repo=market_repo,
                repo=research_repo,
                execution_model=ExecutionModel(
                    execution_price_mode=self.settings.validation.execution_price_mode,
                    cost_bps=self.settings.validation.cost_bps,
                ),
                settings=self.settings.validation,
                universe_settings=self.settings.universe,
                strategy_settings=self.settings.strategy,
                policy_settings=self.settings.policy,
                benchmark_index=benchmark_index,
            ).run(
                start_date=start_date,
                end_date=end_date,
                horizons=[horizon],
                run_id=run_id,
            )

            result = {
                "status": validation_result["status"],
                "run_id": run_id,
                "start_date": validation_result["start_date"],
                "end_date": validation_result["end_date"],
                "horizon": horizon,
                "summary": validation_result["summaries"].get(horizon, {}),
                "policy_review": validation_result["policy_reviews"].get(horizon, {}),
                "universe_review": validation_result.get("universe_review", {}),
                "message": "Validation run completed.",
            }
            report_path = (
                self.db_client.db_path.parents[1]
                / "reports"
                / f"validation_{horizon}d_{validation_result['end_date']}.md"
            )
            report_markdown = ValidationReporter().build_report(result=result, horizon=horizon)
            write_text(report_path, report_markdown)
            result["report_path"] = str(report_path)
            self.run_logger.log_event(
                run_id=run_id,
                module="validate_workflow",
                level="INFO",
                message="Validation workflow completed",
                payload=result,
            )
            self.run_logger.finish_run(run_id=run_id, status=result["status"], summary=result)
            return result
        except Exception as exc:  # pragma: no cover - defensive logging path
            self.run_logger.log_event(
                run_id=run_id,
                module="validate_workflow",
                level="ERROR",
                message="Validation workflow failed",
                payload={"error": str(exc), "start_date": start_date, "end_date": end_date},
            )
            self.run_logger.finish_run(
                run_id=run_id,
                status="FAILED",
                summary={"start_date": start_date, "end_date": end_date, "error": str(exc)},
            )
            raise
