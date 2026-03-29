from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Instrument:
    symbol: str
    exchange: str
    board: str
    name: str
    list_date: str
    is_st: bool
    industry_l1: str | None = None
    industry_l2: str | None = None


@dataclass(slots=True)
class DailyBar:
    trade_date: str
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
    turnover_rate: float | None = None
    adj_factor: float | None = None
    upper_limit_price: float | None = None
    lower_limit_price: float | None = None
    is_suspended: bool = False


@dataclass(slots=True)
class FeatureRow:
    trade_date: str
    symbol: str
    values: dict[str, float | int | str | None]


@dataclass(slots=True)
class ScoreRow:
    trade_date: str
    symbol: str
    horizon: int
    model_name: str
    score_raw: float
    score_rank: int | None = None
    score_bucket: int | None = None


@dataclass(slots=True)
class SignalRow:
    trade_date: str
    symbol: str
    horizon: int
    final_rank: int
    action: str
    target_weight: float
    rule_tags: list[str] = field(default_factory=list)
    blocked_reason: str | None = None


@dataclass(slots=True)
class MarketRegimeRow:
    trade_date: str
    regime_label: str
    style_label: str
    breadth_up_ratio: float
    limit_up_count: int
    limit_down_count: int
    volume_heat: str
    extra: dict[str, Any] = field(default_factory=dict)

