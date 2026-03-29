from __future__ import annotations

from adapters.market.base import MarketDataProvider
from data.storage.repositories import MarketRepository


class IndexDailyCollector:
    def __init__(self, provider: MarketDataProvider, repo: MarketRepository) -> None:
        self.provider = provider
        self.repo = repo

    def collect(self, start_date: str, end_date: str, index_codes: list[str]) -> int:
        df = self.provider.fetch_index_daily(
            index_codes=index_codes,
            start_date=start_date,
            end_date=end_date,
        )
        return self.repo.save_index_daily(df)

