# A04 恢复加固与新电脑复验

日期：2026-09-11（新电脑）。结论：**A04 仍为 PARTIAL**；本地竞争窗口、长任务续租与取消/完成竞争已修复并有回归证据，云端真实中断恢复仍未验收。

本报告只记录本轮真实执行结果。历史 A04 报告中的 16 项合同测试仍有效，但“本地测试通过”不等于云端出片与真实进程中断已验收。

## 1. 复验环境

| 组件 | 本机实际 |
| --- | --- |
| OS / Shell | Windows，PowerShell |
| Python | 3.12.10（项目内 `.venv`） |
| Node / npm | 24.14.0 / 11.9.0 |
| FFmpeg / ffprobe | 8.0.1-full_build |
| Blender | 未安装；本轮渲染路径为明确 mock，不代表真实 Blender 出片 |

依赖安装：`python -m venv .venv`，`pip install -r apps/api/requirements-test.txt`，`apps/web` 下 `npm ci` 安装 67 个包（与交接记录一致）。

提示：本机 Vite 构建会解析仓库父目录，受限沙箱下会报 `Cannot read directory`；在允许读取上级目录的环境中构建即通过。这不是项目缺陷。

## 2. 本轮执行结果

| 检查 | 结果 |
| --- | --- |
| `python -m compileall -q apps/api tests` | PASS |
| `python -m unittest discover -s tests -p 'test_*.py'` | PASS，**28/28**（原 23 + 新增 A04 回归 5） |
| `node node_modules/vite/bin/vite.js build` | PASS（4574 模块；仅包体积告警） |
| `node --test tests/sites-worker.test.mjs` | PASS，4/4 |

## 3. 本轮修复的真实缺陷

### 3.1 租约到期时间的格式缺陷（新电脑复验发现）

旧实现把租约到期时间按整秒写入，却用带微秒的当前时间做 SQL 字符串比较。ISO 字符串在同一秒内会因 `+00:00` 与 `.123456+00:00` 的字符差异被判为“已过期”，导致有效期损失最多 1 秒；租约越短误判越明显。

修复：新增 `lease_expiry_text()`，租约到期时间保留微秒并与当前时间格式一致；`require_active_lease` 仍用解析后的 `datetime` 比较，两者语义一致。

### 3.2 跨事务竞争窗口

旧实现先在一个连接里校验租约，再在另一个连接里写状态，中间存在被重新领取后仍写入的窗口。

修复：

- 抽出只负责写入的 `_apply_job_update()`，事务由调用方控制。
- 新增 `update_job_with_lease()`：在同一 `BEGIN IMMEDIATE` 事务内先校验租约（worker + epoch + 到期时间）再落库。
- `complete_leased_job()` / `fail_leased_job()` 的校验、状态写入、租约释放、事件写入合并进单一写事务。
- `claim_job()` 的“查可领取行 + 抢占写租约”也改为 `BEGIN IMMEDIATE`，避免两个 worker 同时领到同一任务。

### 3.3 长任务没有续租

旧实现只在领取时设置一次 `LEASE_SECONDS`（60 秒）；真实 Blender/FFmpeg 渲染超过租约后，另一个 worker 会合法抢走同一任务并重复执行。

修复：新增 `HeartbeatKeeper` 线程，在 `execute_claimed_job()` 执行期间按 `HEARTBEAT_SECONDS`（租约的 1/3，最少 1 秒）周期续租，结束或异常时停止。续租失败会记录 `lease_lost` 并停止重试，不再抢占。

### 3.4 取消与完成的竞争

旧实现可以在用户取消后由迟到的完成回报把任务改回 `SUCCEEDED`。

修复：`_apply_job_update()` 在写入前读取当前状态：

- 当前已 `cancel_requested` / `CANCEL_REQUESTED` 时，`SUCCEEDED` 回报改判为 `CANCELLED`（进度归零，不写入产物引用）。
- 终态（`SUCCEEDED`/`FAILED`/`CANCELLED`）是最终事实：迟到的 `status`/`stage`/`progress`/`cancel_requested` 写入被丢弃，不能把任务改回进行中。
- 失去租约的执行器收到第二次 `409` 后不再改写任务或产物（`is_lease_conflict`）。

## 4. 新增回归用例（`tests/test_job_control.py`）

| 用例 | 覆盖 |
| --- | --- |
| `test_a04_cancel_wins_over_late_completion` | 取消后迟到的完成回报落在 `CANCELLED`，且不提供视频；终态后进度写入无效 |
| `test_a04_reclaimed_job_ignores_stale_worker_writes` | 被重领后旧 worker 的完成/心跳/内部更新均 409，且不能释放新 worker 的租约 |
| `test_a04_long_render_renews_lease_and_blocks_rival_claim` | 渲染时长超过一个租约周期时心跳续租有效，竞争 worker 无法领取，作业最终 `SUCCEEDED` |
| `test_a04_render_without_heartbeat_loses_lease_to_rival` | 负向对照：关闭心跳后租约确实会被重领，且旧 worker 不得覆盖新 worker 的任务 |
| `test_a04_killed_worker_is_reconciled_and_finished_by_new_worker` | 进程被杀（无回报）→ 租约过期 → 对账回 `QUEUED` → 新 worker 完成到 `SUCCEEDED`，旧 worker 迟到回报 409 |

同时修正 `tests/test_security.py` 的产物路径断言：Windows 临时目录可能是 `ADMINI~1` 短路径，期望值需与解析结果同样 `resolve()` 后比较。

## 5. 尚未验收（继续保持 PARTIAL）

1. 真实 Blender/FFmpeg 长任务上的杀进程重领、渲染中途抢占与产物清理未在云端实测；本轮渲染为 mock。
2. 远程任务输入下载、受限成果上传、受控隧道与真实云任务回收仍未实现，HTTP 身份边界不等于远程执行闭环。
3. 编码失败后的自动重试与退避尚无入口；失败任务目前只能靠人工重建作业。
4. 事件流是 `Last-Event-ID` 轮询续读，不是长连接 SSE 推送。
5. SQLite 原型仍不是主规划要求的 PostgreSQL/租约实现，不得据此宣布 A04 整包通过。
