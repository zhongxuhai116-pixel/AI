# V6-08 事务性 Outbox 与签名 Webhook

- 日期：2026-09-13（云端会话）
- 范围：主规划 12.10「Webhook」——事件目录、事务性 Outbox、HMAC 签名与容差、重试计划与死信、密钥轮换、脱敏投递历史；以及 12.9 中 `GET/POST /webhooks`、`POST /webhooks/{id}/test`、`GET /webhooks/{id}/deliveries`。
- 状态：**完成**（模块/接口测试 25/25，真机冒烟 19/19 无失败，后端全量 572/572）；V6 整体仍为 IN_PROGRESS。

## 1. 交付物

### 1.1 模块 `apps/api/productdirector_api/webhooks.py`

| 能力 | 实现与诚实边界 |
| --- | --- |
| 事件目录 | `batch.started`、`batch.completed`、`item.completed`、`item.failed`、`review.required`、`package.ready`、`publish.succeeded`、`publish.failed`、`budget.blocked`（+ 仅用于联调的 `webhook.test`） |
| 信封 | `schema_version`、稳定 `event_id`、`event_type`、`aggregate{type,id,revision}`、`occurred_at`、`payload`、`delivery{semantics=at_least_once, dedupe_by=event_id, ordered=false}`；**不内嵌视频/音频/凭证**（写入 Outbox 前检查，命中即拒绝） |
| 凭证检查 | 文本模式（Bearer、`api_key=`/`token=`/`secret=`/`password=`）+ **结构检查**（键名恰为 secret/token/api_key/… 才算问题）；不使用「长随机串」这类会误伤缓存键与哈希的启发式 |
| 签名 | `HMAC-SHA256(secret, X-PDA-Timestamp + "." + raw_body)`，时间戳与签名分头，另带 `X-PDA-Event-Id`、`X-PDA-Delivery-Attempt`；容差默认 300 秒 |
| 轮换 | 新密钥成为当前密钥，旧密钥进入宽限期（`secret_previous` + 过期时间），宽限期内两个密钥都能验签（`verify_delivery.key_index` 指示命中哪一个） |
| 重试 | 1m/5m/15m/1h/6h/24h；**共 7 次尝试**（首次 + 6 次重试），之后进死信；429/408/5xx/网络错误可重试，其余 4xx 视为配置/权限错误 → 死信并**暂停该目标** |
| 脱敏 | 投递记录截断响应片段并抹掉疑似凭证；列表与投递记录都不返回密钥（只给末 4 位提示） |

### 1.2 事务性 Outbox（与业务写入同事务）

新增四张表：`webhook_endpoints`、`outbox_events`、`delivery_attempts`、`dead_letter_events`（内联 SQLite 与 `db/schema.postgres.sql` 同步）。

`emit_event(db, …)` 接收**业务事务里的连接**，事件与该业务写入同事务提交；测试 `test_outbox_write_is_rolled_back_with_business_write` 注入业务异常，验证事件一起回滚（不会出现「事件已发但业务未生效」）。

已接入的真实发射点：

| 事件 | 触发点 |
| --- | --- |
| `batch.started` / `batch.completed` | 批次聚合状态在 `refresh_batch_status` 中发生转变时（与状态写入同事务） |
| `item.completed` / `item.failed` | 批次项在 `reconcile_batch_items` 中回写为终态时 |
| `review.required` | 该项的 Job 为 `QA_REJECTED` 时（同一处） |
| `package.ready` | 发布包审批通过（`content_hash` 绑定）时 |
| `budget.blocked` | 预算预留被 409 阻断、以及 Automation Key 周期预算超限时（该路径没有业务写入，因此用独立事务，报告如实说明不与其原子） |
| `publish.succeeded` / `publish.failed` | **已在目录中定义但尚未发射**：发布框架属 V6-10…15，未实现前不伪造事件 |

`item.failed` 覆盖 `FAILED` 与 `QA_REJECTED`；**`CANCELLED` 不发事件**（取消是操作员动作，不是生产结果），这一点写在报告里以免被误认为漏发。

### 1.3 API

| 方法与路径 | 说明 |
| --- | --- |
| `GET /webhooks/catalog` | 事件目录、签名方案、请求头、重试计划、死信与轮换约定 |
| `GET/POST /webhooks` | 列表（含投递统计）/ 注册（只接受 HTTPS，本机回环允许 http 用于联调；密钥只返回一次） |
| `PATCH /webhooks/{id}` | 改名称/地址/订阅/启停（revision 乐观锁） |
| `POST /webhooks/{id}/rotate-secret` | 轮换（宽限期可配 0–7 天） |
| `POST /webhooks/{id}/pause`、`/resume` | 暂停/恢复（暂停只停新投递，已入队事件保留） |
| `POST /webhooks/{id}/test` | 写入测试事件并**只针对该事件与目标**立即投递一次 |
| `GET /webhooks/{id}/deliveries` | 脱敏投递历史（游标分页）+ 死信列表 |
| `POST /webhooks/{id}/dead-letters/{event_id}/replay` | 死信重放（原 `event_id` 不变、恢复目标暂停状态） |
| `POST /internal/v1/webhooks/dispatch?limit=&force_due=` | 投递 Worker 入口（Worker 令牌）；`force_due` 用于演练与故障恢复，忽略重试排期 |

