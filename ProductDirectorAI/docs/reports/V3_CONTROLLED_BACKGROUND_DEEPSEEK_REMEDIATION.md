# V3-03 受控背景工作流 Producer 整改报告（DeepSeek）

日期：2026-09-12

## 范围

本批只交付 V3-03 的**系统受控背景 Producer 适配层与离线正负例**，不是完整 V3 或真实
Blender→背景→合成端到端证明。当前证据仍为验证样本，不改变 `VERIFICATION_PASSED`、
`fidelity_complete=false`、`release_eligible=false` 的既有资格门。

## 本轮修正

- `apps/api/productdirector_api/strict_background.py`
  - `run_controlled_background_workflow` 不再把缺失/非 dict 的冻结 Run 工作流回退成
    `approved_workflow` 自比较；缺失或字段不全立即 fail-closed。调用方显式传入的
    `requested_workflow` 会先与不可变 Run 快照逐项复核，再与批准策略项复核。
  - `collect_controlled_comfyui_frames` 在下载/写盘前预检帧名不可解析、越出冻结帧集、
    重复帧；全部下载并验证 PNG 尺寸后才一次性发布到 `strict/background`，失败不遗留
    半截成功帧。
  - `run_controlled_import` 改为先整体校验帧集、Mask、逐帧产品保护区隔离与输出尺寸，
    全部通过后经独立暂存目录一次性发布；失败不产出成功帧。
  - `verify_product_isolation` 使用连续 Mask（`mask > 1e-4`）并外扩 1 像素边缘容差，
    低但非零的 Mask 边缘像素也会被保护，不再仅用 `> 0.5` 阈值。
  - `protection_map` 增加显式安全语义检查：非空、同时声明 `product_region` 与
    `background_region`、二者不得相同；`anywhere`/`full_frame`/`product` 等不安全背景
    区域以及 `include_product` 等不安全标志直接拒绝。
  - `FULL_FRAME_EXTRACT_NON_PRODUCT` 保持 fail-closed，明确不能把 V2 整幅 H3 video graph
    改个名字当作 background-only。

- `tests/test_strict_background.py`
  - 合同：Run 快照缺失/字段不全、显式请求错配、不安全保护映射、全幅提取模式。
  - 受控导入：合法正例、缺 Mask、缺帧、越出受控根目录、低 alpha 边缘背景覆盖。
  - ComfyUI 独立工作流：合法帧收集、重复帧写盘前拒绝、越界帧、无效 PNG、无半截产物。
  - 证据绑定：跨 Job/owner/workflow hash/客户端 producer 拒绝。

## 实际命令与结果

- 编译检查：

  `python -m py_compile apps/api/productdirector_api/strict_background.py tests/test_strict_background.py`

  结果：退出码 0，无输出。

- 专项回归：

  `$env:PYTHONPATH = 'var\acceptance\2026-09-12-deepseek-remediation;' + (Get-Location).Path`

  `.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_strict_background -v`

  结果：Ran 16 tests，OK。

- 全量后端回归：

  `.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v`

  结果：Ran 243 tests in 69.237s，OK。

## 已知限制

- 本模块尚未接入 `main.py` 的 `execute_claimed_job` 主执行流；它是可独立验收的 Producer
  适配层，不表示已经贯通真实 Blender→受控背景→Strict 合成。
- `INDEPENDENT_BACKGROUND_WORKFLOW` 仅通过现有 `providers.comfyui` 的
  `submit/history/download` 接口执行，本地沙箱没有真实 ComfyUI 工作流节点/哈希/产物，
  因此没有完成现场进程或服务响应的真实性验证。
- ComfyUI 证据当前记录 workflow 文件 hash、提交记录和逐帧 hash，尚未记录模型/节点版本、
  Provider 健康信息与服务版本；这些需要现场只读接入时补齐。
- 独立工作流下载为逐帧内存暂存后统一发布，适合本地小样；真实 72 帧或 GB 级缓存需要改为
  磁盘暂存/流式批次，避免全量驻留内存。
- `FULL_FRAME_EXTRACT_NON_PRODUCT` 仍需现场校准产品 Mask/Alpha 隔离和合成像素锁定，
  本批不会将其升级为可信背景来源。

## 修改文件

- `apps/api/productdirector_api/strict_background.py`
- `tests/test_strict_background.py`
- `docs/reports/V3_CONTROLLED_BACKGROUND_DEEPSEEK_REMEDIATION.md`

## 下一步

- 现场只读核实真实独立背景工作流名称/版本/节点哈希，并将 `run_controlled_background_workflow`
  接入持租约 Worker 的 `execute_claimed_job` Strict 路径。
- 为真实 ComfyUI 执行补齐进程/服务证据、模型/节点版本与流式产物收集。
- 若使用全幅生成结果，完成非产品区域提取与可信 Mask/Alpha 隔离、合成产品像素锁定的现场校准。
- 在真实来源、分层、QA、人工批准全链证据完成前，继续保持验证样本资格，不打开发布门。
