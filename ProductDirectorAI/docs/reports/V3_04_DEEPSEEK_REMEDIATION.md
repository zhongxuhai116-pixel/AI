# V3-04 QA 引擎与审批 Hash 绑定 — DeepSeek Remediation

日期：2026-09-12

## 范围

本批仅完成 V3-04 的 QA 阈值治理、QA 审批绑定失效、发布资格门纠偏，不宣称 V3-04 或 V3 阶段整体通过。

## 实现

- QA 阈值集版本冻结后不可改。
- QA 阈值只允许不宽于默认基线：拒绝 `color_core_mae_max=1`、`contour_iou_min=0`、`min_mask_binary_ratio=1.5`、`verified_dimension_max_relative_error=2.0` 等放宽/越界请求。
- IoU/coverage/binary-ratio 与尺寸误差限制在 `[0,1]`，MAE 限制在合理 `[0,1]`。
- `decide_qa_report` 批准前重读当前 `metadata.json`，重算 `manifest_sha256` 与 QA report hash，并逐文件复核 `input_manifest`、`source_manifest`、`composite_out`、`strict_preview.mp4`。
- Strict 批准必需 `input_manifest`、`output_sha256`、`composite_output_sha256`；`REGISTERED_LOCAL_SAMPLE` 还必须有受控来源 `source_manifest`。
- QA 报告读取时动态计算 `decision_valid`、`effective_decision`、`decision_invalid_reason`；文件/阈值/审核/资产/Manifest 变化后旧 `APPROVED` 显示为 `REVOKED`。
- 同一 Job 以最新 QA 报告为准；新 QA 报告（含 FAIL/NOT_VERIFIED/未批准）使旧批准失效，旧报告不可再审批。
- `job_release_eligibility` 修复 `input_trust` 分支缺 `return` 的 fail-open；Strict 发布资格额外要求当前有效 QA 人工批准，V1/V2 legacy 行为不变。

## 实际命令与结果

所有后端命令均使用 `.\.venv\Scripts\python.exe -X utf8`，并设置项目内 `$env:TMP` 与 `tempfile.mkdtemp` 包装，避免 Windows 临时目录权限问题。

- `py_compile main.py test_v3_qa_api.py test_v3_qa_engine.py`：通过。
- `tests.test_v3_qa_api`：9/9 通过。
- `tests.test_v3_qa_engine`：7/7 通过。
- `tests.test_v3_strict_runtime`：34/34 通过。
- 全量后端 `discover -s tests -p test_*.py`：269/269 通过，202.186s。
- `git diff --check`：无输出。

## 正负例

- 正例：旧 QA 报告正常读取有效；恢复被篡改文件后有效批准恢复。
- 负例：篡改 `metadata.json`、`product`、`mask`、`background`、`composite_out`、`strict_preview.mp4` 后旧批准 409 且读取为 `REVOKED`。
- 负例：更换阈值/产品审核 payload 或资产文件后旧批准失效。
- 负例：新 FAIL QA 报告产生后，旧 APPROVED 报告 `decision_valid=false`，发布门 `eligible=false`。
- 负例：不可信 `input_trust` 即使 `SUCCEEDED + fidelity_complete=true + output_path` 存在，仍 `eligible=false`。
- 负例：Strict 可发布条件缺少有效 QA 批准时 `eligible=false`。
- 负例：宽松 QA 阈值与越界比例/MAE 均 422。

## 修改文件

- `apps/api/productdirector_api/main.py`
- `tests/test_v3_qa_api.py`
- `docs/reports/V3_04_DEEPSEEK_REMEDIATION.md`（本报告）

## 已知限制

- QA API 绑定正例使用 `@patch(strict_qa.run_strict_qa, PASS_QA)`，只证明绑定/审批流，不替代真实 QA 指标证据。
- 本地无法证明的物理/视觉指标继续 `NOT_VERIFIED` 且 fail-closed。
- 未实现“阈值放宽需 ADR/质量报告审批”的完整流程；当前策略是 fail-closed，仅允许不宽于默认基线，未来如需放宽应新增明确审批合同。
- 前端 build 与 Sites 测试本轮未由本代理重跑：沙箱阻止 Node spawn，且未获提权；本批未改前端。请由控制端标准环境复验。
- 真实 Blender/H3/ComfyUI 端到端仍属后续现场验证范围，不因本轮 QA 绑定测试宣称完整 V3。

## 下一步

- 控制端独立复验本批 API/绑定与发布门。
- 未来发布入口必须消费 `job_release_eligibility` 或等价有效性门，不能读旧 `decision` 字段。
- 后续补齐受控媒体质量报告与真实现场全链证据。
