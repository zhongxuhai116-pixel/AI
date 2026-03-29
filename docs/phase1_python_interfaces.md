# 第一阶段 Python 模块接口与方法签名

## 1. 文档目标

本文件定义第一阶段核心模块的 Python 接口草案，目标是：

- 先统一模块边界
- 再开始写实现
- 避免数据层、策略层、AI 层互相污染

以下签名是 `建议设计`，不要求逐字照抄，但建议保持分层结构不变。

## 2. 领域对象

### `src/domain/types.py`

```python
from typing import NewType

Symbol = NewType("Symbol", str)
TradeDate = NewType("TradeDate", str)
RunId = NewType("RunId", str)
ModelName = NewType("ModelName", str)
PromptVersion = NewType("PromptVersion", str)
```

### `src/domain/enums.py`

```python
from enum import Enum


class BoardType(str, Enum):
    MAIN = "MAIN"


class HorizonType(int, Enum):
    H5 = 5
    H10 = 10


class SignalAction(str, Enum):
    BUY = "BUY"
    HOLD = "HOLD"
    SKIP = "SKIP"


class RunStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"


class RejectReason(str, Enum):
    NOT_MAIN_BOARD = "NOT_MAIN_BOARD"
    ST_FLAG = "ST_FLAG"
    SUSPENDED = "SUSPENDED"
    LISTING_DAYS_SHORT = "LISTING_DAYS_SHORT"
    LOW_LIQUIDITY = "LOW_LIQUIDITY"
    MISSING_DATA = "MISSING_DATA"
```

### `src/domain/models.py`

```python
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
```

## 3. 配置对象

### `src/infra/config/settings.py`

```python
from pydantic import BaseModel


class AppSettings(BaseModel):
    project_root: str
    data_root: str
    report_root: str
    log_root: str


class DataSettings(BaseModel):
    market_provider: str
    duckdb_path: str
    parquet_root: str
    default_start_date: str


class UniverseSettings(BaseModel):
    allowed_boards: list[str]
    min_listing_days: int
    min_avg_amount: float
    exclude_st: bool


class StrategySettings(BaseModel):
    horizons: list[int]
    top_n: int
    rebalance_frequency: str
    baseline_weights: dict[str, float]
    enable_ml_ranker: bool


class ValidationSettings(BaseModel):
    cost_bps: float
    execution_price_mode: str
    max_positions: int


class AISettings(BaseModel):
    enabled: bool
    model: str
    timeout_seconds: int
    max_retries: int
    max_symbols_per_day: int
    market_prompt_version: str
    stock_prompt_version: str
    risk_prompt_version: str
    report_prompt_version: str


class Settings(BaseModel):
    app: AppSettings
    data: DataSettings
    universe: UniverseSettings
    strategy: StrategySettings
    validation: ValidationSettings
    ai: AISettings
```

## 4. 数据供应商接口

### `src/adapters/market/base.py`

```python
from typing import Protocol
import pandas as pd


class MarketDataProvider(Protocol):
    def fetch_trade_calendar(self, start_date: str, end_date: str) -> pd.DataFrame: ...

    def fetch_instruments(self) -> pd.DataFrame: ...

    def fetch_price_daily(
        self,
        symbols: list[str] | None,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame: ...

    def fetch_index_daily(
        self,
        index_codes: list[str],
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame: ...
```

### `src/adapters/market/akshare_provider.py`

```python
class AKShareProvider:
    def __init__(self, config: DataSettings) -> None: ...

    def fetch_trade_calendar(self, start_date: str, end_date: str) -> pd.DataFrame: ...

    def fetch_instruments(self) -> pd.DataFrame: ...

    def fetch_price_daily(
        self,
        symbols: list[str] | None,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame: ...

    def fetch_index_daily(
        self,
        index_codes: list[str],
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame: ...
```

## 5. LLM 客户端接口

### `src/adapters/llm/base.py`

```python
from typing import Any, Protocol


class StructuredLLMClient(Protocol):
    def generate_json(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        output_schema: dict[str, Any],
        timeout_seconds: int,
    ) -> dict[str, Any]: ...
```

### `src/adapters/llm/openai_client.py`

