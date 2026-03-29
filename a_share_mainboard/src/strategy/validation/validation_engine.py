from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from data.features.feature_pipeline import FeaturePipeline
from data.filters.stock_pool_builder import StockPoolBuilder
from data.storage.repositories import MarketRepository, ResearchRepository
from infra.config.settings import PolicySettings, StrategySettings, UniverseSettings, ValidationSettings
from strategy.market_scan.market_scan_service import MarketScanService
from strategy.policy.policy_overlay_service import PolicyOverlayService
from strategy.rules.rule_engine import RuleEngine
from strategy.stock_selection.baseline_ranker import BaselineRanker
from strategy.stock_selection.selection_service import SelectionService
from strategy.validation.execution_model import ExecutionModel


@dataclass(slots=True)
class ValidationEngine:
    market_repo: MarketRepository
    repo: ResearchRepository
    execution_model: ExecutionModel
    settings: ValidationSettings
    universe_settings: UniverseSettings
    strategy_settings: StrategySettings
    policy_settings: PolicySettings
    benchmark_index: str = "sh000001"

    def run(
        self,
        *,
        start_date: str,
        end_date: str,
        horizons: list[int],
        run_id: str,
    ) -> dict:
        min_price_date, max_price_date = self.market_repo.get_price_date_bounds()
        if min_price_date is None or max_price_date is None:
            return {
                "status": "EMPTY",
                "run_id": run_id,
                "start_date": start_date,
                "end_date": end_date,
                "horizons": horizons,
                "message": "No price history is available for validation.",
                "summaries": {},
            }

        effective_start = max(start_date, min_price_date)
        effective_end = min(end_date, max_price_date)
        signal_dates = self.market_repo.get_open_trade_dates(
            start_date=effective_start,
            end_date=effective_end,
        )
        if not signal_dates:
            return {
                "status": "EMPTY",
                "run_id": run_id,
                "start_date": effective_start,
                "end_date": effective_end,
                "horizons": horizons,
                "message": "No open trade dates matched the validation window.",
                "summaries": {},
            }

        self._materialize_signal_chain(signal_dates=signal_dates, horizons=horizons)
        summaries = self._evaluate_returns(
            signal_dates=signal_dates,
            horizons=horizons,
            max_price_date=max_price_date,
        )
        metric_rows = self._build_metric_rows(run_id=run_id, summaries=summaries)
        metric_count = self.repo.save_validation_metrics(pd.DataFrame(metric_rows))

        return {
            "status": "SUCCESS",
            "run_id": run_id,
            "start_date": effective_start,
            "end_date": effective_end,
            "horizons": horizons,
            "evaluated_trade_dates": len(signal_dates),
            "metric_rows": metric_count,
            "summaries": summaries,
        }

    def _materialize_signal_chain(self, *, signal_dates: list[str], horizons: list[int]) -> None:
        stock_pool_builder = StockPoolBuilder(
            settings=self.universe_settings,
            market_repo=self.market_repo,
            repo=self.repo,
        )
        feature_pipeline = FeaturePipeline(
            market_repo=self.market_repo,
            repo=self.repo,
            benchmark_index=self.benchmark_index,
        )
        market_scan_service = MarketScanService(
            market_repo=self.market_repo,
            repo=self.repo,
            benchmark_index=self.benchmark_index,
        )
        all_instruments_df = self.market_repo.get_instruments()
        instruments_df = all_instruments_df[
            [
                column
                for column in ["symbol", "name", "industry_l1", "industry_l2"]
                if column in all_instruments_df.columns
            ]
        ]
        selection_service = SelectionService(
            repo=self.repo,
            baseline_ranker=BaselineRanker(
                factor_weights=self.strategy_settings.baseline_weights
            ),
        )
        rule_engine = RuleEngine(
            settings=self.strategy_settings,
            repo=self.repo,
            policy_service=PolicyOverlayService(settings=self.policy_settings),
            instruments_df=instruments_df,
        )

        for trade_date in signal_dates:
            stock_pool_builder.build(trade_date=trade_date)
            feature_pipeline.run(trade_date=trade_date)
            market_scan_service.run(trade_date=trade_date)
            selection_service.run(trade_date=trade_date, horizons=horizons)
            self.repo.delete_signals_for_trade_date(trade_date)
            for horizon in horizons:
                rule_engine.run(trade_date=trade_date, horizon=horizon)

    def _evaluate_returns(
        self,
        *,
        signal_dates: list[str],
        horizons: list[int],
        max_price_date: str,
    ) -> dict[int, dict]:
        signals_df = self.repo.read_dataframe(
            """
            SELECT trade_date, symbol, horizon, final_rank, target_weight
            FROM signals_daily
            WHERE trade_date BETWEEN ? AND ?
            ORDER BY trade_date, horizon, final_rank, symbol
            """,
            (signal_dates[0], signal_dates[-1]),
        )
        if signals_df.empty:
            return {horizon: self._empty_summary(horizon=horizon) for horizon in horizons}

        price_history = self.market_repo.get_price_history(
            start_date=signal_dates[0],
            end_date=max_price_date,
        )
        if price_history.empty:
            return {horizon: self._empty_summary(horizon=horizon) for horizon in horizons}

        signals_df["trade_date"] = pd.to_datetime(signals_df["trade_date"], errors="coerce").dt.strftime(
            "%Y-%m-%d"
        )
        price_history["trade_date"] = pd.to_datetime(
            price_history["trade_date"], errors="coerce"
        ).dt.strftime("%Y-%m-%d")
        signal_date_set = set(signal_dates)
        all_trade_dates = self.market_repo.get_open_trade_dates(
            start_date=signal_dates[0],
            end_date=max_price_date,
        )
        date_to_index = {trade_date: index for index, trade_date in enumerate(all_trade_dates)}
        price_lookup = (
            price_history.set_index(["symbol", "trade_date"])[
                ["open", "close", "upper_limit_price", "lower_limit_price"]
            ]
            .to_dict(orient="index")
        )

        summaries: dict[int, dict] = {}
        for horizon in horizons:
            horizon_signals = signals_df[signals_df["horizon"] == horizon].copy()
            if horizon_signals.empty:
                summaries[horizon] = self._empty_summary(horizon=horizon)
                continue

            outcomes: list[dict] = []
            skipped_trades = 0
            for row in horizon_signals.to_dict(orient="records"):
                signal_date = row["trade_date"]
                if signal_date not in signal_date_set or signal_date not in date_to_index:
                    skipped_trades += 1
                    continue

                signal_index = date_to_index[signal_date]
                entry_index = signal_index + 1
                exit_index = signal_index + horizon
                if exit_index >= len(all_trade_dates) or entry_index >= len(all_trade_dates):
                    skipped_trades += 1
                    continue

                entry_date = all_trade_dates[entry_index]
                exit_date = all_trade_dates[exit_index]
                entry_bar = price_lookup.get((row["symbol"], entry_date))
                exit_bar = price_lookup.get((row["symbol"], exit_date))
                if entry_bar is None or exit_bar is None:
                    skipped_trades += 1
                    continue

                try:
                    trade_return = self.execution_model.calculate_trade_return(
                        entry_bar=entry_bar,
                        exit_bar=exit_bar,
                    )
                except ValueError:
                    skipped_trades += 1
                    continue

                outcomes.append(
                    {
                        "signal_date": signal_date,
                        "symbol": row["symbol"],
                        "entry_date": entry_date,
                        "exit_date": exit_date,
                        "trade_return": trade_return,
                    }
                )

            summaries[horizon] = self._summarize_outcomes(
                horizon=horizon,
                signal_dates=signal_dates,
                outcomes_df=pd.DataFrame(outcomes),
                skipped_trades=skipped_trades,
            )

        return summaries

    def _build_metric_rows(self, *, run_id: str, summaries: dict[int, dict]) -> list[dict]:
        rows: list[dict] = []
        for horizon, summary in summaries.items():
            for metric_name, metric_value in summary.items():
                if metric_name in {"horizon"}:
                    continue
                rows.append(
                    {
                        "run_id": run_id,
                        "horizon": horizon,
                        "metric_name": metric_name,
                        "metric_value": float(metric_value),
                    }
                )
        return rows

    @staticmethod
    def _summarize_outcomes(
        *,
        horizon: int,
        signal_dates: list[str],
        outcomes_df: pd.DataFrame,
        skipped_trades: int,
    ) -> dict:
        if outcomes_df.empty:
            return {
                "horizon": horizon,
                "signal_days": 0.0,
                "trade_count": 0.0,
                "avg_trade_return": 0.0,
                "avg_cohort_return": 0.0,
                "win_rate": 0.0,
                "cumulative_return": 0.0,
                "max_drawdown": 0.0,
                "skipped_trades": float(skipped_trades),
                "coverage_ratio": 0.0,
            }

        outcomes_df = outcomes_df.sort_values(["signal_date", "symbol"], ignore_index=True)
        cohort_returns = outcomes_df.groupby("signal_date")["trade_return"].mean().sort_index()
        equity_curve = (1.0 + cohort_returns).cumprod()
        rolling_peak = equity_curve.cummax()
        drawdown = (equity_curve / rolling_peak) - 1.0

        return {
            "horizon": horizon,
            "signal_days": float(len(cohort_returns)),
            "trade_count": float(len(outcomes_df)),
            "avg_trade_return": float(outcomes_df["trade_return"].mean()),
            "avg_cohort_return": float(cohort_returns.mean()),
            "win_rate": float((outcomes_df["trade_return"] > 0).mean()),
            "cumulative_return": float(equity_curve.iloc[-1] - 1.0),
            "max_drawdown": float(drawdown.min()),
            "skipped_trades": float(skipped_trades),
            "coverage_ratio": float(len(cohort_returns) / max(len(signal_dates), 1)),
        }

    @staticmethod
    def _empty_summary(*, horizon: int) -> dict:
        return {
            "horizon": horizon,
            "signal_days": 0.0,
            "trade_count": 0.0,
            "avg_trade_return": 0.0,
            "avg_cohort_return": 0.0,
            "win_rate": 0.0,
            "cumulative_return": 0.0,
            "max_drawdown": 0.0,
            "skipped_trades": 0.0,
            "coverage_ratio": 0.0,
        }
