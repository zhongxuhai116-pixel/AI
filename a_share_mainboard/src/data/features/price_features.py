from __future__ import annotations

import pandas as pd


class PriceFeatureCalculator:
    def compute(self, bars_df: pd.DataFrame) -> pd.DataFrame:
        if bars_df.empty:
            return pd.DataFrame(
                columns=[
                    "trade_date",
                    "symbol",
                    "ret_5d",
                    "ret_10d",
                    "volatility_10d",
                    "ma_gap_5",
                ]
            )

        data = bars_df.copy()
        data = data.sort_values(["symbol", "trade_date"], ignore_index=True)
        data["close"] = pd.to_numeric(data["close"], errors="coerce")
        data["daily_return"] = data.groupby("symbol")["close"].pct_change()
        data["ret_5d"] = data.groupby("symbol")["close"].pct_change(5)
        data["ret_10d"] = data.groupby("symbol")["close"].pct_change(10)
        data["volatility_10d"] = (
            data.groupby("symbol")["daily_return"].rolling(10).std().reset_index(level=0, drop=True)
        )
        data["ma_5"] = (
            data.groupby("symbol")["close"].rolling(5).mean().reset_index(level=0, drop=True)
        )
        data["ma_gap_5"] = (data["close"] / data["ma_5"]) - 1.0
        return data[
            ["trade_date", "symbol", "ret_5d", "ret_10d", "volatility_10d", "ma_gap_5"]
        ].copy()
