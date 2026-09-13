# V6 上线运行手册（Runbook）

- 适用版本：V6（V1…V5 能力已包含）
- 部署形态：单机私有部署（API + Web + PostgreSQL + Blender Worker + H3/ComfyUI），所有数据在自有主机
- 诚实前提：**四个平台连接器在没有真实授权时一律 BLOCKED**；本手册不包含任何真实平台凭据

## 1. 服务与目录

| 项 | 值 |
| --- | --- |
| 代码目录 | `/home/ubuntu/AI/ProductDirectorAI` |
| Python 运行时 | `<repo>/.venv/bin/python` |
| Node 运行时 | `/opt/productdirector/node/bin` |
| Blender | `/usr/local/bin/blender`（5.2.1） |
| 服务 | `productdirector-v1-api`（127.0.0.1:8000）、`productdirector-v1-web`（4173）、`postgresql`、`comfy-h3` |
| 服务环境 | `/etc/productdirector/v1.env`（Owner/Worker 令牌）、`/etc/productdirector/pg.env`（DSN） |
| 运行数据 | `<repo>/var/`（SQLite 兜底、上传、渲染、包目录）、PostgreSQL（生产主库） |
| 日志 | `journalctl -u productdirector-v1-api`、`journalctl -u productdirector-v1-web` |

常用命令：

```bash
sudo systemctl status productdirector-v1-api productdirector-v1-web postgresql comfy-h3
sudo systemctl restart productdirector-v1-api
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/v1/health   # 401 = 鉴权生效（未带令牌）
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:4173/               # 200 = 控制台可用
```

## 2. 部署与升级

```bash
cd /home/ubuntu/AI/ProductDirectorAI
git fetch origin && git merge --ff-only origin/main     # 只做快进合并；本地有改动先备份
cp db/schema.postgres.sql /tmp/schema.sql               # 需要时人工审阅
sudo bash -c 'set -a; . /etc/productdirector/pg.env; set +a; psql "$PRODUCTDIRECTOR_DATABASE_URL" -v ON_ERROR_STOP=1 -f db/schema.postgres.sql'
sudo systemctl restart productdirector-v1-api
cd apps/web && PATH=/opt/productdirector/node/bin:$PATH npm run build && PATH=/opt/productdirector/node/bin:$PATH npm run test:sites
sudo systemctl restart productdirector-v1-web
```

数据库迁移是**幂等**的（`CREATE TABLE IF NOT EXISTS` + `ALTER TABLE … ADD COLUMN IF NOT EXISTS` + 启动时的 `ensure_column`），可以重复执行。

## 3. Worker 与调度

V6 有三类后台工作，**目前没有常驻进程**，需要 systemd timer 或手动驱动：

```ini
# /etc/systemd/system/productdirector-worker@.service
[Unit]
Description=ProductDirectorAI Worker %i
[Service]
WorkingDirectory=/home/ubuntu/AI/ProductDirectorAI/apps/api
EnvironmentFile=/etc/productdirector/v1.env
EnvironmentFile=/etc/productdirector/pg.env
ExecStart=/home/ubuntu/AI/ProductDirectorAI/.venv/bin/python -m productdirector_api.main worker --worker-id pd-%i --poll-seconds 2
Restart=always
```

| 工作 | 入口 | 说明 |
| --- | --- | --- |
| 渲染 Worker | `python -m productdirector_api.main worker --worker-id <id>` | 领取 `run_jobs` 并执行真实渲染；完成后会推进所属批次 |
| 调度 tick | `POST /internal/v1/batches/advance`（Worker 令牌） | 只创建 Run 与队列行，不执行渲染 |
| 投递 tick | `POST /internal/v1/webhooks/dispatch?limit=50`（Worker 令牌） | 投递 Outbox 事件；每端点每轮 ≤5 条，不可达端点 60s 冷却 |

建议：调度 tick 每 3 秒、投递 tick 每 2 秒、渲染 Worker 按 GPU 数量起 1–2 个。

## 4. 备份与恢复

```bash
# 备份（数据库 + 运行数据）
sudo bash -c 'set -a; . /etc/productdirector/pg.env; set +a; pg_dump "$PRODUCTDIRECTOR_DATABASE_URL" | gzip > /home/ubuntu/backup-$(date +%F-%H%M).sql.gz'
tar czf /home/ubuntu/var-$(date +%F-%H%M).tar.gz -C /home/ubuntu/AI/ProductDirectorAI var

# 恢复（先停服务，再灌库）
sudo systemctl stop productdirector-v1-api
sudo bash -c 'set -a; . /etc/productdirector/pg.env; set +a; gunzip -c /home/ubuntu/backup-YYYY-MM-DD-HHMM.sql.gz | psql "$PRODUCTDIRECTOR_DATABASE_URL"'
tar xzf /home/ubuntu/var-YYYY-MM-DD-HHMM.tar.gz -C /home/ubuntu/AI/ProductDirectorAI
sudo systemctl start productdirector-v1-api
```

