# V6-09 真实总控台与负载/故障恢复实测

- 日期：2026-09-13（云端会话）
- 范围：主规划 12.9/12.14 的运营面——真实总控台（服务端按数据库实时聚合）+ 100 项批次 / 2 Worker / 10 项故障恢复实测（实测耗时，不用生成值）。
- 状态：**完成**（负载实测 100/100 成功、10/10 恢复；后端 582/582；4 个真实缺陷已修并有回归测试）；V6 整体仍为 IN_PROGRESS。

## 1. 交付物

### 1.1 `GET /api/v1/console/overview`

单次请求返回：作业状态分布与窗口吞吐、`success_rate`（SUCCEEDED + VERIFICATION_PASSED 记为成功）、Run 状态、队列深度（含 `oldest_queued_at` 与 **`jobs_without_queue_row`**：无法被 Worker 领取的作业数）、批次/批次项聚合、失败原因 Top5、账本四类金额与未定价用量、事件 Outbox 积压与最近事件投递情况、Webhook 投递计数与**投递延迟 p50/p95**（同一个数据库时钟计算）、Automation Key 计数、`measured_at` 与口径说明。

所有数值都是 SQL 聚合（`jobs/runs/run_jobs/batches/batch_items/cost_ledger/usage_events/outbox_events/delivery_attempts`），控制台页面不自行推算。

### 1.2 控制台页面「总控台」

`apps/web/src/App.jsx` 新增 `ConsolePage`（导航第一项，`Gauge` 图标）：六个指标卡（队列中/成功率/批次项/未投递事件/投递延迟 p50·p95/成本）、作业与失败原因表、批次与事件健康、最近事件表、口径说明；**5 秒自动刷新**可开关，窗口可切 15 分钟–24 小时；`jobs_without_queue_row > 0` 时显式告警。

### 1.3 负载与故障恢复测量脚本 `scripts`（本次以运维脚本形式在云端执行）

`v609_load.py`：真实 `POST /batches`（100 项、并发上限 2）→ 启动 **2 个真实 Worker 进程**（`python -m productdirector_api.main worker`）→ 每 2 秒投递 tick、每 3 秒调度 tick（等价于部署里的 systemd timer）→ 中途删除 **10 项**的 `run_jobs` 队列行复现产线故障 → 轮询到批次完成，记录提交延迟、排队曲线、恢复结果、事件投递延迟与总控台快照。

## 2. 实测结果（真机 PostgreSQL，2026-09-13 17:38–17:42 UTC）

| 指标 | 实测值 | 规划门 |
| --- | --- | --- |
| 批次规模 / Worker 数 | 100 项 / 2 个真实 Worker 进程 | 100 项 / 2 Worker |
| 批次创建延迟（100 项一次提交） | **84 ms** | — |
| `POST /batches/preview` 延迟 | **p50 39.8 ms / p95 48.65 ms**（20 次） | p95 < 2 s ✅ |
| 批次完成耗时 | **172.5 s**（2 分 52 秒） | 实测而非生成 ✅ |
| 吞吐 | **34.78 项/分钟** | — |
| 结果 | **100/100 SUCCEEDED，0 FAILED** | — |
| 故障注入 | 删除 10 项的 `run_jobs` 队列行（第 10.3 秒） | 10 项故障恢复 |
| 恢复结果 | **10/10 全部 SUCCEEDED，0 FAILED**（对账自动补建队列行） | ✅ |
| 队列曲线 | 每 10 秒采样一次，PENDING 单调下降、`active` 始终 ≤ 2（并发上限生效） | — |
| 事件投递（干净端点专项测量，20 项批次） | 接收端收到 **20/20**；**p50 982.8 ms / p95 1815.1 ms / max 1940.7 ms**（Outbox → 投递成功，同一数据库时钟） | 事件通常 3 s 内到 UI ✅ |
| 渲染规格 | 真实 V1 计划 540×960 / 24 fps / 6 s（144 帧） | — |

> 事件延迟口径：写入 `outbox_events` 到 `delivery_attempts` 首次 `DELIVERED` 的实测差（SQL `percentile_cont`）。控制台页面每 5 秒轮询一次 API，所以「到 UI」还需要加上最多一个刷新周期。

