# V6-07 Automation API（Key scope / 幂等 / 限流 / 调用面）

- 日期：2026-09-13（云端会话）
- 范围：主规划 12.9「Automation API」——受限外部调用面、Key scope、幂等键、双层限流、OpenAPI 说明；并修一条产线上发现的批次队列行缺失缺陷。
- 状态：**完成**（后端 547/547，真机 PostgreSQL 冒烟 21/21；含真实自动化批次出片）；V6 整体仍为 IN_PROGRESS。

## 1. 交付物

### 1.1 模块 `apps/api/productdirector_api/automation.py`

| 能力 | 实现与诚实边界 |
| --- | --- |
| Key 形态 | `pda_<key_id>_<secret>`；服务端只存 `secret` 的 sha256 与 12 位指纹；**明文只在创建响应里出现一次**，列表/日志只出现 Key ID 与指纹 |
| Scope | 7 个 scope：`projects:read`、`assets:write`、`generate:write`、`jobs:read`、`packages:read`、`publish:write`、`webhooks:manage`；路由 → scope 表逐条登记，**未登记路由一律拒绝**（不默认放行） |
| Key 管理 | 创建/撤销只允许 Owner 会话；Automation Key 调用 Key 管理接口返回 403 `owner_session_required`；撤销幂等，撤销后新调用立即 401 `key_revoked` |
| 限制 | 每个 Key 可设：工作区、项目、IP 允许列表（支持 `10.0.0.*`/`*`）、每分钟限额（1–6000）、周期预算（minute/hour/day/month）、有效期（≤3650 天） |
| 双层限流 | 按 **Key + 工作区**各记窗口计数（默认 60 请求/分钟），超限 429 带 `Retry-After` 与 `X-RateLimit-Limit/Remaining/Reset/Scope`；限额在响应与错误里都说明**是本产品初始设置、不是平台事实** |
| 幂等 | `Idempotency-Key` 按 (owner, 调用方, 接口, 键) 隔离并绑定请求规范化哈希；同键同体重放返回首次结果 + `Idempotency-Replayed: true`，同键不同体 409 `idempotency_conflict`；创建任务/发布类的 Automation 调用缺键 → 428 `idempotency_key_required` |
| Key 周期预算 | 账本行记录 `actor_key_id`（V6-06 的 `cost_ledger` 扩展列），Key 的周期支出 = 该 Key 产生的 settled+accrued 之和；超限后写调用 409 `key_budget_exhausted`（预留不计入） |

### 1.2 API

| 方法与路径 | 说明 |
| --- | --- |
| `POST /automation-keys` | 创建 Key（Owner 会话），返回一次性明文 + 限额文档 + 后续步骤 |
| `GET /automation-keys` | 列表（无密钥、无哈希） |
| `DELETE /automation-keys/{id}` | 撤销（幂等；记录原因） |
| `GET /automation/openapi` | 调用面说明：路由→scope、强制幂等键路由、限额、错误码 |
| `POST /automation/generate` | 复用批次创建实现（同一校验/预算/生产服务）；`auto_publish=true` 必须带 `budget_id` 且预算必须有上限 |
| `GET /automation/batches/{id}` | 与 `GET /batches/{id}` 同一实现，另给 `publish_intent` 与 `publish.status` 分栏 |
| `GET /automation/jobs/{id}` | 按 BatchItem ID / Run ID / Job ID 查询，**生产状态与发布状态分开** |

认证：`Authorization: Bearer pda_...`（无 Cookie、无 CSRF）；`security.authenticate` 增加 Key 分支，中间件在认证后执行 scope / Key 管理边界 / 双层限流 / Key 预算检查。

### 1.3 修的真实缺陷：批次项队列行缺失会永久卡住

对账 `reconcile_batch_items` 过去只回写终态，**不检查队列行是否存在**；产线数据库里查到 2 个 `QUEUED` 作业的 `run_jobs` 行为 0（`created_at` 14:37/14:42，属修复前的历史遗留）。现在对账会调用 `_requeue_missing_run_job` 幂等补建 `run_jobs` + `job_attempts`，`start_batch_item_run` 也改为复用同一实现（不再两份 SQL）。新增回归测试 `test_reconcile_requeues_item_with_missing_queue_row`。

