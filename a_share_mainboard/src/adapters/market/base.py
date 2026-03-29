from __future__ import annotations

from typing import Protocol

import pandas as pd


class MarketDataProvider(Protocol):
    def fetch_trade_calendar(self, start_date: str, end_date: str) -> pd.DataFrame: ...

    def fetch_instruments(self) -> pd.DataFrame: ...

    def fetch_price_daily(
        self,
        symbols: list[str] | None,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame: ...

    def fetch_index_daily(
        self,
        index_codes: list[str],
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame: ...

