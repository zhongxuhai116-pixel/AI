from __future__ import annotations

from dataclasses import dataclass

from data.collectors.index_daily_collector import IndexDailyCollector
from data.collectors.instrument_collector import InstrumentCollector
from data.collectors.price_daily_collector import PriceDailyCollector
from data.collectors.trade_calendar_collector import TradeCalendarCollector


@dataclass(slots=True)
class MarketDataService:
    trade_calendar_collector: TradeCalendarCollector
    instrument_collector: InstrumentCollector
    price_daily_collector: PriceDailyCollector
    index_daily_collector: IndexDailyCollector