```python
class OpenAIClient:
    def __init__(self, api_key: str, model: str, timeout_seconds: int, max_retries: int) -> None: ...

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        output_schema: dict[str, Any],
        timeout_seconds: int,
    ) -> dict[str, Any]: ...

    def _build_request(self, system_prompt: str, user_payload: dict, output_schema: dict) -> dict: ...

    def _send_request(self, request: dict, timeout_seconds: int) -> dict: ...

    def _parse_response(self, response: dict) -> dict: ...
```

## 6. 存储接口

### `src/data/storage/duckdb_client.py`

```python
class DuckDBClient:
    def __init__(self, db_path: str) -> None: ...

    def execute(self, sql: str, params: tuple | None = None) -> None: ...

    def fetch_df(self, sql: str, params: tuple | None = None): ...

    def close(self) -> None: ...
```

### `src/data/storage/repositories.py`

```python
class TableRepository:
    def __init__(self, client: DuckDBClient) -> None: ...

    def upsert_dataframe(self, table_name: str, df, key_columns: list[str]) -> int: ...

    def read_dataframe(self, sql: str, params: tuple | None = None): ...


class MarketRepository(TableRepository):
    def save_trade_calendar(self, df) -> int: ...
    def save_instruments(self, df) -> int: ...
    def save_price_daily(self, df) -> int: ...
    def save_index_daily(self, df) -> int: ...


class ResearchRepository(TableRepository):
    def save_stock_pool(self, df) -> int: ...
    def save_features(self, df) -> int: ...
    def save_market_regime(self, df) -> int: ...
    def save_model_scores(self, df) -> int: ...
    def save_signals(self, df) -> int: ...
    def save_validation_metrics(self, df) -> int: ...
    def save_daily_report(self, df) -> int: ...


class LogRepository(TableRepository):
    def save_pipeline_run(self, df) -> int: ...
    def save_pipeline_events(self, df) -> int: ...
    def save_ai_calls(self, df) -> int: ...
```

## 7. 采集器接口

### `src/data/collectors/trade_calendar_collector.py`

```python
class TradeCalendarCollector:
    def __init__(self, provider: MarketDataProvider, repo: MarketRepository) -> None: ...

    def collect(self, start_date: str, end_date: str) -> int: ...
```

### `src/data/collectors/instrument_collector.py`

```python
class InstrumentCollector:
    def __init__(self, provider: MarketDataProvider, repo: MarketRepository) -> None: ...

    def collect(self) -> int: ...
```

### `src/data/collectors/price_daily_collector.py`

```python
class PriceDailyCollector:
    def __init__(self, provider: MarketDataProvider, repo: MarketRepository) -> None: ...

    def collect(self, start_date: str, end_date: str, symbols: list[str] | None = None) -> int: ...
```

### `src/data/collectors/index_daily_collector.py`

```python
class IndexDailyCollector:
    def __init__(self, provider: MarketDataProvider, repo: MarketRepository) -> None: ...

    def collect(self, start_date: str, end_date: str, index_codes: list[str]) -> int: ...
```

## 8. 股票池过滤接口

### `src/data/filters/stock_pool_builder.py`

```python
class StockPoolBuilder:
    def __init__(
        self,
        settings: UniverseSettings,
        repo: ResearchRepository,
    ) -> None: ...

    def build(self, trade_date: str) -> int: ...

    def load_candidates(self, trade_date: str): ...

    def apply_filters(self, trade_date: str): ...
```

### 各过滤器统一风格

```python
class ListingFilter:
    def __init__(self, min_listing_days: int) -> None: ...
    def apply(self, df, trade_date: str): ...


class STFilter:
    def apply(self, df): ...


class SuspensionFilter:
    def apply(self, df): ...


class LiquidityFilter:
    def __init__(self, min_avg_amount: float) -> None: ...
    def apply(self, df): ...
```

## 9. 特征计算接口

### `src/data/features/feature_pipeline.py`

```python
class FeaturePipeline:
    def __init__(self, repo: ResearchRepository) -> None: ...

    def run(self, trade_date: str) -> int: ...

    def load_inputs(self, trade_date: str): ...

    def merge_feature_blocks(self, *blocks): ...
```

### `price_features.py`

```python
class PriceFeatureCalculator:
    def compute(self, bars_df): ...
```

### `liquidity_features.py`

```python
class LiquidityFeatureCalculator:
    def compute(self, bars_df): ...
```

