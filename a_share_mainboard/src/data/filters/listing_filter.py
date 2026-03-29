from __future__ import annotations

import pandas as pd


class ListingFilter:
    def __init__(self, min_listing_days: int) -> None:
        self.min_listing_days = min_listing_days

    def apply(self, df: pd.DataFrame, trade_date: str) -> pd.DataFrame:
        if df.empty:
            return df

        filtered = df.copy()
        trade_ts = pd.to_datetime(trade_date)
        filtered["list_date"] = pd.to_datetime(filtered["list_date"], errors="coerce")
        filtered["list_days"] = (trade_ts - filtered["list_date"]).dt.days
        return filtered[filtered["list_days"].fillna(-1) >= self.min_listing_days].copy()
