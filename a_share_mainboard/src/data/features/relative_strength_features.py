from __future__ import annotations

import pandas as pd


class RelativeStrengthFeatureCalculator:
    def compute(self, stock_df: pd.DataFrame, index_df: pd.DataFrame) -> pd.DataFrame:
        if stock_df.empty:
            return pd.DataFrame(columns=["trade_date", "symbol", "rs_index_10d"])

        if index_df.empty:
            output = stock_df[["trade_date", "symbol"]].copy()
            output["rs_index_10d"] = 0.0
            return output

        benchmark = index_df.copy()
        benchmark = benchmark.sort_values("trade_date", ignore_index=True)
        benchmark["close"] = pd.to_numeric(benchmark["close"], errors="coerce")
        benchmark["benchmark_ret_10d"] = benchmark["close"].pct_change(10)
        benchmark = benchmark[["trade_date", "benchmark_ret_10d"]].copy()

        merged = stock_df.merge(benchmark, on="trade_date", how="left")
        merged["rs_index_10d"] = merged["ret_10d"] - merged["benchmark_ret_10d"].fillna(0.0)
        return merged[["trade_date", "symbol", "rs_index_10d"]].copy()