## 3. 负载实测发现并修复的 4 个真实缺陷

| # | 缺陷（实测现象） | 根因 | 修复 |
| --- | --- | --- | --- |
| 1 | 100 项批次只调度 2 项，其余 98 项**永久停在 PENDING**（Worker 日志 `claimed:false`，队列行不存在） | 批次只在创建/恢复时调度一次；项完成后没有任何东西推进 `advance_batch` | ① Worker 完成作业后推进所属批次（`_advance_batch_for_job`）；② 新增调度入口 `POST /internal/v1/batches/advance`（Worker 令牌，`execute_inline=false`，只写 Run 与队列行） |
| 2 | 调度接口 **500**：`psycopg.errors.UniqueViolation: idx_runs_plan_id_idempotency`，随后 `InFailedSqlTransaction` 让后续语句全部失败 | 多个调度器（API 内联执行器、调度 tick、Worker 完成回调）并发为同一项创建 Run；且异常后在同一事务里继续写，PostgreSQL 事务已中止 | ① 原子认领（`UPDATE … WHERE status='PENDING'`，按 `rowcount` 判定）；② **每一项各自一个事务**，失败不影响其他项；③ 同键已有 Run 时复用（`_existing_or_new_item_run`），不重复插入；④ 认领超时（>300 s 未落地 Run）自动退回 PENDING |
| 3 | 新注册端点的事件延迟被推到**几十分钟**（接收端 40 秒内只收到 4 条） | 端点会收到**全部历史事件**：一次注册立刻产生数百条待投递队列 | 端点默认**不回填历史**（`backfill_history` 列，默认 0；需要回填才置 1），`ensure_column` + 幂等 `ALTER TABLE` 兼容旧安装 |
| 4 | 即使不回填，新端点仍然收不到事件（`attempted=0`，被扫描预算饿死） | 投递队列按**全局事件时间**排序取前 N 条：历史/不可达目标的旧事件长期占满名额 | `_pending_deliveries` 改为**按端点轮询**：每端点每 tick 上限 5 条；连接级失败的目标进入 60 s 冷却；跳过原因通过 `skipped_endpoints` 返回 |

回归测试：`tests/test_v6_console_load.py` **10 例**（总控台聚合与口径、队列缺行可见、认领超时回收、同键 Run 复用、**Worker 完成后推进批次**、新端点不回填、显式回填、注册后新事件必达、不可达端点不饿死健康端点、全局上限生效）。

## 4. 验证证据

- 负载报告：`/home/ubuntu/v609_load_report.json`（100 项批次、注入 10 项、恢复 10 项、排队曲线、总控台快照）。
- 延迟报告：`/home/ubuntu/v609_latency_report.json`（干净端点 20/20、p50/p95/max 由 SQL 计算）。
- 前端：`npm run build` 成功、`npm run test:sites` 4/4、web 服务重启后 200；控制台页含 5 秒自动刷新。
- 后端全量：见 `EXECUTION_LOG.md` 本轮记录（V6-08 为 572/572，本阶段新增 10 例）。

## 5. 诚实边界（未完成 / 不能声称的部分）

1. **渲染规格不是 1080p**：吞吐 34.78 项/分钟基于真实的 540×960 / 6 秒 / 3 镜头计划；1080×1920 与更长时长的吞吐未测。
2. **单机部署**：2 个 Worker 与 API 在同一台云主机、同一个数据库；没有多机 Worker 池、网络分区或数据库连接池压力的实测。
3. **没有常驻进程**：调度 tick 与投递 tick 目前由外部脚本/手册驱动（部署需要 systemd timer 或常驻 Worker），产品内未提供常驻调度进程。
4. **事件延迟是「Outbox → 接收端确认」**，不含浏览器渲染；「到 UI」还要加上控制台最多 5 秒的轮询周期。
5. **故障注入只有一种**：只复现了「队列行缺失」这一类故障；Provider 超时、磁盘写满、数据库重启、租约竞争等场景未做负载级演练。
6. **未做长稳测试**：没有连续数小时的浸泡测试；内存/句柄增长未测量。
7. **历史数据未清理**：Outbox/投递记录仍在增长（清理与保留策略属 V6-16 运行手册）。
