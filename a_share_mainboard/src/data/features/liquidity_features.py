from __future__ import annotations

import pandas as pd


class LiquidityFeatureCalculator:
    def compute(self, bars_df: pd.DataFrame) -> pd.DataFrame:
        if bars_df.empty:
            return pd.DataFrame(
                columns=["trade_date", "symbol", "amount_ratio_5d", "turnover_5d"]
            )

        data = bars_df.copy()
        data = data.sort_values(["symbol", "trade_date"], ignore_index=True)
        data["amount"] = pd.to_numeric(data["amount"], errors="coerce")
        data["turnover_rate"] = pd.to_numeric(data["turnover_rate"], errors="coerce")
        data["amount_ma_5"] = (
            data.groupby("symbol")["amount"].rolling(5).mean().reset_index(level=0, drop=True)
        )
        data["amount_ratio_5d"] = data["amount"] / data["amount_ma_5"]
        data["turnover_5d"] = (
            data.groupby("symbol")["turnover_rate"].rolling(5).mean().reset_index(level=0, drop=True)
        )
        return data[["trade_date", "symbol", "amount_ratio_5d", "turnover_5d"]].copy()
