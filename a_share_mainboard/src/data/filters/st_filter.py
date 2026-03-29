from __future__ import annotations

import pandas as pd


class STFilter:
    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        return df[~df["is_st"].fillna(False)].copy()
