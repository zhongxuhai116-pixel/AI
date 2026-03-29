# 第一阶段项目目录与文件级设计

## 1. 文档目标

本文件将 `沪深主板 A 股 5日/10日短周期研究系统` 的第一阶段拆到文件级，目标是让后续开发时：

- 目录边界清楚
- 模块职责稳定
- AI 层不污染主链路
- 可以直接按文件开始搭骨架

第一阶段范围只覆盖：

- 行情采集
- 股票池过滤
- 特征计算
- 市场扫描模块
- 选股模型
- 规则引擎
- 交易秘书
- 日志中心
- 最小验证器
- OpenAI API 解释层

## 2. 第一阶段总目录

```text
a_share_mainboard/
├─ app/
│  ├─ launcher.py
│  ├─ daily_workflow.py
│  ├─ validate_workflow.py
│  └─ rebuild_workflow.py
├─ config/
│  ├─ app.toml
│  ├─ data.toml
│  ├─ universe.toml
│  ├─ strategy.toml
│  ├─ validation.toml
│  └─ ai.toml
├─ docs/
├─ scripts/
│  ├─ init_workspace.py
│  ├─ run_daily.py
│  ├─ run_validate.py
│  └─ export_report.py
├─ src/
│  ├─ adapters/
│  │  ├─ market/
│  │  │  ├─ base.py
│  │  │  ├─ akshare_provider.py
│  │  │  └─ tushare_provider.py
│  │  └─ llm/
│  │     ├─ base.py
│  │     └─ openai_client.py
│  ├─ domain/
│  │  ├─ enums.py
│  │  ├─ types.py
│  │  ├─ models.py
│  │  └─ schemas.py
│  ├─ data/
│  │  ├─ collectors/
│  │  │  ├─ trade_calendar_collector.py
│  │  │  ├─ instrument_collector.py
│  │  │  ├─ price_daily_collector.py
│  │  │  └─ index_daily_collector.py
│  │  ├─ filters/
│  │  │  ├─ listing_filter.py
│  │  │  ├─ st_filter.py
│  │  │  ├─ suspension_filter.py
│  │  │  ├─ liquidity_filter.py
│  │  │  └─ stock_pool_builder.py
│  │  ├─ features/
│  │  │  ├─ price_features.py
│  │  │  ├─ liquidity_features.py
│  │  │  ├─ relative_strength_features.py
│  │  │  └─ feature_pipeline.py
│  │  ├─ storage/
│  │  │  ├─ duckdb_client.py
│  │  │  ├─ parquet_store.py
│  │  │  ├─ repositories.py
│  │  │  └─ table_bootstrap.py
│  │  └─ services/
│  │     ├─ market_data_service.py
│  │     └─ dataset_service.py
│  ├─ strategy/
│  │  ├─ market_scan/
│  │  │  ├─ breadth_metrics.py
│  │  │  ├─ regime_detector.py
│  │  │  ├─ market_scan_service.py
│  │  │  └─ market_scan_formatter.py
│  │  ├─ stock_selection/
│  │  │  ├─ baseline_ranker.py
│  │  │  ├─ ml_ranker.py
│  │  │  ├─ labels.py
│  │  │  └─ selection_service.py
│  │  ├─ rules/
│  │  │  ├─ constraints.py
│  │  │  ├─ limit_rules.py
│  │  │  ├─ portfolio_rules.py
│  │  │  └─ rule_engine.py
│  │  ├─ validation/
│  │  │  ├─ execution_model.py
│  │  │  ├─ validation_engine.py
│  │  │  ├─ validation_metrics.py
│  │  │  └─ validation_reporter.py
│  │  └─ secretary/
│  │     ├─ report_context_builder.py
│  │     ├─ report_templates.py
│  │     ├─ secretary_service.py
│  │     └─ markdown_writer.py
│  ├─ ai/
│  │  ├─ prompts/
│  │  │  ├─ market_summary_v1.md
│  │  │  ├─ stock_explainer_v1.md
│  │  │  ├─ risk_reviewer_v1.md
│  │  │  └─ report_writer_v1.md
│  │  ├─ agents/
│  │  │  ├─ market_summary_agent.py
│  │  │  ├─ stock_explainer_agent.py
│  │  │  ├─ risk_reviewer_agent.py
│  │  │  └─ report_writer_agent.py
│  │  ├─ guards/
│  │  │  ├─ json_guard.py
│  │  │  ├─ timeout_guard.py
│  │  │  └─ fallback_guard.py
│  │  └─ services/
│  │     ├─ ai_orchestrator.py
│  │     └─ ai_result_store.py
│  └─ infra/
│     ├─ config/
│     │  ├─ loader.py
│     │  └─ settings.py
│     ├─ logging/
│     │  ├─ logger.py
│     │  ├─ run_logger.py
│     │  └─ event_logger.py
│     ├─ utils/
│     │  ├─ dates.py
│     │  ├─ hashing.py
│     │  ├─ ids.py
│     │  └─ io.py
│     └─ exceptions.py
├─ tests/
│  ├─ unit/
│  ├─ integration/
│  └─ fixtures/
├─ data/
│  ├─ raw/
│  ├─ ods/
│  ├─ mart/
│  ├─ reports/
│  └─ logs/
├─ pyproject.toml
├─ README.md
└─ .env.example
```

