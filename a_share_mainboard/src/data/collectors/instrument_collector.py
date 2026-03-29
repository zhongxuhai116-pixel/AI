from __future__ import annotations

from adapters.market.base import MarketDataProvider
from data.storage.repositories import MarketRepository


class InstrumentCollector:
    def __init__(self, provider: MarketDataProvider, repo: MarketRepository) -> None:
        self.provider = provider
        self.repo = repo

    def collect(self) -> int:
        df = self.provider.fetch_instruments()
        return self.repo.save_instruments(df)

