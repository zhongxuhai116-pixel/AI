from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any

from data.storage.duckdb_client import DuckDBClient
from data.storage.repositories import MarketRepository, ResearchRepository
from infra.config.settings import Settings
from infra.logging.run_logger import RunLogger
from infra.utils.io import write_text
from strategy.validation.execution_model import ExecutionModel
from strategy.validation.rolling_validation_reporter import RollingValidationReporter
from strategy.validation.validation_engine import ValidationEngine


@dataclass(slots=True)
class RollingValidateWorkflow:
    settings: Settings
    run_logger: RunLogger
    db_client: DuckDBClient

    def run(
        self,
        *,
        start_date: str,
        end_date: str,
        horizon: int,
        window_size: int,
        step_size: int,
    ) -> dict[str, Any]:
        run_id = self.run_logger.start_run(
            run_type="validate_rolling",
            config_hash="phase1-rolling-validation",
        )
        market_repo = MarketRepository(self.db_client)
        research_repo = ResearchRepository(self.db_client)

        try:
            self.run_logger.log_event(
                run_id=run_id,
                module="validate_rolling_workflow",
                level="INFO",
                message="Rolling validation workflow started",
                payload={
                    "start_date": start_date,
                    "end_date": end_date,
                    "horizon": horizon,
                    "window_size": window_size,
                    "step_size": step_size,
                },
            )

            trade_dates = market_repo.get_open_trade_dates(
                start_date=start_date,
                end_date=end_date,
            )
            windows = self._build_windows(
                trade_dates=trade_dates,
                window_size=window_size,
                step_size=step_size,
            )
            benchmark_index = (
                self.settings.data.index_codes[0]
                if self.settings.data.index_codes
                else "sh000001"
            )
            engine = ValidationEngine(
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
            )

            window_results: list[dict[str, Any]] = []
            for index, window in enumerate(windows, start=1):
                window_run_id = f"{run_id}_w{index:02d}"
                validation_result = engine.run(
                    start_date=window["start_date"],
                    end_date=window["end_date"],
                    horizons=[horizon],
                    run_id=window_run_id,
                )
                window_results.append(
                    {
                        "window_index": index,
                        "start_date": validation_result["start_date"],
                        "end_date": validation_result["end_date"],
                        "summary": validation_result.get("summaries", {}).get(horizon, {}),
                        "policy_review": validation_result.get("policy_reviews", {}).get(
                            horizon,
                            {},
                        ),
                        "universe_review": validation_result.get("universe_review", {}),
                    }
                )

            stability_summary = self._summarize_windows(window_results)
            result = {
                "status": "SUCCESS",
                "run_id": run_id,
                "start_date": start_date,
                "end_date": end_date,
                "horizon": horizon,
                "window_size": window_size,
                "step_size": step_size,
                "windows": window_results,
                "stability_summary": stability_summary,
                "message": "Rolling validation completed.",
            }

            report_path = (
                self.db_client.db_path.parents[1]
                / "reports"
                / f"rolling_validation_{horizon}d_{end_date}.md"
            )
            write_text(
                report_path,
                RollingValidationReporter().build_report(result=result, horizon=horizon),
            )
            result["report_path"] = str(report_path)

            self.run_logger.log_event(
                run_id=run_id,
                module="validate_rolling_workflow",
                level="INFO",
                message="Rolling validation workflow completed",
                payload=result,
            )
            self.run_logger.finish_run(run_id=run_id, status="SUCCESS", summary=result)
            return result
        except Exception as exc:  # pragma: no cover - defensive logging path
            self.run_logger.log_event(
                run_id=run_id,
                module="validate_rolling_workflow",
                level="ERROR",
                message="Rolling validation workflow failed",
                payload={"error": str(exc), "start_date": start_date, "end_date": end_date},
            )
            self.run_logger.finish_run(
                run_id=run_id,
                status="FAILED",
                summary={"start_date": start_date, "end_date": end_date, "error": str(exc)},
            )
            raise

    @staticmethod
    def _build_windows(
        *,
        trade_dates: list[str],
        window_size: int,
        step_size: int,
    ) -> list[dict[str, str]]:
        if window_size <= 0 or step_size <= 0:
            raise ValueError("window_size and step_size must be positive integers.")
        if not trade_dates:
            return []
        if len(trade_dates) <= window_size:
            return [{"start_date": trade_dates[0], "end_date": trade_dates[-1]}]

        windows: list[dict[str, str]] = []
        last_end_date = ""
        for end_index in range(window_size - 1, len(trade_dates), step_size):
            start_index = end_index - window_size + 1
            window = {
                "start_date": trade_dates[start_index],
                "end_date": trade_dates[end_index],
            }
            windows.append(window)
            last_end_date = window["end_date"]

        if last_end_date != trade_dates[-1]:
            windows.append(
                {
                    "start_date": trade_dates[-window_size],
                    "end_date": trade_dates[-1],
                }
            )

        deduped: list[dict[str, str]] = []
        seen_pairs: set[tuple[str, str]] = set()
        for window in windows:
            pair = (window["start_date"], window["end_date"])
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            deduped.append(window)
        return deduped

    @staticmethod
    def _summarize_windows(window_results: list[dict[str, Any]]) -> dict[str, Any]:
        if not window_results:
            return {
                "window_count": 0,
                "positive_window_ratio": 0.0,
                "avg_cumulative_return": 0.0,
                "median_cumulative_return": 0.0,
                "avg_win_rate": 0.0,
                "policy_outperformance_ratio": 0.0,
                "best_window": {},
                "worst_window": {},
            }

        cumulative_returns = [
            float(item.get("summary", {}).get("cumulative_return", 0.0) or 0.0)
            for item in window_results
        ]
        win_rates = [
            float(item.get("summary", {}).get("win_rate", 0.0) or 0.0)
            for item in window_results
        ]
        policy_diffs = []
        for item in window_results:
            review = item.get("policy_review", {})
            policy_avg = float(
                review.get("policy_group", {}).get("avg_trade_return", 0.0) or 0.0
            )
            non_policy_avg = float(
                review.get("non_policy_group", {}).get("avg_trade_return", 0.0) or 0.0
            )
            policy_diffs.append(policy_avg - non_policy_avg)

        best_window = max(
            window_results,
            key=lambda item: float(
                item.get("summary", {}).get("cumulative_return", 0.0) or 0.0
            ),
        )
        worst_window = min(
            window_results,
            key=lambda item: float(
                item.get("summary", {}).get("cumulative_return", 0.0) or 0.0
            ),
        )

        return {
            "window_count": len(window_results),
            "positive_window_ratio": sum(value > 0 for value in cumulative_returns)
            / max(len(cumulative_returns), 1),
            "avg_cumulative_return": sum(cumulative_returns) / max(len(cumulative_returns), 1),
            "median_cumulative_return": median(cumulative_returns),
            "avg_win_rate": sum(win_rates) / max(len(win_rates), 1),
            "policy_outperformance_ratio": sum(value > 0 for value in policy_diffs)
            / max(len(policy_diffs), 1),
            "best_window": {
                "start_date": best_window.get("start_date", ""),
                "end_date": best_window.get("end_date", ""),
                "cumulative_return": float(
                    best_window.get("summary", {}).get("cumulative_return", 0.0) or 0.0
                ),
            },
            "worst_window": {
                "start_date": worst_window.get("start_date", ""),
                "end_date": worst_window.get("end_date", ""),
                "cumulative_return": float(
                    worst_window.get("summary", {}).get("cumulative_return", 0.0) or 0.0
                ),
            },
        }