## 3. 根目录文件职责

### `pyproject.toml`

职责：

- Python 项目依赖管理
- 统一入口命令
- 测试、格式化、静态检查配置

建议最小依赖：

- `pandas`
- `numpy`
- `duckdb`
- `pyarrow`
- `pydantic`
- `lightgbm`
- `scikit-learn`
- `akshare`
- `httpx`
- `tenacity`
- `python-dotenv`

### `README.md`

职责：

- 项目概述
- 安装方式
- 配置说明
- 一键运行入口
- 当前限制

### `.env.example`

职责：

- OpenAI API Key
- 可选数据源 Token
- 日志级别

## 4. `app/` 目录

### `app/launcher.py`

职责：

- 图形界面之前的统一入口
- 根据参数决定执行 `daily / validate / rebuild`

### `app/daily_workflow.py`

职责：

- 第一阶段主流程编排
- 依次执行数据更新、股票池、特征、扫描、选股、规则、交易秘书

### `app/validate_workflow.py`

职责：

- 调用最小验证器
- 生成历史验证摘要

### `app/rebuild_workflow.py`

职责：

- 全量重建特征或重跑历史评分

## 5. `config/` 目录

### `config/app.toml`

包含：

- 项目路径
- 日志目录
- 报告目录
- 默认工作模式

### `config/data.toml`

包含：

- 默认数据源
- 增量更新窗口
- Parquet 分区规则
- DuckDB 文件位置

### `config/universe.toml`

包含：

- 市场限定为 `沪深主板`
- 上市天数阈值
- 流动性阈值
- ST 过滤开关

### `config/strategy.toml`

包含：

- 候选股数量
- 持有期 `5/10`
- baseline 因子权重
- 模型开关

### `config/validation.toml`

包含：

- 成本参数
- 执行价格口径
- 持仓数量

### `config/ai.toml`

包含：

- 使用的 OpenAI 模型
- 超时
- 重试次数
- 是否开启候选股解释
- 每日最多解释股票数

## 6. `scripts/` 目录

### `scripts/init_workspace.py`

职责：

- 初始化数据目录
- 建立 DuckDB 表
- 生成默认配置副本

### `scripts/run_daily.py`

职责：

- 命令行运行每日主流程

### `scripts/run_validate.py`

职责：

- 命令行运行最小验证器

### `scripts/export_report.py`

职责：

- 导出 Markdown 或 JSON 报告

## 7. `src/adapters/market/`

### `base.py`

职责：

- 定义数据供应商协议

### `akshare_provider.py`

职责：

- 第一阶段默认实现
- 提供交易日历、主板股票基础信息、日线行情、指数行情

### `tushare_provider.py`

职责：

- 第二优先级实现
- 先只保留适配骨架，不要求第一阶段完全实现

## 8. `src/adapters/llm/`

