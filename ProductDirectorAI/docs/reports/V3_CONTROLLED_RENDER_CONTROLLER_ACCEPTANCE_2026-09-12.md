# V3 受控 Blender 来源代码切片：控制端复验

日期：2026-09-12。范围仅限受控产品五通道 Producer 的代码控制流，不代表真实 Blender 端到端或 V3 阶段通过。

## 判定

- **代码切片 PASS**：Strict Job 在无预置来源时由持租约 Worker 发起受控命令；冻结 Blender 路径/版本、脚本、资产、计划、命令、退出码、通道解析和逐文件 hash；进程失败、缺 Pass、资产变化、版本失效、文件篡改拒绝。
- **真实 Producer 证据 NOT_TESTED**：本机没有 Blender，正例使用 mock 进程写测试帧。历史云端 72 帧不能代替本次系统发起的过程证据。
- **发布门 FAIL-CLOSED**：成功正例仍为 `VERIFICATION_PASSED`、`fidelity_complete=false`、`release_eligible=false`。未见实际背景工作流、分层来源、完整 QA 与人工批准 hash，因此 V3 仍 `IN_PROGRESS`。

## 控制端独立执行

- `python -X utf8 -m unittest tests.test_v3_strict_runtime -v`：23/23 PASS。
- `python -X utf8 -m unittest discover -s tests -p 'test_*.py'`：213/213 PASS。
- `git diff --check`：通过；仅有工作树 LF/CRLF 提示。
- 本批没有前端修改，沿用此前前端构建和 Sites 4/4 结果；未重复运行。

控制端审代码发现最初草稿的 `plan_snapshot` 参数引用问题与硬编码通道回退，并在交付前送回开发端；最终形参改为 `plan_snapshot: dict`，`pass_semantics` 改为实际解析结果，专项测试随后通过。下一批应将受控产品产物与批准的背景/独立层接入全片闭环，严格保留未完成的发布阻断。
