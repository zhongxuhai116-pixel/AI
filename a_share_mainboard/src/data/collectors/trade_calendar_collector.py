from __future__ import annotations

from adapters.market.base import MarketDataProvider
from data.storage.repositories import MarketRepository


class TradeCalendarCollector:
    def __init__(self, provider: MarketDataProvider, repo: MarketRepository) -> None:
        self.provider = provider
        self.repo = repo

    def collect(self, start_date: str, end_date: str) -> int:
        df = self.provider.fetch_trade_calendar(start_date=start_date, end_date=end_date)
        return self.repo.save_trade_calendar(df)

