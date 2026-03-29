# A Share Mainboard

沪深主板 A 股 5日/10日短周期研究系统。

当前版本是第一阶段工程骨架，目标是先把以下链路搭起来：

- 配置加载
- DuckDB 存储
- 工作流入口
- 运行日志
- OpenAI API 客户端封装

后续会在此基础上继续补：

- 行情采集
- 股票池过滤
- 特征计算
- 市场扫描
- 选股模型
- 规则引擎
- 最小验证器
- 交易秘书

## 快速开始

1. 创建虚拟环境并安装依赖。
2. 复制 `.env.example` 为 `.env`，填入 `OPENAI_API_KEY`。
3. 初始化工作区：

```powershell
python scripts/init_workspace.py
```

4. 运行空的每日流程：

```powershell
python scripts/run_daily.py --trade-date 2026-03-29
```

## 当前限制

- 数据供应商适配器已建立，但行情采集还未接通实盘数据
- 工作流当前只跑通骨架与日志，不产出真实选股结果
- OpenAI 客户端可调用 Responses API，但 AI Agent 业务层尚未接入

