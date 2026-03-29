from __future__ import annotations

import pandas as pd


class SuspensionFilter:
    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        return df[df["has_trade_bar"].fillna(False) & ~df["is_suspended"].fillna(True)].copy()
