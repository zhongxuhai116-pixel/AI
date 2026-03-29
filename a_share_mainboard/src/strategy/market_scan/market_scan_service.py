from __future__ import annotations

import json
from dataclasses import dataclass

import pandas as pd

from data.storage.repositories import MarketRepository, ResearchRepository
from infra.utils.dates import add_days
from strategy.market_scan.breadth_metrics import BreadthMetricsCalculator
from strategy.market_scan.regime_detector import RegimeDetector


@dataclass(slots=True)
class MarketScanService:
    market_repo: MarketRepository
    repo: ResearchRepository
    benchmark_index: str = "sh000001"

    def run(self, trade_date: str) -> int:
        pool_df = self.repo.get_stock_pool(trade_date=trade_date, eligible_only=True)
        symbols = pool_df["symbol"].tolist()
        start_date = add_days(trade_date, -15)

        stock_df = self.market_repo.get_price_history(
            start_date=start_date,
            end_date=trade_date,
            symbols=symbols,
        )
        latest_stock = stock_df[
            pd.to_datetime(stock_df["trade_date"], errors="coerce").dt.date
            == pd.to_datetime(trade_date).date()
        ].copy()

        index_df = self.market_repo.get_index_history(start_date=start_date, end_date=trade_date)
        benchmark_df = index_df[index_df["index_code"] == self.benchmark_index].copy()
        benchmark_ret_5d = self._compute_benchmark_ret_5d(benchmark_df)

        metrics = BreadthMetricsCalculator().compute(latest_stock)
        metrics["benchmark_ret_5d"] = benchmark_ret_5d
        metrics["eligible_universe_size"] = len(symbols)

        regime = RegimeDetector().detect(metrics)
        payload = pd.DataFrame(
            [
                {
                    "trade_date": trade_date,
                    "regime_label": regime["regime_label"],
                    "style_label": regime["style_label"],
                    "breadth_up_ratio": metrics["breadth_up_ratio"],
                    "limit_up_count": metrics["limit_up_count"],
                    "limit_down_count": metrics["limit_down_count"],
                    "volume_heat": metrics["volume_heat"],
                    "extra_payload": json.dumps(metrics, ensure_ascii=False),
                }
            ]
        )
        self.repo.delete_market_regime_for_trade_date(trade_date)
        return self.repo.save_market_regime(payload)

    @staticmethod
    def _compute_benchmark_ret_5d(index_df: pd.DataFrame) -> float:
        if index_df.empty:
            return 0.0

        benchmark = index_df.copy()
        benchmark = benchmark.sort_values("trade_date", ignore_index=True)
        benchmark["close"] = pd.to_numeric(benchmark["close"], errors="coerce")
        benchmark["ret_5d"] = benchmark["close"].pct_change(5)
        latest = benchmark["ret_5d"].dropna()
        if latest.empty:
            return 0.0
        return float(latest.iloc[-1])
