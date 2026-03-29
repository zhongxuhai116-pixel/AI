from __future__ import annotations

import pandas as pd


class LabelBuilder:
    def build(self, bars_df: pd.DataFrame, horizons: list[int]) -> pd.DataFrame:
        _ = horizons
        return bars_df

