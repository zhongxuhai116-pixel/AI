from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from data.filters.liquidity_filter import LiquidityFilter
from data.filters.listing_filter import ListingFilter
from data.filters.st_filter import STFilter
from data.filters.suspension_filter import SuspensionFilter
from data.storage.repositories import MarketRepository, ResearchRepository
from infra.config.settings import UniverseSettings
from infra.utils.dates import add_days


@dataclass(slots=True)
class StockPoolBuilder:
    settings: UniverseSettings
    market_repo: MarketRepository
    repo: ResearchRepository
    listing_filter: ListingFilter = field(init=False)
    st_filter: STFilter = field(init=False)
    suspension_filter: SuspensionFilter = field(init=False)
    liquidity_filter: LiquidityFilter = field(init=False)

    def __post_init__(self) -> None:
        self.listing_filter = ListingFilter(min_listing_days=self.settings.min_listing_days)
        self.st_filter = STFilter()
        self.suspension_filter = SuspensionFilter()
        self.liquidity_filter = LiquidityFilter(min_avg_amount=self.settings.min_avg_amount)

    def build(self, trade_date: str) -> int:
        instruments = self.market_repo.get_instruments()
        if instruments.empty:
            return 0

        lookback_start = add_days(trade_date, -60)
        price_history = self.market_repo.get_price_history(
            start_date=lookback_start,
            end_date=trade_date,
        )
        snapshot = self._build_snapshot(instruments=instruments, price_history=price_history, trade_date=trade_date)

        eligible_frame = snapshot[snapshot["board"].isin(set(self.settings.allowed_boards))].copy()
        eligible_frame = self.listing_filter.apply(eligible_frame, trade_date=trade_date)
        if self.settings.exclude_st:
            eligible_frame = self.st_filter.apply(eligible_frame)
        eligible_frame = self.suspension_filter.apply(eligible_frame)
        eligible_frame = self.liquidity_filter.apply(eligible_frame)

        eligible_symbols = set(eligible_frame["symbol"].tolist())
        snapshot["eligible"] = snapshot["symbol"].isin(eligible_symbols)
        snapshot["reject_reason_codes"] = snapshot.apply(self._collect_reject_reasons, axis=1)

        payload = snapshot[
            [
                "trade_date",
                "symbol",
                "eligible",
                "reject_reason_codes",
                "list_days",
                "liquidity_score",
            ]
        ].sort_values(["eligible", "symbol"], ascending=[False, True], ignore_index=True)
        return self.repo.save_stock_pool(payload)

    def _build_snapshot(
        self,
        *,
        instruments: pd.DataFrame,
        price_history: pd.DataFrame,
        trade_date: str,
    ) -> pd.DataFrame:
        snapshot = instruments.copy()
        snapshot["trade_date"] = pd.to_datetime(trade_date).date()
        snapshot["list_date"] = pd.to_datetime(snapshot["list_date"], errors="coerce")
        snapshot["list_days"] = (
            pd.to_datetime(trade_date) - snapshot["list_date"]
        ).dt.days.fillna(-1).astype(int)

        if price_history.empty:
            snapshot["latest_trade_date"] = pd.NaT
            snapshot["is_suspended"] = True
            snapshot["avg_amount_20d"] = 0.0
            snapshot["liquidity_score"] = 0.0
            snapshot["has_trade_bar"] = False
            return snapshot

        history = price_history.copy()
        history["trade_date"] = pd.to_datetime(history["trade_date"], errors="coerce")
        history = history.sort_values(["symbol", "trade_date"], ignore_index=True)

        latest = history.groupby("symbol", as_index=False).tail(1).copy()
        latest = latest.rename(
            columns={
                "trade_date": "latest_trade_date",
                "is_suspended": "latest_is_suspended",
            }
        )

        avg_amount = (
            history.groupby("symbol", as_index=False)["amount"].mean().rename(
                columns={"amount": "avg_amount_20d"}
            )
        )

        snapshot = snapshot.merge(
            latest[["symbol", "latest_trade_date", "latest_is_suspended"]],
            on="symbol",
            how="left",
        )
        snapshot = snapshot.merge(avg_amount, on="symbol", how="left")

        snapshot["avg_amount_20d"] = snapshot["avg_amount_20d"].fillna(0.0)
        snapshot["liquidity_score"] = snapshot["avg_amount_20d"] / max(
            self.settings.min_avg_amount,
            1.0,
        )
        snapshot["has_trade_bar"] = (
            snapshot["latest_trade_date"].dt.date == pd.to_datetime(trade_date).date()
        )
        snapshot["is_suspended"] = snapshot["latest_is_suspended"].fillna(False).astype(bool)
        snapshot.loc[~snapshot["has_trade_bar"], "is_suspended"] = True
        return snapshot

    def _collect_reject_reasons(self, row: pd.Series) -> str:
        reasons: list[str] = []

        if row.get("board") not in set(self.settings.allowed_boards):
            reasons.append("board")
        if self.settings.exclude_st and bool(row.get("is_st", False)):
            reasons.append("st")
        if int(row.get("list_days", -1)) < self.settings.min_listing_days:
            reasons.append("listing_days")
        if not bool(row.get("has_trade_bar", False)):
            reasons.append("missing_bar")
        elif bool(row.get("is_suspended", False)):
            reasons.append("suspended")
        if float(row.get("avg_amount_20d", 0.0) or 0.0) < self.settings.min_avg_amount:
            reasons.append("liquidity")

        return "|".join(reasons)
