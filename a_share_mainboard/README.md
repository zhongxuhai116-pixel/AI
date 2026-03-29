# A Share Mainboard

沪深主板 A 股 5日/10日短周期研究系统。

当前版本已经跑通第一阶段主链路：

- 行情采集
- 股票池过滤
- 特征计算
- 市场扫描
- 基线选股打分
- 规则筛选
- AI 解释层接入
- 飞书推送接入
- 最小验证器

后续会在此基础上继续补：

- 风控引擎
- 事件数据采集
- 可视化界面
- 参数评估
- 模型管理

## 快速开始

1. 创建虚拟环境并安装依赖。
2. 复制 `.env.example` 为 `.env`。
3. 按需填入这些环境变量：

- `OPENAI_API_KEY`
- `FEISHU_BOT_WEBHOOK`
- `FEISHU_BOT_SECRET`

4. 初始化工作区：

```powershell
python scripts/init_workspace.py
```

5. 回补历史行情：

```powershell
python scripts/run_rebuild.py --start-date 2026-02-01 --end-date 2026-03-29
```

6. 运行每日流程：

```powershell
python scripts/run_daily.py --trade-date 2026-03-29
```

7. 单独运行验证：

```powershell
python scripts/run_validate.py --start-date 2026-02-01 --end-date 2026-03-27 --horizon 5
```

## 当前限制

- 当前只覆盖沪深主板，不含创业板、科创板和北交所
- AI 与飞书都已接入主流程，但默认仍是“无密钥可降级”模式
- 最小验证器当前按 `T日收盘出信号 -> T+1 开盘买入 -> 持有 N 日后按收盘卖出` 计算
- 回测/验证仍是第一阶段版本，尚未加入更细的涨跌停成交约束与组合风控
