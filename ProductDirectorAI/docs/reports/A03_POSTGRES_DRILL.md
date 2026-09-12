# A03 PostgreSQL 迁移与恢复演练（隔离库）

日期：2026-09-12。结论：**迁移与恢复演练 PASS**；但生产 API 仍运行 SQLite，本次不是切换（cutover）。A03 仍为 PARTIAL。

## 1. 为什么要做这一步

主规划要求 V1 使用 PostgreSQL，并明确"不得把 SQLite 原型当作 PostgreSQL 要求已完成"。这是 V1 剩下的最大结构性缺口。

本次交付的是**可复跑的迁移与恢复工具 + 在真实数据上的演练证据**，而不是一次性手工操作：

| 文件 | 作用 |
| --- | --- |
| `db/schema.postgres.sql` | SQLite schema 的 1:1 翻译（列名、可空性、主外键保持一致；ISO 时间字符串仍是 TEXT，便于逐表机械比对） |
| `scripts/pg_migrate.py` | 按依赖顺序拷贝数据，`ON CONFLICT DO NOTHING` 可重复执行，并逐表核对行数 |
| `scripts/pg_restore_drill.sh` | 一致快照 → 迁移 → 幂等复跑 → `pg_dump` → 恢复到第二个库 → 逐表核对行数与内容摘要 |
| `apps/api/requirements-migrate.txt` | 迁移工具依赖（`psycopg[binary]==3.3.5`），运行时 API 不需要 |

## 2. 演练环境

| 项目 | 值 |
| --- | --- |
| 主机 | 云节点（Ubuntu 22.04，与 A05 部署同一台） |
| 数据库 | PostgreSQL 14.24（Ubuntu 包），`listen_addresses=localhost`，端口 5432 |
| 演练角色 | `pddrill`（仅 `LOGIN CREATEDB`，非超级用户） |
| 凭据 | `/etc/productdirector/pg-drill.env`（`root:root` 0600，未进入 Git、未出现在聊天记录） |
| 源数据 | 线上 `var/productdirector.db` 的**只读一致快照**（SQLite 在线备份 API，不修改线上库） |

## 3. 演练结果

### 3.1 快照与迁移

```
snapshot: /home/ubuntu/pd-pg-drill/productdirector-20260912T142628Z.db
sha256   : 0cc572c4ac06047dadc470a91e3a0d21e67c1fede8f098154f1bfcd695f762cc
迁移结果 : ok = true（14/14 张表 source_rows == target_rows）
```

真实数据规模（迁移后核对）：assets 16、product_versions 20、plans 17、plan_contracts 20、jobs 15、runs 10、run_jobs 10、job_attempts 10、job_events 258、auth_sessions 5、provider_credentials 0。

### 3.2 幂等复跑

对同一快照再执行一次迁移：各表 `target_rows` 与首次一致，未产生重复行（`ON CONFLICT DO NOTHING` + 主键/唯一索引生效）。

### 3.3 备份与恢复

```
备份 : pg_dump -Fc → productdirector-drill-20260912T142628Z.dump（48,728 字节）
       sha256 6d3d6aeb7be5dbbcffc04af05bf0b2239011a9fc8e277da98d47485bb4579526
恢复 : 新建 productdirector_drill_restore，pg_restore 还原成功
```

### 3.4 逐表核对（行数 + 内容摘要）

| 表 | 源库 | 恢复库 | 内容摘要 |
| --- | ---: | ---: | --- |
| owners / workspaces / projects | 1 / 1 / 1 | 1 / 1 / 1 | match |
| assets | 16 | 16 | match |
| product_versions | 20 | 20 | match |
| plans / plan_contracts | 17 / 20 | 17 / 20 | match |
| jobs / runs / run_jobs / job_attempts | 15 / 10 / 10 / 10 | 15 / 10 / 10 / 10 | match |
| job_events | 258 | 258 | match |
| auth_sessions | 5 | 5 | match |
| provider_credentials | 0 | 0 | match |

内容摘要为对该表全部行按文本排序后取 `md5(string_agg(...))`，因此不只是行数一致，**内容也一致**。

```
结果：PASS（迁移与恢复后逐表行数和内容摘要一致）
```

## 4. 本次补充的出口门测试

| 用例 | 覆盖 |
| --- | --- |
| `test_a03_same_key_ten_times_reuses_one_run` | 同键同内容连续 10 次只产生 1 个 run/job/run_job/attempt，其余 9 次复用 |
| `test_a03_failed_run_creation_leaves_no_partial_task` | 未批准计划（409）与不存在计划（404）都不会写入任何任务行 |
| `test_a03_transaction_rollback_leaves_no_partial_rows` | 在事务末尾注入异常，回滚后 `jobs/runs/run_jobs/job_attempts/job_events` 计数完全不变 |

后端回归：本地 **65/65** 通过。