### `base.py`

职责：

- 定义统一 LLM 客户端接口

### `openai_client.py`

职责：

- 统一封装 Responses API
- Structured Outputs
- 超时、重试、日志写入

## 9. `src/domain/`

### `enums.py`

放：

- `BoardType`
- `HorizonType`
- `SignalAction`
- `RejectReason`
- `RunStatus`

### `types.py`

放：

- 关键类型别名
- `Symbol`
- `TradeDate`
- `RunId`

### `models.py`

放：

- 领域 dataclass
- `Instrument`
- `DailyBar`
- `FeatureRow`
- `SignalRow`

### `schemas.py`

放：

- Pydantic Schema
- AI 输出 Schema
- 配置加载 Schema

## 10. `src/data/collectors/`

### `trade_calendar_collector.py`

职责：

- 同步交易日历

### `instrument_collector.py`

职责：

- 获取股票基础信息
- 只保留沪深主板字段

### `price_daily_collector.py`

职责：

- 获取日线行情
- 统一字段和复权处理

### `index_daily_collector.py`

职责：

- 获取基准指数行情

## 11. `src/data/filters/`

### `listing_filter.py`

职责：

- 上市时间过滤

### `st_filter.py`

职责：

- ST 和 *ST 过滤

### `suspension_filter.py`

职责：

- 停牌与缺失过滤

### `liquidity_filter.py`

职责：

- 成交额、成交量、换手阈值过滤

### `stock_pool_builder.py`

职责：

- 汇总全部过滤条件
- 生成 `stock_pool_daily`

## 12. `src/data/features/`

### `price_features.py`

负责：

- 收益率
- 均线偏离
- 波动率
- 回撤

### `liquidity_features.py`

负责：

- 成交额变化
- 换手
- 量比类指标

### `relative_strength_features.py`

负责：

- 相对指数强弱
- 相对行业强弱

### `feature_pipeline.py`

负责：

- 聚合所有特征
- 输出 `features_daily`

## 13. `src/data/storage/`

### `duckdb_client.py`

职责：

- DuckDB 连接管理

### `parquet_store.py`

职责：

- 原始和中间结果文件落地

### `repositories.py`

职责：

- 按表封装读写

### `table_bootstrap.py`

职责：

- 初始化 DuckDB 表结构

## 14. `src/data/services/`

### `market_data_service.py`

职责：

- 驱动采集器执行增量更新

### `dataset_service.py`

职责：

- 读取训练、评分、验证所需数据切片

## 15. `src/strategy/market_scan/`

### `breadth_metrics.py`

负责：

- 上涨家数占比
- 涨跌停统计
- 市场宽度

### `regime_detector.py`

负责：

- 市场状态标签
- 趋势/震荡/弱修复分类

### `market_scan_service.py`

负责：

- 扫描入口
- 写入 `market_regime_daily`

### `market_scan_formatter.py`

负责：

- 将结构化扫描结果转换为交易秘书输入

## 16. `src/strategy/stock_selection/`

### `baseline_ranker.py`

职责：

- 第一阶段默认选股器
- 因子加权打分

### `ml_ranker.py`

职责：

- 预留 LightGBM 排序器
- 第一阶段可以只保留骨架和训练入口

### `labels.py`

职责：

- 构造未来 `5日` 和 `10日` 标签

### `selection_service.py`

职责：

- 汇总 ranker 输出
- 生成 `model_scores_daily`

## 17. `src/strategy/rules/`

### `constraints.py`

负责：

- 通用约束定义

### `limit_rules.py`

负责：

- 涨停难买
- 跌停难卖
- T+1 执行规则

### `portfolio_rules.py`

负责：

- 候选数量
- 单票权重
- 持有期

### `rule_engine.py`

负责：

- 把模型分数转成最终信号

## 18. `src/strategy/validation/`

### `execution_model.py`

负责：

- 设定次日执行价格口径

### `validation_engine.py`

负责：

- 最小验证闭环

### `validation_metrics.py`

负责：

- 收益
- 回撤
- 换手
- 胜率

