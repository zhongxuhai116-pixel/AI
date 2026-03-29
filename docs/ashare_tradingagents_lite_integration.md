# A股版 TradingAgents Lite 接入方案

## 1. 方案定位

本方案用于在 `沪深主板 A 股 5日/10日短周期研究系统` 中引入一套轻量级多 Agent 协作层。

设计目标不是复制原版 `TradingAgents`，而是基于 A 股实际交易制度和本项目核心链路，做一个：

- 可控
- 可审计
- 可降级
- 与主策略解耦

的 `TradingAgents Lite`。

## 2. 为什么不能直接照搬原版 TradingAgents

原版 `TradingAgents` 更适合做：

- 单票研究
- 多 Agent 辩论式输出
- 美股或海外市场导向的研究工作流

但当前项目需要的是：

- 主板全市场日频扫描
- 候选股横截面排序
- 严格遵守 A 股 `T+1`、涨跌停、ST、停牌等约束
- 支持普通用户一键运行

因此需要保留其“分角色讨论”的思想，但删掉其不适合 A 股短周期主链路的部分。

## 3. 总体原则

### 3.1 主链路不变

TradingAgents Lite 不得替代核心策略链路：

`数据 -> 股票池 -> 特征 -> 选股模型 -> 规则引擎 -> 最小验证器 -> 输出`

### 3.2 AI 只做增强

TradingAgents Lite 的职责：

- 补充市场文字摘要
- 解释候选股入选原因
- 产出风险标签
- 生成交易秘书日报
- 为后续复盘生成结构化记录

TradingAgents Lite 不得直接：

- 修改模型分数
- 绕过规则引擎
- 生成最终买卖执行指令
- 替代回测或验证模块

### 3.3 必须支持无 AI 降级

当 OpenAI API 不可用、超时、限流或返回异常时：

- 主策略继续运行
- 仍可输出结构化结果
- 交易秘书输出无 AI 的简版日报
- 记录失败日志并允许后补生成解释

## 4. A股版 Lite 架构

```text
TradingAgents Lite
├─ Orchestrator
├─ Context Builder
├─ Agent Registry
│  ├─ 市场结构Agent
│  ├─ 行业轮动Agent
│  ├─ 技术节奏Agent
│  ├─ 公告新闻Agent
│  ├─ 风险审查Agent
│  └─ 交易秘书Agent
├─ Output Guards
├─ Prompt Registry
└─ Result Store
```

## 5. 与主系统的集成位置

### 5.1 输入位置

TradingAgents Lite 只读取这些已产出的结构化数据：

- `market_regime_daily`
- `stock_pool_daily`
- `features_daily`
- `model_scores_daily`
- `signals_daily`
- `validation_summary`（后续）

### 5.2 输出位置

TradingAgents Lite 只写入这些附加结果表或文件：

- `ai_calls`
- `agent_outputs`
- `daily_reports`
- `risk_annotations`
- `review_notes`

### 5.3 运行时机

建议顺序：

1. 主链路完成候选股生成
2. 对前 `N` 只候选股构建上下文
3. 运行 TradingAgents Lite
4. 输出解释、风险标签、日报
5. 由交易秘书整合最终展示内容

## 6. 第一阶段建议保留的 Agent

第一阶段只保留 4 个最小角色。

### 6.1 市场结构 Agent

职责：

- 解读市场扫描模块的结构化输出
- 总结当前市场更偏向趋势、震荡还是弱修复
- 生成面向普通用户可读的市场摘要

输入：

- 指数 5 日/10 日涨跌
- 市场宽度
- 涨跌停家数
- 成交额冷热
- 风格强弱

输出：

- `market_summary`
- `market_style_label`
- `market_risk_notes`

### 6.2 技术节奏 Agent

职责：

- 对候选股的短周期量价节奏做解释
- 说明入选原因是否来自趋势延续、回调企稳或量价共振

输入：

- 候选股基础特征
- 相对指数强弱
- 均线偏离
- 波动、换手、量能变化

输出：

- `technical_summary`
- `pattern_tags`
- `execution_notes`

### 6.3 公告新闻 Agent

职责：

- 汇总短期事件因素
- 标记可能影响未来 5 到 10 日节奏的公告、新闻、异动因素

输入：

- 后续的公告新闻数据
- 当前可先接预留接口，阶段 1 可返回空结果或简化结果

输出：

- `event_summary`
- `event_tags`
- `event_risk_level`

### 6.4 风险审查 Agent

职责：

- 基于候选股已有结构化数据做风险补充说明
- 标记高波动、成交脆弱、追高、板块拥挤等风险

输入：

- 候选股特征
- 规则引擎标签
- 市场状态标签

输出：

- `risk_summary`
- `risk_tags`
- `watch_points`

## 7. 第二阶段再补的 Agent

### 7.1 行业轮动 Agent

用于解释行业相对强弱和板块拥挤度。

### 7.2 复盘 Agent

用于在 T+5 或 T+10 后复盘当日候选股：

- 为什么对
- 为什么错
- 是否受市场环境影响

### 7.3 任务协同 Agent

与飞书同步器或 OpenClaw 结合，用于：

- 生成研究任务
- 派发复盘任务
- 跟踪待补数据信号

## 8. 第一阶段建议删除的原版 TradingAgents 思路

这些内容不建议直接进入 A 股 Lite 第一阶段：

- Bull / Bear 辩论式多轮长对话
- Portfolio Manager 再次决定买卖
- 直接输出 `BUY / SELL / HOLD`
- 让 Agent 主导最终仓位判断
- 以 `单 ticker 深研` 取代 `全市场先筛后解释`