恢复后必须核对：`GET /console/overview`（队列/作业计数）、`GET /packages`（已审批包哈希）、`GET /budgets`（账本金额）。

## 5. 密钥与凭据

| 凭据 | 位置 | 轮换方式 |
| --- | --- | --- |
| Owner 令牌 | `/etc/productdirector/v1.env` `PRODUCTDIRECTOR_OWNER_TOKEN` | 生成新值 → 改 env → 重启 API；旧会话失效 |
| Worker 令牌 | 同上 `PRODUCTDIRECTOR_WORKER_TOKEN` | 同步改所有 Worker 的 EnvironmentFile 后重启 |
| Automation Key | DB（只存哈希） | 控制台「自动化接入」→ 轮换/撤销；撤销立即 401 |
| Webhook 签名密钥 | DB（只存明文用于签名） | `POST /webhooks/{id}/rotate-secret`，旧密钥在宽限期内仍可验签 |
| 成员令牌 | DB（只存哈希） | 控制台撤销后重建 |
| 平台 OAuth 凭据 | 环境变量 `PRODUCTDIRECTOR_<PLATFORM>_CLIENT_ID/CLIENT_SECRET/ACCESS_TOKEN` | 未配置 → 连接器 BLOCKED；配置后需在真实账号完成授权 |

禁止：把任何令牌写进 Git、前端、日志或发布包。发布包 Manifest 会拒绝疑似凭证串与临时签名链接。

## 6. 日常巡检

1. `GET /api/v1/console/overview`：`queue.jobs_without_queue_row` 必须为 0（否则读一次对应批次触发对账补建）；`queue.oldest_queued_at` 不应长期增长。
2. `webhooks.dead_letters_open > 0`：查看 `GET /webhooks/{id}/deliveries` 的死信原因，修复后用死信重放（event_id 不变）。
3. `events.undelivered` 持续增长：确认投递 tick 在跑；`skipped_endpoints` 会说明被跳过的端点与原因。
4. `cost.unpriced_usage_events > 0`：说明有 Provider 没有已知报价（**不会按 0 计入**），需要管理员给出上限或阻断该路线。
5. 磁盘：`var/runs` 与包目录会持续增长；`var/` 已排除在 Git 之外，清理前先确认没有在跑的渲染。

## 7. 故障处置

| 现象 | 处置 |
| --- | --- |
| 批次停在 RUNNING 且项为 PENDING | 确认调度 tick 在跑（`POST /internal/v1/batches/advance`）；读一次批次触发对账 |
| 作业 QUEUED 但 `jobs_without_queue_row > 0` | 读一次对应批次即可补建队列行（V6-07 修复）；仍不动则检查 Worker 是否在跑 |
| 渲染中断后作业停在 RUNNING | `POST /internal/v1/workers/reconcile`（Worker 令牌）把过期租约放回队列 |
| 投递 429/5xx 堆积 | 属正常重试（1m/5m/15m/1h/6h/24h）；超过 7 次进死信，修复接收端后重放 |
| 接收端 4xx（非 408/429） | 目标被自动暂停；修好配置后 `POST /webhooks/{id}/resume` |
| 发布任务 RECONCILING | 说明提交结果未知：先 `POST /publishing/jobs/{id}/reconcile`，不要重复提交 |
| 连接器 BLOCKED | 缺少平台凭据/授权：配置环境变量并在真实账号完成官方授权；**不要用浏览器自动化绕过** |
| 数据库连接失败 | 检查 `pg.env` 与 `postgresql` 服务；不要把 DPAPI 加密的旧库当跨机备份 |

## 8. 回滚

1. 记录当前 HEAD：`git rev-parse HEAD`。
2. 代码回滚：`git checkout <上一个已验证提交>`（数据库只增列不回删，回滚不会破坏数据）。
3. 必要时恢复第 4 节的备份。
4. 回滚后重跑：`.venv/bin/python -m unittest discover -s tests -p 'test_*.py'` 与前端 build。

## 9. 保留策略（待实现，如实记录）

Outbox、投递记录、账本与发布包目录目前**没有自动清理**。上线前建议按季度归档：导出 `outbox_events`/`delivery_attempts`/`cost_ledger` 到冷存储，再删除超过保留期的行；包目录按批次归档后清理。该策略尚未在代码中实现（V6-09 报告已列为边界）。
