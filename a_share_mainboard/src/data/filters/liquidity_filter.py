from __future__ import annotations

import pandas as pd


class LiquidityFilter:
    def __init__(self, min_avg_amount: float) -> None:
        self.min_avg_amount = min_avg_amount

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        return df[df["avg_amount_20d"].fillna(0.0) >= self.min_avg_amount].copy()