## 5. 复跑方式

```bash
cd /home/ubuntu/AI/ProductDirectorAI
sudo ./scripts/pg_restore_drill.sh /home/ubuntu/AI/ProductDirectorAI/var/productdirector.db
```

脚本会自行创建/重建演练库与恢复库；源 SQLite 库始终以只读方式打开。

## 6. 尚未完成（A03 仍为 PARTIAL）

1. **没有切换**：API 仍使用 SQLite。切换需要维护窗口、回滚方案与数据核对，属于独立授权动作。
2. **离线迁移**：迁移基于某一时刻的快照，迁移窗口内的写入不会自动追平；正式切换需要冻结写入或做增量对账。
3. **类型未重新建模**：本次是 1:1 翻译（时间仍是 TEXT、布尔仍是 INTEGER），没有把时间改成 `timestamptz`、没有加约束/索引优化。
4. **数据库运维**：单机本地实例，没有备份计划、保留策略、复制或高可用。
5. **应用层未适配**：`main.py` 仍直接使用 `sqlite3`（含 `PRAGMA`、`BEGIN IMMEDIATE`、`executescript`），尚未抽象出可切换的数据库层。

---

## 7. 应用层跑在 PostgreSQL 上（2026-09-12 追加）

上面第 6.5 条已经处理：新增 `apps/api/productdirector_api/storage.py`，`main.connect()` 改为委托给它。

### 7.1 抽象方式

- 后端由环境变量 `PRODUCTDIRECTOR_DATABASE_URL` 决定：未设置或 `sqlite:///…` 走 SQLite，`postgresql://…` 走 PostgreSQL。
- **SQLite 路径原样返回 `sqlite3` 连接**，行为与历史实现完全一致（既有 65 项测试未改动仍全部通过）。
- PostgreSQL 路径用 `PostgresConnection` 适配 `sqlite3` 的连接形状：`execute/executemany/executescript`、`fetchone/fetchall/rowcount`、以及 `with connect() as db:` 的"成功提交、异常回滚"语义。
- SQL 方言改写在适配器内完成：`?` → `%s`（并转义字面量 `%`）、`INSERT OR IGNORE` → `INSERT … ON CONFLICT DO NOTHING`、`BEGIN IMMEDIATE` → `BEGIN`；列名读取走 `information_schema`；建表使用 `db/schema.postgres.sql`。

`main.py` 的改动只有 5 处（`connect`/`begin_immediate`/`ensure_column`/`initialize_db`/导入），其余数百处 `with connect() as db:` 与 `db.execute(...)` 无需改动。

### 7.2 单元测试

`tests/test_storage_translation.py`（14 项，不需要真实 PostgreSQL）：占位符与 `%` 转义、`INSERT OR IGNORE` 改写、`BEGIN IMMEDIATE` 改写、脚本切分、URL 解析、适配器调用形状、成功提交/异常回滚、`information_schema` 列读取。

后端回归：本地 **79/79**、云端 **79/79** 通过。

### 7.3 真实 PostgreSQL 上的应用层冒烟

`scripts/pg_app_smoke.py` 把 `PRODUCTDIRECTOR_DATABASE_URL` 指向 PostgreSQL 后启动真实 FastAPI（TestClient 走完整中间件），并用**真实 FFmpeg**出片（素材与成片写入临时目录，不污染线上 `var/`）：

| 检查 | 结果 |
| --- | --- |
| `/api/v1/health` | ok（blender / ffmpeg / ffprobe 均 available） |
| 素材上传 + 模板计划 + 分镜编辑 + 批准 + 启动任务 | 通过 |
| 作业终态 | `SUCCEEDED` / `ARTIFACT`，`error=null` |
| 产物 | H.264、1080×1920、144 帧、6.000 秒、146,100 字节 |
| 质量门 | `qa.passed = true` |
| 幂等 | 同 `idempotency_key` 第二次请求 `created=false` 且复用同一 `run_id` |
| 应用自建表 | 14 张表全部由应用在 PostgreSQL 中创建 |

随后直接查 PostgreSQL 核对，行数与"一次逻辑运行"完全一致：

```
jobs=1  runs=1  run_jobs=1  job_attempts=1  job_events=6  plan_contracts=2  product_versions=2
```

### 7.4 仍未完成

1. **线上服务仍跑 SQLite**：切换需要维护窗口、写入冻结（或增量对账）与回滚方案，属于单独授权的动作。
2. **测试仍以 SQLite 为主**：PostgreSQL 路径由冒烟脚本覆盖，尚未把整套测试参数化到两种后端。
3. **性能未评估**：适配器对每条语句做一次字符串改写，未做基准对比。
4. **数据库运维空白**：单机本地实例，没有备份计划、保留策略或高可用。
5. **类型未重新建模**：列类型仍是 1:1 翻译（时间为 TEXT、布尔为 INTEGER）。
