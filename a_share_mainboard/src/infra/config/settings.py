from __future__ import annotations

from pydantic import BaseModel


class AppSettings(BaseModel):
    project_name: str
    environment: str
    timezone: str
    data_root: str
    report_root: str
    log_root: str


class DataSettings(BaseModel):
    market_provider: str
    duckdb_path: str
    parquet_root: str
    default_start_date: str
    index_codes: list[str]


class UniverseSettings(BaseModel):
    allowed_boards: list[str]
    min_listing_days: int
    min_avg_amount: float
    exclude_st: bool


class StrategySettings(BaseModel):
    horizons: list[int]
    top_n: int
    rebalance_frequency: str
    enable_ml_ranker: bool
    baseline_weights: dict[str, float]
    min_amount_ratio_5d: float | None = None
    max_turnover_quantile: float | None = None
    min_ret_5d: float | None = None
    min_rs_index_10d: float | None = None
    require_benchmark_positive: bool = False
    allowed_regimes: list[str] = ["bullish", "neutral"]
    allowed_volume_heat: list[str] = ["warm", "hot"]


class ValidationSettings(BaseModel):
    cost_bps: float
    execution_price_mode: str
    max_positions: int


class AISettings(BaseModel):
    enabled: bool
    model: str
    base_url: str
    timeout_seconds: int
    max_retries: int
    max_symbols_per_day: int
    market_prompt_version: str
    stock_prompt_version: str
    risk_prompt_version: str
    report_prompt_version: str
    api_key_env: str = "OPENAI_API_KEY"


class FeishuSettings(BaseModel):
    enabled: bool
    webhook_url_env: str = "FEISHU_BOT_WEBHOOK"
    signing_secret_env: str = "FEISHU_BOT_SECRET"
    timeout_seconds: int = 15


class PolicyEventSettings(BaseModel):
    date: str
    title: str
    source_url: str


class PolicyThemeSettings(BaseModel):
    name: str
    label: str
    start_date: str
    end_date: str
    weight: float
    summary: str
    source_url: str
    industries: list[str] = []
    name_keywords: list[str] = []
    symbols: list[str] = []
    events: list[PolicyEventSettings] = []


class PolicySettings(BaseModel):
    enabled: bool
    max_total_bonus: float = 0.08
    min_theme_match_count: int = 2
    min_theme_positive_ratio: float = 0.55
    min_theme_amount_ratio_5d: float = 1.0
    sentiment_multiplier_cap: float = 1.35
    fresh_event_days: int = 10
    decay_event_days: int = 45
    event_decay_floor: float = 0.35
    themes: list[PolicyThemeSettings] = []


class Settings(BaseModel):
    app: AppSettings
    data: DataSettings
    universe: UniverseSettings
    strategy: StrategySettings
    validation: ValidationSettings
    ai: AISettings
    feishu: FeishuSettings
    policy: PolicySettings
