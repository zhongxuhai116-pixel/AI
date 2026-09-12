# V3-03 前半段：受控产品五通道到 Strict 全片闭环整改报告

日期：2026-09-12

## 结论

本批把 Strict 独立分层合同从“策略允许列表”语义修正为“策略允许 + 本镜头/本次合同明确要求”，并补齐按分镜帧区间逐帧覆盖校验。产物仍为 `VERIFICATION_PASSED`、`release_eligible=false`，未把预置/注册本地小样或 mock Blender 渲染升级为可发布资格。

## 变更文件

- `apps/api/productdirector_api/main.py`
  - `Shot` 增加 `required_strict_layers`（本镜头明确要求的独立层）。
  - 新增 `STRICT_OPERATION_TO_LAYER`、`_required_strict_layers_from_plan`、`_required_layer_frame_contracts`、`_layer_file_frame_set`、`_optional_layer_frame_file_failures`。
  - `_strict_layer_contract_failures` 增加必需层覆盖检查：必需帧必须覆盖，策略未允许的层拒绝；允许但未必需的合法帧不因不在 required set 被拒，但仍校验帧号可识别且无重复。
  - 注册、运行时 registered 与 preseeded 三条路径统一做计划级/分镜级图层合同复核。
  - 移除未实现且误导 schema 的 `strict_full_source_request_json/sha256` 迁移列。
- `scripts/strict_composite.py`
  - 支持计划合同中的 `required_layers` 逐层帧集；必需层只要求覆盖必需区间，允许但从未必需的层可仅提供合法部分帧，所有提供帧必须落在冻结全片帧集合内。
  - 可选层缺帧读取改为跳过缺失帧，避免已记录结构化失败后再抛 `KeyError`。
- `contracts/image-preview.v1.schema.json`
  - `shot` 定义补充 `required_strict_layers` 字段，保持 Schema 与运行时同步。
- `tests/test_v3_strict_runtime.py`
  - 新增允许但非必需层可缺、必需层缺失拒绝、出现未允许层拒绝、必需层须覆盖指定分镜帧区间、分镜区间覆盖正确通过等正负例。

## 实际命令与结果

```powershell
# 项目根：C:\Users\Administrator\Desktop\MEET BENI 4K\GitHub-AI-Archive\ProductDirectorAI
$env:PYTHONPATH = 'var\acceptance\2026-09-12-deepseek-remediation;' + (Get-Location).Path
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_v3_strict_runtime -v
# Ran 34 tests ... OK

.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -p 'test_*.py'
# Ran 227 tests ... OK

.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_contract_parity -v
# Ran 11 tests ... OK
```

前端 `npm run build` 与 `npm run test:sites` 本次未完成：沙箱内报 `spawn EPERM`，请求沙箱外运行被拒绝。本批未修改 `apps/web`，后端合同测试不受影响；控制端如复验前端可沿用既有命令。

## 行为正例

- 策略允许 `shadow_layer` 但本镜头未要求，且输入无 shadow 层：注册 200、Run `VERIFICATION_PASSED`。
- 策略允许 `shadow_layer`、任何镜头均未要求，且只在镜头1部分帧提供 shadow：注册 200、Run `VERIFICATION_PASSED`。
- 镜头 2 明确要求 shadow，且 shadow 覆盖镜头 2 的帧区间 49–96：注册 200、Run `VERIFICATION_PASSED`。
- 镜头 2 明确要求 shadow 49–96，镜头 1 虽未必需但策略允许且提供合法 shadow 1–48：注册 200、Run `VERIFICATION_PASSED`，混合镜头合法帧不被当作多余帧拒绝。
- 受控 Blender mock 产品五通道：仍 `CONTROLLED_BLENDER_PRODUCT`，`fidelity_complete=false`、不可发布。

## 行为负例

- 镜头明确要求 shadow 但 shadow 层完全缺失：注册 409 / preseeded `QA_REJECTED`。
- 镜头 2 要求 shadow，但只在镜头 1 提供 shadow 1–48、镜头 2 必需段 49–96 缺失：注册 409，报告含 `缺少帧`，不把全局一帧/相邻镜头存在当作必需段满足。
- 允许但未必需的 shadow 提供越出冻结全片帧集合的帧：注册 409，报告含 `越出`；合成器同样拒绝。
- 镜头未要求 shadow 且策略未允许 shadow，但输入含 shadow：注册 409，报告 `未获策略允许`。
- 旧审批/版本失效、错 hash、错 owner、错 product version、资产 hash 变化、渲染进程失败、缺 pass：沿用现有 fail-closed 行为。

## 已知限制

- 本批没有启动真实 Blender/GPU；受控渲染仍用 `run_controlled_blender_command` mock 进程验证控制流，不能声称真实五通道产物已验证。
- `REGISTERED_LOCAL_SAMPLE`、`PRESEEDED_LOCAL_SAMPLE`、`CONTROLLED_BLENDER_PRODUCT` 均为验证态，`fidelity_complete=false` 且不可发布。
- 必需层按分镜区间做“该层必须覆盖该区间”校验，但当前不区分同一层在相邻镜头的连续片段是否来自同一受控工作流；真实来源仍缺少 H3/ComfyUI 背景工作流的受控进程证据。
- 背景层目前只验证已批准工作流哈希与保护映射、逐文件哈希；其真实生成进程证据仍需后续受控 worker 批次。

## 下一步

- 打通真实 Blender 产品五通道与 H3/ComfyUI 背景工作流的受控生成、进程证据与冻结哈希。
- 将注册/预置本地样本逐步替换为 `VERIFIED_RENDER_SOURCE` 系统受控证据链。
- 补背景工作流版本/输入保护区映射的现场复验与真实 OpenEXR 72 帧流式处理。