### 1.4 控制台页面「事件与 Webhook」

`apps/web/src/App.jsx` 新增 `WebhookPage`（nav 第 17 项）：目标列表（订阅/投递统计/暂停状态）、注册（事件勾选）、一次性签名密钥展示与复制、测试投递、轮换、暂停/恢复、脱敏投递记录与死信重放，以及签名/重试/顺序约定说明。

## 2. 验证证据

### 2.1 模块与接口测试 `tests/test_v6_webhooks.py`（25 例，全部通过）

含真实本机接收端（`ThreadingHTTPServer`）：事件目录、信封语义与去重键、**载荷含凭证即拒绝**、签名正确/错误、时间戳容差、双密钥验签、响应分类与重试计划（含 4xx 不可重试）、URL 校验、事件类型规范化、脱敏、密钥只返回一次、非 HTTPS 拒绝、测试投递可独立复算签名、**同事务回滚**、订阅过滤与禁用目标、**7 次尝试后进死信**、4xx 暂停并拒绝后续测试投递、死信重放（event_id 不变）、429 只重试不暂停、投递历史脱敏、轮换双密钥与宽限、revision 冲突与 URL 校验、目录文档、测试事件不含凭证。

### 2.2 真机 PostgreSQL 冒烟（19/19 PASS，真实接收端进程）

```
[PASS] 事件目录：签名/重试/死信约定
[PASS] 只接受 HTTPS：http 外部地址被拒 422 insecure_url
[PASS] 注册目标：密钥只返回一次
[PASS] 列表不回传密钥
[PASS] 测试投递：送达并记录 DELIVERED
[PASS] 签名可用共享密钥独立复算（HMAC 独立重算一致）
[PASS] 事件信封：至少一次 + 去重键 + 不内嵌凭证
[PASS] 预算阻断返回 409 budget_exhausted
[PASS] 投递 Worker：预算阻断事件送达（attempted=76, delivered=14, retried=62）
[PASS] budget.blocked 事件内容含阻断码
[PASS] 真实 500：第一次进入重试计划（next_retry_at 写入）
[PASS] 连续失败达到最大尝试次数 attempt=7
[PASS] 死信记录：次数与原因（"超过最大投递次数 7"）
[PASS] 投递记录脱敏：接收端回显的 token 片段被 [redacted]
[PASS] 死信重放：event_id 不变且送达
[PASS] 重放使用原 event_id（接收端可去重，attempt=8）
[PASS] 密钥轮换：返回新密钥并保留旧密钥宽限
[PASS] 投递历史分页游标
[PASS] 暂停后不再投递（测试投递 409 endpoint_paused）；恢复后可再投递
```

投递 Worker 的 `attempted=76` 里包含此前失败冒烟留下的待投递事件（其接收端已退出）——它们正按 1m/5m/… 计划重试，这是队列真实工作而非伪造的成功。

### 2.3 全量回归与前端

- 云端全量（含 V6-08 新增 25 例）：

```
Ran 572 tests in 530.028s
OK
```

（V6-07 时为 547/547。）
- `npm run build` 成功，`npm run test:sites` 4/4，`productdirector-v1-web` 重启后 200。

## 3. 诚实边界（未完成 / 不能声称的部分）

1. **没有外部真实接收方**：投递目标全部是本机接收端进程（`127.0.0.1`）；没有第三方系统的验签接入案例，也没有跨公网投递（TLS、DNS、代理、超时抖动）的证据。
2. **顺序不保证**：只承诺至少一次与 `aggregate.revision`；未实现按聚合的分区顺序投递。
3. **投递 Worker 依赖被调用**：没有独立常驻投递进程（只有 `POST /internal/v1/webhooks/dispatch`）；生产部署需要由 systemd timer 或 Worker 循环调用（记在 V6-09 的运维手册里）。
4. **`publish.succeeded`/`publish.failed` 未发射**（发布框架属 V6-10…15）。
5. **`CANCELLED` 不发 `item.failed`**：有意为之，避免把操作员取消当成生产失败。
6. **重试计划未做长周期实测**：只在真机上验证了「立即重试到死信」路径（`force_due`）与首次排期写入，没有等待真实的 24 小时窗口。
7. **事件积压无上限**：Outbox 与投递记录没有自动清理/保留策略（V6-09 运维范围）。
