from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from adapters.market.akshare_provider import AKShareProvider
from data.collectors.index_daily_collector import IndexDailyCollector
from data.collectors.instrument_collector import InstrumentCollector
from data.collectors.price_daily_collector import PriceDailyCollector
from data.collectors.trade_calendar_collector import TradeCalendarCollector
from data.storage.duckdb_client import DuckDBClient
from data.storage.repositories import MarketRepository
from infra.config.settings import Settings
from infra.logging.run_logger import RunLogger


@dataclass(slots=True)
class RebuildWorkflow:
    settings: Settings
    run_logger: RunLogger
    db_client: DuckDBClient

    def run(self, start_date: str, end_date: str) -> dict[str, Any]:
        run_id = self.run_logger.start_run(run_type="rebuild", config_hash="phase1-history-backfill")
        market_repo = MarketRepository(self.db_client)
        provider = AKShareProvider(self.settings.data)

        try:
            self.run_logger.log_event(
                run_id=run_id,
                module="rebuild_workflow",
                level="INFO",
                message="Historical rebuild started",
                payload={"start_date": start_date, "end_date": end_date},
            )

            calendar_count = TradeCalendarCollector(provider=provider, repo=market_repo).collect(
                start_date=self.settings.data.default_start_date,
                end_date=end_date,
            )
            instrument_count = InstrumentCollector(provider=provider, repo=market_repo).collect()
            instrument_symbols = market_repo.get_instruments()["symbol"].tolist()

            index_count = IndexDailyCollector(provider=provider, repo=market_repo).collect(
                start_date=start_date,
                end_date=end_date,
                index_codes=self.settings.data.index_codes,
            )
            price_count = PriceDailyCollector(provider=provider, repo=market_repo).collect(
                start_date=start_date,
                end_date=end_date,
                symbols=instrument_symbols,
            )

            result = {
                "status": "SUCCESS",
                "run_id": run_id,
                "start_date": start_date,
                "end_date": end_date,
                "counts": {
                    "trade_calendar": calendar_count,
                    "instrument_basic": instrument_count,
                    "index_daily": index_count,
                    "price_daily": price_count,
                },
                "message": "Historical mainboard price and index data backfilled.",
            }
            self.run_logger.log_event(
                run_id=run_id,
                module="rebuild_workflow",
                level="INFO",
                message="Historical rebuild completed",
                payload=result,
            )
            self.run_logger.finish_run(run_id=run_id, status="SUCCESS", summary=result)
            return result
        except Exception as exc:  # pragma: no cover - defensive logging path
            self.run_logger.log_event(
                run_id=run_id,
                module="rebuild_workflow",
                level="ERROR",
                message="Historical rebuild failed",
                payload={"error": str(exc), "start_date": start_date, "end_date": end_date},
            )
            self.run_logger.finish_run(
                run_id=run_id,
                status="FAILED",
                summary={"start_date": start_date, "end_date": end_date, "error": str(exc)},
            )
            raise
