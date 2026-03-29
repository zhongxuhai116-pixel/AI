from __future__ import annotations

import pandas as pd

from infra.config.settings import DataSettings


class TushareProvider:
    def __init__(self, config: DataSettings) -> None:
        self.config = config

    def fetch_trade_calendar(self, start_date: str, end_date: str) -> pd.DataFrame:
        _ = (start_date, end_date)
        raise NotImplementedError("Tushare trade calendar adapter is pending implementation.")

    def fetch_instruments(self) -> pd.DataFrame:
        raise NotImplementedError("Tushare instrument adapter is pending implementation.")

    def fetch_price_daily(
        self,
        symbols: list[str] | None,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        _ = (symbols, start_date, end_date)
        raise NotImplementedError("Tushare price daily adapter is pending implementation.")

    def fetch_index_daily(
        self,
        index_codes: list[str],
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        _ = (index_codes, start_date, end_date)
        raise NotImplementedError("Tushare index daily adapter is pending implementation.")

