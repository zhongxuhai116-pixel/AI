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


class Settings(BaseModel):
    app: AppSettings
    data: DataSettings
    universe: UniverseSettings
    strategy: StrategySettings
    validation: ValidationSettings
    ai: AISettings
    feishu: FeishuSettings