### `validation_reporter.py`

负责：

- 产出验证摘要

## 19. `src/strategy/secretary/`

### `report_context_builder.py`

负责：

- 汇总市场扫描、候选股、AI 结果

### `report_templates.py`

负责：

- Markdown 模板
- JSON 模板

### `secretary_service.py`

负责：

- 交易秘书总入口

### `markdown_writer.py`

负责：

- 输出报告文件

## 20. `src/ai/prompts/`

只放 Prompt，不放代码。

建议第一阶段只有 4 个 prompt：

- `market_summary_v1.md`
- `stock_explainer_v1.md`
- `risk_reviewer_v1.md`
- `report_writer_v1.md`

## 21. `src/ai/agents/`

### `market_summary_agent.py`

职责：

- 对市场结构化扫描结果做总结

### `stock_explainer_agent.py`

职责：

- 对候选股做入选解释

### `risk_reviewer_agent.py`

职责：

- 对候选股做风险补充标签

### `report_writer_agent.py`

职责：

- 可选，把结构化结果润色为更自然的日报文本

## 22. `src/ai/guards/`

### `json_guard.py`

职责：

- 校验 AI 输出 JSON 是否合规

### `timeout_guard.py`

职责：

- 控制调用超时

### `fallback_guard.py`

职责：

- AI 调用失败时回落到模板输出

## 23. `src/ai/services/`

### `ai_orchestrator.py`

职责：

- 调度多个 Agent
- 控制顺序与最大调用数量

### `ai_result_store.py`

职责：

- 存储 AI 结果和请求日志

## 24. `src/infra/config/`

### `loader.py`

职责：

- 加载 TOML 配置

### `settings.py`

职责：

- 统一配置对象

## 25. `src/infra/logging/`

### `logger.py`

职责：

- 应用级 logger

### `run_logger.py`

职责：

- 记录 run_id 级别事件

### `event_logger.py`

职责：

- 记录模块事件、耗时、异常

## 26. `src/infra/utils/`

### `dates.py`

职责：

- 交易日偏移
- 日期窗口计算

### `hashing.py`

职责：

- 生成 prompt hash、config hash、input hash

### `ids.py`

职责：

- 生成 `run_id`、`call_id`

### `io.py`

职责：

- 文件读写辅助

## 27. `tests/`

第一阶段必须至少覆盖：

- 股票池过滤逻辑
- 特征计算正确性
- 规则引擎输出稳定性
- AI JSON 输出校验
- 最小验证器执行逻辑

## 28. 第一阶段真正需要优先创建的文件

不是所有文件都要一次写完。建议创建顺序如下：

### 第一批

- `pyproject.toml`
- `README.md`
- `config/*.toml`
- `src/domain/*`
- `src/infra/config/*`
- `src/infra/logging/*`
- `src/data/storage/*`

### 第二批

- `src/adapters/market/base.py`
- `src/adapters/market/akshare_provider.py`
- `src/data/collectors/*`
- `src/data/filters/*`
- `src/data/features/*`

### 第三批

- `src/strategy/market_scan/*`
- `src/strategy/stock_selection/*`
- `src/strategy/rules/*`
- `src/strategy/validation/*`

### 第四批

- `src/adapters/llm/openai_client.py`
- `src/ai/agents/*`
- `src/strategy/secretary/*`
- `app/*.py`
- `scripts/*.py`

## 29. 第一阶段目录设计原则

- 所有数据源差异只允许存在于 `adapters/market/`
- 所有 AI 调用只允许存在于 `adapters/llm/` 和 `src/ai/`
- 所有买卖信号生成只允许存在于 `strategy/stock_selection/` 与 `strategy/rules/`
- 所有外部展示只允许通过 `strategy/secretary/`
- 所有日志必须带 `run_id`

## 30. 最终落地建议

第一阶段不要急着铺太多文件内容，但目录必须先定型。  
后续开发应严格遵守：

- 数据层先跑通
- 再做规则和验证
- 最后加 AI 解释

一句话执行顺序：

> 先把“算得对”做出来，再把“说得明白”接进去。

