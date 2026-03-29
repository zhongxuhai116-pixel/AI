from __future__ import annotations

import pandas as pd


class BreadthMetricsCalculator:
    def compute(self, stock_df: pd.DataFrame) -> dict[str, float | int | str]:
        if stock_df.empty:
            return {
                "breadth_up_ratio": 0.0,
                "limit_up_count": 0,
                "limit_down_count": 0,
                "volume_heat": "unknown",
            }

        data = stock_df.copy()
        data["open"] = pd.to_numeric(data["open"], errors="coerce")
        data["close"] = pd.to_numeric(data["close"], errors="coerce")
        data["amount"] = pd.to_numeric(data["amount"], errors="coerce")
        up_ratio = float((data["close"] > data["open"]).fillna(False).mean())
        total_amount = float(data["amount"].fillna(0.0).sum())

        if total_amount >= 300_000_000_000:
            volume_heat = "hot"
        elif total_amount >= 150_000_000_000:
            volume_heat = "warm"
        else:
            volume_heat = "cold"

        return {
            "breadth_up_ratio": up_ratio,
            "limit_up_count": 0,
            "limit_down_count": 0,
            "volume_heat": volume_heat,
        }
