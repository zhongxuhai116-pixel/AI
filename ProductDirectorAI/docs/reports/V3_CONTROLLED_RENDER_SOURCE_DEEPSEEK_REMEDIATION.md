# V3 受控 Blender Producer 证据链整改报告（DeepSeek）

2026-09-12。本批把 Strict 产品五通道来源从“客户端/目录自报”推进为“由持有租约的受控 Worker 发起并冻结的 Blender 进程证据”，但仍保持 `VERIFICATION_PASSED` 且 `release_eligible=false`。这不是真实 Blender 端到端验收。

## 本批范围

- 新增 `run_controlled_blender_passes()`：同一 Job 的受控 Worker 在领取租约后，由系统构造 Blender 命令、读取真实可执行文件/版本、渲染脚本与产品资产 hash，记录命令、退出码、Pass 语义、逐文件 sha256。
- 启动前先复核冻结合同：`plan_contract_id/product_version_id/product_review_id/fidelity_policy_id` 均不得漂移，产品资产文件 hash 与冻结资产一致，计划快照 hash 与合同一致；任一失败进入 `QA_REJECTED`。
- 渲染产物经 `blender/scripts/validate_fidelity_passes.py` 真实读取并校验五通道，失败不登记来源。
- 证据冻结到 `runs.controlled_render_evidence_json` / `controlled_render_evidence_sha256`。
- `verify_controlled_render_evidence()` 用当前磁盘文件复核证据，缺文件、多出未登记文件、替换文件、资产 hash 漂移均结构化失败。
- `pass_semantics` 只记录 Blender 输出中实际解析到的通道；`pass_layout` 与通道有效性以 `passes_report` 为事实来源，不在解析失败时回退硬编码五通道。

## 状态语义

- 受控产品五通道的 `input_trust=CONTROLLED_BLENDER_PRODUCT`，不是 `VERIFIED_RENDER_SOURCE`。
- `fidelity_complete=false`；任务状态保持 `VERIFICATION_PASSED`，仅表示验证小样完成，不可发布、不可继承 Strict PASS。
- 客户端 `strict-source-manifest` 的 `render_evidence.producer` 自报仍只能是 `REGISTERED_LOCAL_SAMPLE`，不能升级到 `CONTROLLED_BLENDER_PRODUCT`。
- 背景 H3/ComfyUI 闭环与 V3 全量验收未完成前，发布资格门继续拒绝。

## 修改文件

- `apps/api/productdirector_api/main.py`
- `db/schema.postgres.sql`
- `tests/test_v3_strict_runtime.py`
- `docs/reports/V3_CONTROLLED_RENDER_SOURCE_DEEPSEEK_REMEDIATION.md`（本报告）

## 实际命令与结果

专项：

```powershell
$env:PYTHONPATH = 'var\acceptance\2026-09-12-deepseek-remediation;' + (Get-Location).Path
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_v3_strict_runtime -v
```

结果：`Ran 23 tests ... OK`。

全量后端：

```powershell
$env:PYTHONPATH = 'var\acceptance\2026-09-12-deepseek-remediation;' + (Get-Location).Path
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -p 'test_*.py'
```

结果：`Ran 213 tests ... OK`。

前端 `npm run build` / `npm run test:sites` 未在本轮重跑；本轮未修改前端文件，且沙箱内 Node/esbuild 子进程此前出现 `spawn EPERM`。

## 行为正负例

正例：

- 受控 Worker（`local-background`）持有租约后启动 Blender 产品五通道，`execute_job` 真 Job 路径返回 `VERIFICATION_PASSED`。
- metadata 显示 `controlled_render_evidence.producer_control=CONTROLLED_WORKER`、Blender 版本、渲染脚本 hash、资产/计划 hash、命令、退出码、逐文件 hash 与 `passes_report`。

负例：

- 产品资产文件 hash 与冻结资产不一致：`QA_REJECTED`，错误含“资产文件 hash”。
- 计划更新后旧合同失效：`QA_REJECTED`，错误含“旧保真审批已失效”。
- Blender 渲染进程非零退出：`QA_REJECTED`，错误含“渲染进程失败”。
- 渲染未产出五通道：`QA_REJECTED`，错误含“五通道校验未通过”。
- 已冻结证据中的产物被替换：`verify_controlled_render_evidence()` 返回“已被替换”。

## 已知限制 / 剩余项

- 本机未安装 Blender；本批没有真实 Blender 端到端产物，只用受控假进程执行离线控制流。报告不把该正例称为真实渲染证明。
- 未接入真实 H3/ComfyUI 背景工作流、保护区域输入映射与受控产出证明，因此仍不可发布。
- 未实现渲染进程级认证的 Worker 进程/命令行签名（当前依赖租约身份与系统侧发起），真实多 Worker 场景还需校准。
- 未接入 V3-03 阴影/反射/遮挡分层合同与 V3-04 QA 阈值集、人工批准 hash。
