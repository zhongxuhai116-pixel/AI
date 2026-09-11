# A02 / A03 验收记录

验收日期：2026-09-11  
范围：`A02 产品与计划合同（不可变合同 + 版本化）`、`A03 数据与任务基础（幂等 + 任务上下文）`

## 执行内容

- 执行 `python -m unittest discover -s tests -p 'test_*.py'`（本地测试套件）。
- 复核并回归 `tests/test_job_control.py`：
  - `test_a02_contract_version_is_immutable_on_plan_update`
  - `test_a03_run_idempotency_conflict`
  - `test_a03_run_access_requires_project_contract_context`

## 命令输出

```text
............ 
Ran 12 tests in 0.322s

OK
```

## 验收结论

- A02：计划变更会生成新合同版本（`version=2`），旧版本仍保留。
- A03：重复 `idempotency_key` 且请求体一致会复用同一 `run_id/job_id`；
      同键不同内容会返回 `409`；
      不同上下文（同 owner 下不同 project）被拒绝为 `403`，体现了计划/Run 上下文隔离。
- 过程问题修复：`update_job` 里原先向 `run_jobs` 写 `progress` 导致列不存在报错，已移除，确保 `jobs` / `runs` / `run_jobs` 字段同步路径一致。

## 下一步

- 继续推进 `A04 独立执行与恢复`。
