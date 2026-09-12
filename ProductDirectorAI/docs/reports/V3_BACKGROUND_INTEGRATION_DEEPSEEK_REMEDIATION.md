# V3-03 背景 Producer 接入同一 Job Worker 整改报告（DeepSeek）

日期：2026-09-12

## 范围

本批把 `strict_background.py` 接入 `execute_claimed_job` 的 Strict 路径，打通受控
Blender 五通道 → 冻结 `background_workflow` → 系统受控背景 Producer → Strict 合成/QA
的离线纵向切片。真实 H3/ComfyUI 独立背景工作流仍未做现场验证，本批不宣称完整 V3。

## 实现

- `apps/api/productdirector_api/main.py`
  - `RunRequest` 新增 `background_workflow` 与 `background_source_mode`，必须成对出现；
    只有 Strict Run 可绑定背景工作流。
  - `create_run_record` 在冻结快照中显式保存 workflow 的 name/version/hash/protection_map
    及 `background_source_mode`，并复核该工作流确实位于已批准的 STRICT 策略列表。
  - 新增 `run_controlled_background_producer`，由同一持租约 Worker 调用：
    - `INDEPENDENT_BACKGROUND_WORKFLOW` 必须存在现场 workflow 文件；缺失直接
      `QA_REJECTED`，不会自动回退到受控导入。
    - `CONTROLLED_IMPORT` 只能使用 `PRODUCTDIRECTOR_BACKGROUND_SOURCE_ROOT/<job_id>`
      的 Job 专属目录，产品 Mask 来自 `strict/mask`；证据标记为验证样本，不声称生成。
  - `execute_claimed_job` 在受控 Blender 五通道成功后按快照模式执行背景 Producer；
    随后运行 `run_strict_runtime_closure`，任一步失败均置 `QA_REJECTED`。
  - `run_strict_runtime_closure` 运行后复核 `background_evidence.json` 的绑定身份、
    逐帧 hash、帧集与受控导入产品区隔离；最终 manifest 标注
    `CONTROLLED_IMPORT_VERIFICATION_SAMPLE`。

- `apps/api/productdirector_api/strict_background.py`
  - 受控导入证据增加 `source_provenance=CONTROLLED_IMPORT`、`generation_claim=false`、
    `input_trust=CONTROLLED_IMPORT_VERIFICATION_SAMPLE`。
  - 新增 `verify_background_evidence_files`，运行后复核目标背景帧集/文件 hash，并对
    受控导入源帧与 Mask 再做隔离与替换检测。

- `tests/test_v3_background_integration.py`
  - 受控导入完整合成正例：Job 专属源目录，最终 `VERIFICATION_PASSED` 且
    `release_eligible=false`，证据明确不可发布。
  - 独立工作流缺现场文件：fail-closed，不自动导入，不留下 `strict/background`。
  - 跨 Job 预置目录复用：拒绝，避免把其他 Job 的本地预置帧包装成当前 Job 受控来源。
  - 缺少 `background_source_mode`：API 422。

## 实际命令与结果

- 编译检查：

  `python -m py_compile apps/api/productdirector_api/main.py apps/api/productdirector_api/strict_background.py tests/test_v3_background_integration.py`

  结果：退出码 0。

- 端到端专项：

  `.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_v3_background_integration -v`

  结果：Ran 4 tests in 42.365s，OK。

- 相关回归：

  `.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_v3_strict_runtime tests.test_v3_run_fidelity_binding tests.test_fidelity_policy tests.test_strict_background -v`

  结果：Ran 70 tests，OK。

- 全量后端回归：

  `.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v`

  结果：Ran 247 tests in 106.887s，OK。

## 已知限制

- `INDEPENDENT_BACKGROUND_WORKFLOW` 的现场 ComfyUI workflow 文件、节点/版本 hash 与
  真实进程产物仍未在本地沙箱验证；缺文件按设计 fail-closed。
- `CONTROLLED_IMPORT` 是显式验证样本模式，只能证明系统从 Job 专属目录受控导入并做
  产品区隔离，不证明背景由当前 Worker 生成，也不提升发布资格。
- 真实 Blender 脚本当前只产出 `passes/mask`，尚缺产品层与合成 Mask 的自动落盘；本次
  端到端正例由测试夹具在 `strict/product` 与 `strict/mask` 写入，属于离线控制流验证。
- 未运行前端 build/Sites；本批为后端接入切片。
- `release_eligible=false`、`fidelity_complete=false` 均保持不变。

## 修改文件

- `apps/api/productdirector_api/main.py`
- `apps/api/productdirector_api/strict_background.py`
- `tests/test_strict_background.py`
- `tests/test_v3_background_integration.py`
- `docs/reports/V3_BACKGROUND_INTEGRATION_DEEPSEEK_REMEDIATION.md`

## 下一步

- 现场只读核实真实独立背景 workflow 文件、节点/模型版本与 ComfyUI 提交/下载证据。
- 打通真实 Blender 产品层与合成 Mask 自动落盘，替代测试夹具的预置 product/mask。
- 对真实背景产物流式收集与磁盘暂存，避免全片内存驻留。
- 在真实来源、分层、QA、人工批准全链证据完成前继续保持验证样本资格。
