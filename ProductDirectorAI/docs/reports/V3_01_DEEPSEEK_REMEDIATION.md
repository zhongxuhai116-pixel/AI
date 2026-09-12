# V3-01 冻结绑定整改报告（DeepSeek）

2026-09-12。本批为 V3-01 的可独立验收纵向切片：不可变产品版本、审核、策略与 Run 冻结绑定。不宣称整个 V3 或 V3-01 完整 PASS；控制端负责独立验收。

## 目标与边界

- 产品版本变化后，旧保真审批不能继续被新 Run 复用。
- 新 Strict Run 必须显式引用真实冻结的 `plan_contract_id` + `product_review_id` + `fidelity_policy_id`，并核对真实 `payload_sha256`，不从可变 latest 猜测审核/策略。
- 保持 V1/V2 既有 Run 行为兼容（默认 LEGACY 路径不变）。
- 不修改控制端独立验收报告、`docs/BROKER_V6_CONTROL.md`、`var/acceptance` 历史证据。

## 实现合同

- `RunRequest` 新增可选字段：`require_fidelity_snapshot`、`plan_contract_id`、`product_review_id`、`product_review_sha256`、`fidelity_policy_id`、`fidelity_policy_sha256`。
- 显式开启 `require_fidelity_snapshot=true`，或传入任一绑定字段时，强制三件套齐全，否则 Pydantic 422。
- Strict 绑定校验顺序：
  1. 幂等复用优先（同 `idempotency_key` + 同请求 hash 直接返回既有 Run）。
  2. 冻结合同必须存在且属于该计划/owner/project。
  3. 冻结合同必须是该计划最新版本；旧合同返回 409（旧保真审批已失效）。
  4. 审核必须属于合同指向的产品版本、属于当前 owner、`decision=APPROVED`。
  5. 策略必须属于同一产品版本；客户端提供的 hash 与 DB 冻结 hash 不一致时 409。
- `runs` 表新增快照列并写回真实 hash：`product_review_id`、`product_review_sha256`、`fidelity_policy_id`、`fidelity_policy_version`、`fidelity_policy_sha256`、`fidelity_snapshot_json`。
- SQLite `initialize_db` 与 `db/schema.postgres.sql` 同步迁移；既有库通过 `ensure_column` 增量加列。
- Run 幂等 hash 在 Strict 模式下并入绑定字段，避免不同冻结引用共享同一 key。

## 修改文件

- `apps/api/productdirector_api/main.py`
- `db/schema.postgres.sql`
- `tests/test_v3_run_fidelity_binding.py`（新增）
- `docs/reports/V3_01_DEEPSEEK_REMEDIATION.md`（本报告）

## 实际命令与结果

前置：sandbox 下 `tempfile.mkdtemp` 会被 Windows ACL 拒绝，使用既有只读 hook：

```powershell
$env:PYTHONPATH = 'var\acceptance\2026-09-12-deepseek-remediation;' + (Get-Location).Path
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_v3_run_fidelity_binding -v
```

结果：`Ran 9 tests ... OK`。

相关回归：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_job_control tests.test_product_versions tests.test_product_reviews tests.test_fidelity_policy -v
```

结果：`Ran 38 tests ... OK`。

全量后端：

```powershell
$env:PYTHONPATH = 'var\acceptance\2026-09-12-deepseek-remediation;' + (Get-Location).Path
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -p 'test_*.py'
```

结果：`Ran 186 tests ... OK`。

前端（无前端改动，供复验参考）：

```powershell
npm --prefix apps/web run test:sites
npm --prefix apps/web run build
```

结果：两项均以 `spawn EPERM`（errno -4048）在沙箱内失败；为已知环境限制，非代码失败。需控制端在可执行环境复验。

## 行为正负例

正例：

- 批准计划 → 建 APPROVED 审核 + STRICT 策略 → 显式三件套创建 Run → 202，响应与 `runs` 行均记录真实 `payload_sha256`。
- 客户端同时提供正确 review/policy hash → 202。
- 后续新增第二版审核/策略，历史 Run 快照保持原引用不变。
- 无绑定字段的 LEGACY Run → 202，`fidelity_snapshot=null`。

负例：

- 缺任一三件套或 hash 无对应 id → 422。
- 编辑并重新批准计划后，用旧 contract/review/policy → 409（`旧保真审批已失效`）。
- 新合同引用旧版本 review → 404。
- 错误 review hash 或 policy hash → 409。
- `decision=REJECTED` 审核 → 409。
- 审核 reviewer 被改为其他 owner → 403。
- 失败绑定不写入 jobs/runs/run_jobs/job_events（无残留）。

## 已知限制 / 未完成项

- 渲染/合成/QA 消费这些冻结字段尚未接入；本切片只完成 Run 侧合同与持久化。
- 无 Run/Job 详情接口单独返回 fidelity snapshot；当前在 `POST /runs` 响应与 DB 列可见。
- PostgreSQL 仅做 schema/迁移对齐，未在真实 PostgreSQL 实例执行 DDL/回归。
- 未实现 UI 选择审核/策略版本，未实现 QA hash 批准闭环。
- 前端 build/test:sites 受 sandbox `spawn EPERM` 阻塞，未在本轮验证。

## 下一步

- 控制端独立复验本切片正负例与全量回归。
- 后续把 `fidelity_snapshot` 传给 RENDER/COMPOSITE/QA 状态机，并阻止未绑定或 QA_REJECTED 的 Run 进入 SUCCEEDED。

## 纠偏补丁（2026-09-12）

- `freeze_fidelity_binding()` 增加 `policy["mode"] != "STRICT"` 检查：`require_fidelity_snapshot=true` 仅接受 STRICT 策略；CONTROLLED/CREATIVE 返回 409。
- 新增 API 负例：CONTROLLED 与 CREATIVE 均 409，且 jobs/runs/run_jobs/job_events 无残留。
- 幂等复核：同请求在旧审批失效前重试返回 202 且 `reused_idempotent=true`、`created=false`、`run_id` 不变；旧审批因计划版本变化失效后，同 `idempotency_key` 重试返回 409，不新建 Run，也不冒充新有效 Strict Run。
- LEGACY V1/V2 行为不变。

更新后的测试结果：

- `tests.test_v3_run_fidelity_binding -v`：`Ran 12 tests ... OK`
- 相关组合 `tests.test_job_control tests.test_product_versions tests.test_product_reviews tests.test_fidelity_policy tests.test_v3_run_fidelity_binding -v`：`Ran 50 tests ... OK`
- 全量后端 `unittest discover -s tests -p 'test_*.py'`：`Ran 189 tests ... OK`