原因是这些结构会：

- 增加 Token 成本
- 降低结果稳定性
- 强化幻觉风险
- 让回测与审计变得困难

## 9. Agent 输入输出规范

所有 Agent 统一要求：

- 输入用结构化 JSON
- 输出也用 JSON Schema
- 不接受自由散文作为机器输出
- 交易秘书可以把 JSON 二次转成自然语言

### 9.1 市场结构 Agent 输入示例

```json
{
  "trade_date": "2026-03-29",
  "market_regime": {
    "index_ret_5d": {
      "sh000001": 0.021,
      "sz399001": 0.018
    },
    "breadth_up_ratio": 0.57,
    "limit_up_count": 42,
    "limit_down_count": 6,
    "volume_heat": "neutral",
    "style_label": "large_cap_relative_strong"
  }
}
```

### 9.2 市场结构 Agent 输出示例

```json
{
  "market_style_label": "mild_trend_up",
  "market_summary": "主板市场维持温和偏强状态，但情绪并未极端扩散。",
  "market_risk_notes": [
    "热点集中度一般，追高性价比有限",
    "若次日量能不放大，强势延续概率会下降"
  ]
}
```

### 9.3 候选股解释 Agent 输入示例

```json
{
  "trade_date": "2026-03-29",
  "symbol": "600000.SH",
  "horizon": 5,
  "score_rank": 7,
  "rule_tags": ["main_board", "liquidity_ok"],
  "features": {
    "ret_5d": 0.064,
    "ret_10d": 0.091,
    "ma_gap_5": 0.023,
    "volatility_10d": 0.018,
    "turnover_5d": 0.021,
    "rs_index_10d": 0.052
  },
  "market_context": {
    "style_label": "mild_trend_up"
  }
}
```

### 9.4 候选股解释 Agent 输出示例

```json
{
  "technical_summary": "个股在5日和10日维度保持相对强势，均线结构完整，波动尚未失控。",
  "pattern_tags": ["trend_continuation", "relative_strength"],
  "execution_notes": [
    "更适合分批跟踪，不适合大幅追价",
    "若次日低开失守短均线，应下调优先级"
  ],
  "confidence_note": "medium"
}
```

### 9.5 风险审查 Agent 输出示例

```json
{
  "risk_summary": "短期趋势较强，但存在连续上涨后的追高风险。",
  "risk_tags": ["chasing_risk", "crowded_trade"],
  "watch_points": [
    "次日是否高开过多",
    "成交额是否明显萎缩"
  ]
}
```

## 10. 交易秘书整合输出

交易秘书不是最终决策 Agent，而是输出整理器。

它的输入来自：

- 市场扫描模块
- 最终候选股
- 各 Agent 结构化结果

它的输出内容包括：

- 今日市场一句话摘要
- 今日候选股列表
- 每只股票的入选原因
- 每只股票的风险标签
- 次日观察点

建议输出双格式：

- `daily_report.json`
- `daily_report.md`

## 11. Prompt 与版本管理

每个 Agent 都必须有明确的 Prompt 版本号。

建议目录：

```text
src/ai/prompts/
├─ market_summary_v1.md
├─ stock_explainer_v1.md
├─ risk_reviewer_v1.md
└─ report_writer_v1.md
```

每次 AI 调用记录：

- `run_id`
- `task_type`
- `prompt_version`
- `model`
- `reasoning_profile`
- `input_hash`
- `token_usage`
- `request_id`
- `status`

## 12. OpenAI API 接入建议

### 12.1 统一封装

只保留一个客户端出口：

```text
src/adapters/llm/openai_client.py
```

不允许业务模块直接散落调用 SDK。

### 12.2 统一能力

客户端统一提供：

- `generate_structured_output()`
- `retry_with_backoff()`
- `safe_timeout_call()`
- `record_ai_call()`

### 12.3 调用约束

- 温度保持低值
- 指定 JSON Schema
- 单次输入严格限长
- 候选股解释只对前 `N` 只股票执行
- 超时后直接降级

## 13. 最小运行流程

第一阶段建议这样运行：

1. 主链路完成候选股生成
2. 选取前 `10` 或 `20` 只候选股
3. 调用市场结构 Agent 生成市场摘要
4. 对每只候选股调用技术节奏 Agent
5. 对每只候选股调用风险审查 Agent
6. 交易秘书整合日报
7. 写入 `daily_reports` 与 `agent_outputs`

## 14. 阶段拆分

### 阶段 1

- 市场结构 Agent
- 技术节奏 Agent
- 风险审查 Agent
- 交易秘书整合器

### 阶段 2

- 行业轮动 Agent
- 公告新闻 Agent
- 飞书同步器对接
- 可视化展示

### 阶段 3

- 复盘 Agent
- Prompt A/B 比较
- Agent 结果效果评估
- OpenClaw 或其他多 Agent 运行时对接

## 15. 成功标准

TradingAgents Lite 接入成功，不以“写了多少 Agent”来衡量，而以以下标准衡量：

- 不影响主链路稳定运行
- AI 挂掉时系统仍能产出结果
- 候选股解释能提高用户可理解性
- 风险标签能为复盘提供价值
- Token 成本可控
- 输出能被日志与验证模块追踪

## 16. 最终建议

本项目不应把 TradingAgents 作为内核，而应把它改造成：

- `先筛后解释`
- `先规则后文本`
- `先结构化后自然语言`

的 `A股版 TradingAgents Lite`。

一句话定义：

> 主系统负责算出“该看谁”，TradingAgents Lite 负责解释“为什么看它、风险在哪里、明天怎么看”。