### `relative_strength_features.py`

```python
class RelativeStrengthFeatureCalculator:
    def compute(self, stock_df, index_df): ...
```

## 10. 市场扫描接口

### `src/strategy/market_scan/market_scan_service.py`

```python
class MarketScanService:
    def __init__(self, repo: ResearchRepository) -> None: ...

    def run(self, trade_date: str) -> MarketRegimeRow: ...

    def load_inputs(self, trade_date: str): ...
```

### `breadth_metrics.py`

```python
class BreadthMetricsCalculator:
    def compute(self, stock_df) -> dict[str, float | int | str]: ...
```

### `regime_detector.py`

```python
class RegimeDetector:
    def detect(self, metrics: dict[str, float | int | str]) -> dict[str, str]: ...
```

## 11. 选股模型接口

### `src/strategy/stock_selection/labels.py`

```python
class LabelBuilder:
    def build(self, bars_df, horizons: list[int]): ...
```

### `baseline_ranker.py`

```python
class BaselineRanker:
    def __init__(self, factor_weights: dict[str, float]) -> None: ...

    def score(self, features_df, trade_date: str, horizon: int): ...
```

### `ml_ranker.py`

```python
class MLRanker:
    def __init__(self, model_name: str) -> None: ...

    def fit(self, train_df, label_col: str) -> None: ...

    def predict(self, score_df): ...

    def save(self, path: str) -> None: ...

    def load(self, path: str) -> None: ...
```

### `selection_service.py`

```python
class SelectionService:
    def __init__(
        self,
        repo: ResearchRepository,
        baseline_ranker: BaselineRanker,
        ml_ranker: MLRanker | None = None,
    ) -> None: ...

    def run(self, trade_date: str, horizons: list[int]) -> int: ...

    def build_scores(self, trade_date: str, horizon: int): ...
```

## 12. 规则引擎接口

### `src/strategy/rules/rule_engine.py`

```python
class RuleEngine:
    def __init__(self, settings: StrategySettings, repo: ResearchRepository) -> None: ...

    def run(self, trade_date: str, horizon: int) -> int: ...

    def apply_constraints(self, score_df, market_regime_df): ...

    def build_signals(self, ranked_df): ...
```

### `limit_rules.py`

```python
class LimitRuleChecker:
    def annotate(self, df): ...
```

### `portfolio_rules.py`

```python
class PortfolioRuleSet:
    def __init__(self, top_n: int) -> None: ...

    def apply(self, df): ...
```

## 13. 最小验证器接口

### `src/strategy/validation/execution_model.py`

```python
class ExecutionModel:
    def __init__(self, execution_price_mode: str, cost_bps: float) -> None: ...

    def get_entry_price(self, signal_date: str, symbol: str, bars_df) -> float: ...

    def get_exit_price(self, exit_date: str, symbol: str, bars_df) -> float: ...
```

### `validation_engine.py`

```python
class ValidationEngine:
    def __init__(
        self,
        repo: ResearchRepository,
        execution_model: ExecutionModel,
        settings: ValidationSettings,
    ) -> None: ...

    def run(self, start_date: str, end_date: str, horizon: int): ...

    def simulate_day(self, signal_date: str, horizon: int): ...
```

### `validation_metrics.py`

```python
class ValidationMetricsCalculator:
    def summarize(self, trades_df, equity_df): ...
```

### `validation_reporter.py`

```python
class ValidationReporter:
    def build_report(self, metrics: dict, horizon: int) -> dict: ...
```

## 14. AI Agent 接口

### `src/ai/agents/market_summary_agent.py`

```python
class MarketSummaryAgent:
    def __init__(self, client: StructuredLLMClient, prompt_loader) -> None: ...

    def run(self, payload: dict) -> dict: ...
```

### `src/ai/agents/stock_explainer_agent.py`

```python
class StockExplainerAgent:
    def __init__(self, client: StructuredLLMClient, prompt_loader) -> None: ...

    def run(self, payload: dict) -> dict: ...
```

### `src/ai/agents/risk_reviewer_agent.py`

```python
class RiskReviewerAgent:
    def __init__(self, client: StructuredLLMClient, prompt_loader) -> None: ...

    def run(self, payload: dict) -> dict: ...
```

### `src/ai/agents/report_writer_agent.py`