**如实记录**：这 2 个孤儿作业的 `batch_items` 行已不存在（V6-04 重试流程重建了项），因此它们**不属于**当前任何批次的项；补建逻辑按设计只处理仍被项引用的作业。孤儿 QUEUED 作业的定期清扫属于 V6-09「故障恢复与负载」范围，尚未实现（不假装已解决）。

### 1.4 控制台页面「自动化接入」

`apps/web/src/App.jsx` 新增 `AutomationPage`（nav 第 16 项）：Key 列表（scope/限额/状态/调用次数/最后使用）、创建 Key（scope 勾选、IP、限额、周期预算、有效期）、**一次性密钥展示 + 复制**、撤销、以及调用面文档（路由→scope、强制幂等键、限额、错误码）。

## 2. 验证证据

### 2.1 测试

- 新增 `tests/test_v6_automation.py` **30 例**：规则层（Key 格式/哈希/scope 映射/幂等路由/IP 允许列表/限流窗口/指纹规范化/限额文档/TTL）与接口层（密钥只显示一次、未知 scope 422、scope 越权 403、未登记路由 403、Key 不能管理 Key、撤销后 401、伪造密钥 401、过期 401、双层限流 429 与响应头、缺幂等键 428、重放同批次、同键不同体 409、状态分栏、auto_publish 需要预算与上限、Key 预算阻断、IP 白名单、审计计数、调用面文档）。
- `tests/test_v6_batch.py` 新增 1 例队列行补建回归（共 20 例）。
- 云端全量（含 V6-07 新增 30 例与批次补建回归）：

```
Ran 547 tests in 480s
OK
```

### 2.2 真机 PostgreSQL 冒烟（21/21 PASS）

```
[PASS] Key：创建并显示一次密钥 status=201
[PASS] Key：列表不含密钥
[PASS] Key：允许的读请求 status=200 limit=60
[PASS] 越权：缺 packages:read → 403 required=['packages:read']
[PASS] 越权：Key 不能管理 Key → 403 owner_session_required
[PASS] 幂等：缺 Idempotency-Key → 428
[PASS] 调用面：scope/限额/错误码可见
[PASS] Key 周期预算：超额写调用 409 key_budget_exhausted
[PASS] 生成：202 且带自动化意图 actor=key:2a8fd909d3341c78
[PASS] 幂等：同键同体重放同一批次 replayed=true
[PASS] 幂等：同键不同体 409
[PASS] 批次聚合：含发布意图与状态分栏
[PASS] 作业查询：生产与发布状态分开 production=RUNNING
[PASS] 限流：第 3 次 429 且带 Retry-After codes=[200,200,429] remaining=[1,0,0] retry_after=40
[PASS] 撤销：Owner 会话可撤销
[PASS] 撤销后立即 401 key_revoked
[PASS] 审计：Key ID + 调用次数 + 最后使用时间 calls=8
```

**真实产出**：冒烟通过 Automation API 创建的两个批次各自完成了一个**真实渲染项**（`batch_items.status=SUCCEEDED`，作业 `20120a40`、`77650128`，批次 `01532212`、`23101ac6` 均为 `COMPLETED`）——自动化路径与 UI 走的是同一套生产服务，不是另写的捷径。

### 2.3 前端构建

`npm run build` 成功，`npm run test:sites` 4/4 通过，`productdirector-v1-web` 重启后 200。

## 3. 诚实边界（未完成 / 不能声称的部分）

1. **幂等键强制范围**：`Idempotency-Key` 目前对 **Automation Key 调用**强制（缺则 428）；浏览器会话路径沿用既有 CSRF + 请求体可选 `idempotency_key`（前端批次创建已带该字段），未强制请求头。这是有意的分阶段决定，V6-16 统一时若要让会话路径也强制，需要前端补发请求头并同步改既有测试。
2. **限流数值是产品设置**：60 请求/分钟、100 展开项、2 GPU Job / 4 远程操作都是本产品初始值，未做真实压测标定（V6-09 的负载测试会实测后再定），文档与错误里都这样标注。
3. **没有真实外部客户端接入**：冒烟脚本是本机客户端；没有第三方系统的真实接入案例与密钥轮换演练。
4. **Key 预算只按已发生金额**：按 `(settled + accrued)` 聚合，未把未结预留计入；多币种不换算（沿用 V6-06 的边界）。
5. **孤儿作业未清扫**：见 1.3，属于 V6-09。
6. **Webhook scope 已登记但接口未实现**：`webhooks:manage` 目前只覆盖 `/webhooks` 路由登记，端点本身在 V6-08 实现。
