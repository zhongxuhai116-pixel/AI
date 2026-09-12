# V3 Strict 输入来源注册/冻结整改报告（DeepSeek）

2026-09-12。本批把 Strict Run 的输入身份从“执行时目录快照”推进为“执行前冻结的来源合同”，并新增来源一致性校验。这不是完整 V3-02/03，也未打通真实 Blender→背景工作流端到端。

## 本批范围

- 新增 `POST /api/v1/runs/{run_id}/strict-source-manifest`：仅在 Run 仍为 `QUEUED`（领取/执行前）时冻结来源。
- 来源 manifest 冻结到 `runs.strict_source_manifest_json` 与 `strict_source_manifest_sha256`。
- 冻结内容包括：`plan_id/plan_contract_id/product_version_id/asset_id/owner_id/job_id`、逐文件 sha256、`fidelity_complete=false`、`input_trust=REGISTERED_LOCAL_SAMPLE`、render_evidence 与 background_workflow。
- 新增 `scripts/validate_strict_sources.py`：校验 passes/product/mask/background 帧集合同、缺 mask 结构化失败、Beauty 与产品层在产品 Mask 腐蚀 2px 核心区线性空间一致性。
- 运行时若存在已冻结来源，先逐项复核身份/hash/未注册文件，再跑来源校验器；任一失败进入 `QA_REJECTED`。
- `FidelityPolicyRequest` 新增 `background_workflows` 批准列表并写入策略 payload；注册背景层时必须在已冻结 STRICT 策略的批准列表中。

## 状态语义

- 本批注册来源仍为本地受控小样，`fidelity_complete=false`、`input_trust=REGISTERED_LOCAL_SAMPLE`。
- 发布资格门只认 `STRICT_VERIFIED_INPUT_TRUST=VERIFIED_RENDER_SOURCE`；`REGISTERED_LOCAL_SAMPLE`、`PRESEEDED_LOCAL_SAMPLE` 均不可发布/继承 Strict PASS。
- `render_evidence.producer` 与 `background_workflow` 请求字段是登记声明，不能提升资格；真实可信来源需后续受控产出证明并显式升级为 `VERIFIED_RENDER_SOURCE`。

## 背景工作流合同

- STRICT 仍禁止 `allowed_operations` 含 `background_generation`（不允许在产品区域内生成）。
- `background_workflows` 表示批准用于**非产品区域**的背景来源工作流版本；注册时校验 name/version/workflow_hash 与输入保护区映射均与策略 payload 批准列表一致。
- 本批只批准 `local.background@1.0.0` 测试工作流；H3/ComfyUI 真实工作流 hash 与保护区域映射仍需后续受控产出后接入。

## 修改文件

- `apps/api/productdirector_api/main.py`
- `scripts/validate_strict_sources.py`（新增）
- `tests/test_v3_strict_runtime.py`
- `tests/test_fidelity_policy.py`
- `db/schema.postgres.sql`
- `docs/reports/V3_STRICT_SOURCES_DEEPSEEK_REMEDIATION.md`（本报告）

## 实际命令与结果

专项（Strict runtime + FidelityPolicy 合同）：

```powershell
$env:PYTHONPATH = 'var\acceptance\2026-09-12-deepseek-remediation;' + (Get-Location).Path
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_v3_strict_runtime tests.test_fidelity_policy -v
```

结果：`Ran 25 tests ... OK`。

相关组合：

```powershell
$env:PYTHONPATH = 'var\acceptance\2026-09-12-deepseek-remediation;' + (Get-Location).Path
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_job_control tests.test_v3_run_fidelity_binding tests.test_v3_strict_runtime -v
```

结果：该组合未在本轮重复运行；上一轮已通过 47 项，本轮全量覆盖。

全量后端：

```powershell
$env:PYTHONPATH = 'var\acceptance\2026-09-12-deepseek-remediation;' + (Get-Location).Path
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -p 'test_*.py'
```

结果：`Ran 207 tests ... OK`。

## 行为正负例

正例：

- 本地 144 帧真实 EXR/PNG 层经 `POST /runs/{id}/strict-source-manifest` 冻结后，`execute_job` 真 Job 运行通过，状态保持 `VERIFICATION_PASSED`。
- metadata 显示 `source_manifest`、`source_report`、`input_trust=REGISTERED_LOCAL_SAMPLE`、`fidelity_complete=false`。
- 背景层工作流必须出现在已冻结 STRICT 策略 `background_workflows` 中。
- `FidelityPolicyRequest.background_workflows` 通过真实 API 创建并冻结进 policy payload，注册正例可创建可读取的批准合同。

负例：

- 缺 mask 帧：注册 409，报告结构化 `mask 缺少帧`，不再 KeyError。
- 替换已注册 background 文件：执行进入 `QA_REJECTED`，错误含“替换”。
- Beauty 与产品层不一致：注册 409，错误含“不一致”。
- Run 已 `RUNNING`（被领取）后注册：409，错误含“领取/执行前”。
- 注册背景工作流未出现在已冻结 STRICT 策略批准列表：409，错误含“背景工作流未出现”。
- 注册背景工作流 hash 相同但输入保护区映射与批准合同不一致：409，错误含“背景工作流未出现”。
- STRICT 策略 API 仍拒绝 `allowed_operations=background_generation`：422；同时允许 `background_workflows` 作为非产品区域背景来源合同，不放开产品区域生成。
- 跨 owner 注册：403。
- 产品版本变化后旧 Run 注册：409，错误含“旧保真审批已失效”。
- 缺 depth、缺 background 帧、错 hash 等既有负例保持通过。

## 剩余边界 / 未完成项

- 未接入真实 Blender 多通道渲染产物自动注册；本批文件由受控测试/本地目录提供。
- 未接入真实 H3/ComfyUI 背景工作流版本、节点/模型 hash、保护区域输入映射的受控产出证明。
- 未把来源 manifest 与渲染/背景 producer 的进程级证据（命令行、进程 hash、退出码）绑定。
- 未实现 V3-03 阴影/反射/遮挡分层来源合同与 V3-04 QA 阈值集、人工批准 hash。
- 本地小样继续使用 8×8 合成数据，只验证控制流，不代表真实渲染质量或性能。
- 前端 `npm run build`/`npm run test:sites` 未在本轮重跑：沙箱内 Node/esbuild 子进程 `spawn EPERM`，升级执行被拒绝；本轮未修改前端文件。