```python
class ReportWriterAgent:
    def __init__(self, client: StructuredLLMClient, prompt_loader) -> None: ...

    def run(self, payload: dict) -> dict: ...
```

## 15. AI 编排接口

### `src/ai/services/ai_orchestrator.py`

```python
class AIOrchestrator:
    def __init__(
        self,
        settings: AISettings,
        market_agent: MarketSummaryAgent,
        stock_agent: StockExplainerAgent,
        risk_agent: RiskReviewerAgent,
        report_agent: ReportWriterAgent | None,
        result_store,
    ) -> None: ...

    def run_daily(
        self,
        trade_date: str,
        market_payload: dict,
        stock_payloads: list[dict],
    ) -> dict: ...

    def run_market_summary(self, payload: dict) -> dict: ...

    def run_stock_explanations(self, payloads: list[dict]) -> list[dict]: ...

    def run_risk_reviews(self, payloads: list[dict]) -> list[dict]: ...
```

### `src/ai/services/ai_result_store.py`

```python
class AIResultStore:
    def __init__(self, repo: LogRepository) -> None: ...

    def save_call_log(self, record: dict) -> None: ...

    def save_agent_output(self, trade_date: str, agent_name: str, payload: dict) -> None: ...
```

## 16. 交易秘书接口

### `src/strategy/secretary/report_context_builder.py`

```python
class ReportContextBuilder:
    def build(
        self,
        trade_date: str,
        market_regime: dict,
        signals_df,
        ai_outputs: dict | None,
    ) -> dict: ...
```

### `src/strategy/secretary/secretary_service.py`

```python
class SecretaryService:
    def __init__(self, repo: ResearchRepository, markdown_writer) -> None: ...

    def run(self, trade_date: str, context: dict) -> dict: ...

    def build_markdown(self, context: dict) -> str: ...

    def build_json(self, context: dict) -> dict: ...
```

### `src/strategy/secretary/markdown_writer.py`

```python
class MarkdownWriter:
    def write(self, path: str, content: str) -> None: ...
```

## 17. 工作流接口

### `app/daily_workflow.py`

```python
class DailyWorkflow:
    def __init__(
        self,
        market_data_service,
        stock_pool_builder,
        feature_pipeline,
        market_scan_service,
        selection_service,
        rule_engine,
        ai_orchestrator,
        secretary_service,
        run_logger,
    ) -> None: ...

    def run(self, trade_date: str) -> dict: ...
```

### `app/validate_workflow.py`

```python
class ValidateWorkflow:
    def __init__(self, validation_engine, validation_reporter, run_logger) -> None: ...

    def run(self, start_date: str, end_date: str, horizon: int) -> dict: ...
```

## 18. 日志接口

### `src/infra/logging/run_logger.py`

```python
class RunLogger:
    def start_run(self, run_type: str, config_hash: str) -> str: ...

    def finish_run(self, run_id: str, status: str, summary: dict | None = None) -> None: ...

    def log_event(
        self,
        run_id: str,
        module: str,
        level: str,
        message: str,
        payload: dict | None = None,
    ) -> None: ...
```

## 19. 第一阶段关键返回值约定

建议：

- 所有 `run()` 方法返回结构化 dict 或 DataFrame
- 不直接返回自然语言文本作为上游依赖
- 所有模块错误必须抛业务异常，不要静默吞掉

推荐统一返回风格：

```python
{
    "status": "SUCCESS",
    "trade_date": "2026-03-29",
    "rows": 3210,
    "extra": {}
}
```

## 20. 第一阶段最小实现优先顺序

真正开始写代码时，建议按这个顺序实现：

1. `Settings + DuckDBClient + Repository`
2. `AKShareProvider + Collectors`
3. `StockPoolBuilder`
4. `FeaturePipeline`
5. `MarketScanService`
6. `BaselineRanker + SelectionService`
7. `RuleEngine`
8. `ValidationEngine`
9. `SecretaryService`
10. `OpenAIClient + AIOrchestrator`
11. `DailyWorkflow`

## 21. 最终建议

接口设计阶段不要追求“面面俱到”，只要保证一件事：

> 每层只依赖下层稳定输出，不跨层偷拿数据。

这样后面无论你接：

- 飞书同步器
- 可视化界面
- OpenClaw
- 正式回测引擎

都不会把第一阶段推翻重做。
