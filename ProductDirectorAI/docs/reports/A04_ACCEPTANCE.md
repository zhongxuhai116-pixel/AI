# A04 验收记录

验收日期：2026-09-11  
范围：`A04 独立执行与恢复`：持久任务事件、Worker 领取/心跳/完成/失败接口、租约 epoch、断线续读、过期租约对账与独立 worker 命令入口。

## 执行内容

- 为 `run_jobs` 增加租约字段：`lease_owner`、`lease_expires_at`、`lease_epoch`。
- 新增 `job_events` 表，用数据库保存任务事件历史。
- 新增内部 Worker 接口：
  - `POST /internal/v1/workers/claim`
  - `POST /internal/v1/workers/jobs/{job_id}/heartbeat`
  - `POST /internal/v1/workers/jobs/{job_id}/complete`
  - `POST /internal/v1/workers/jobs/{job_id}/fail`
- 新增事件恢复接口：`GET /api/v1/jobs/{job_id}/events`，支持 `Last-Event-ID` 续读。
- 修正 Run 创建链路，确保 `runs`、`jobs`、`run_jobs`、`job_attempts` 在同一任务创建路径落地。
- 新增重启对账：`POST /internal/v1/workers/reconcile`，可把过期租约的 RUNNING 任务拉回 QUEUED。
- 新增独立 worker 命令入口：`python -m productdirector_api.main worker --once --worker-id <id>`。

## 验收命令

```text
.\.venv\Scripts\python.exe -m compileall -q apps/api/productdirector_api/main.py tests/test_job_control.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'
```

结果：

```text
................
Ran 16 tests in 0.796s

OK
```

新增 A04 用例：

- `test_a04_worker_lease_blocks_stale_completion`：旧租约 epoch 的完成回报返回 409，新 Worker 在租约过期后可重新领取并提交失败态。
- `test_a04_events_can_resume_after_last_event_id`：任务事件包含创建、领取、心跳；带 `Last-Event-ID` 后只续读后续事件。
- `test_a04_reconcile_requeues_expired_running_job`：过期 RUNNING 租约可对账回 QUEUED，并由新 worker 领取。
- `test_a04_worker_once_executes_claimed_job`：独立 worker-once 路径可领取任务并执行到 SUCCEEDED。

命令入口冒烟：

```text
$env:PYTHONPATH='apps/api'; .\.venv\Scripts\python.exe -m productdirector_api.main worker --once --worker-id smoke-worker
{"claimed": false}
```

## 未完成项

- 本轮已提供独立 worker 命令入口；原本的本机 BackgroundTasks 仍作为兼容执行入口。
- 还未在真实 Blender/FFmpeg 长任务上做“杀 worker 进程后自动重领并完成”的云端实测；本地用 mock 渲染路径完成了合同级回归。
- 前端 build 与 Sites worker 测试仍受本机 `npm` 不在 PATH 阻塞，命令未通过：
  - `npm run build`
  - `npm run test:sites`

## 下一步

- 进入 A05：安全远程闭环，包括同源会话/CSRF、Worker 身份边界、远程 API 基址配置与 Linux 凭证适配。
