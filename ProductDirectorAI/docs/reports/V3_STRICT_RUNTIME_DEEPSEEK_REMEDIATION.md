# V3 Strict Run 运行时闭环整改报告（DeepSeek）

2026-09-12。本批在 Strict Run 运行时闭环上补齐状态语义与发布资格门：预置输入的验证小样不再冒充完整 Strict 成果。这不是完整 V3 Strict Fidelity，也不宣称 Blender→背景→合成端到端。

## 本批纠偏

- Strict Run 的预置输入小样执行成功后，状态从 `SUCCEEDED/ARTIFACT` 改为 `VERIFICATION_PASSED/VERIFIED_SAMPLE`，与可发布完成状态明确区分。
- `VERIFICATION_PASSED` 加入 `TERMINAL_JOB_STATUSES`；迟到更新、取消与重试均按终态处理。
- `GET /api/v1/jobs/{job_id}` 返回 `release_eligible` 与 `release_status`。
- 新增 `GET /api/v1/jobs/{job_id}/release-status` 作为下游聚合/发布前的资格门。
- `complete_leased_job` 对带 `fidelity_snapshot_json` 的 Run 拒绝 legacy complete，防止远程 Worker 绕过 Strict runtime closure 直接置 `SUCCEEDED`。
- 取消接口改按 `TERMINAL_JOB_STATUSES` 判断，`QA_REJECTED` 与 `VERIFICATION_PASSED` 不再进入取消流程。

## 状态与权限门

- `VERIFICATION_PASSED` 只表示本地小样通过真实通道校验与合成 QA，不代表已取得可继承的 Strict PASS。
- 该状态的 `output_path` / `manifest_path` 仅用于非商业验证预览；`job_video` / `job_manifest` 允许读取小样证据。
- 发布资格规则 `job_release_eligibility()`：
  - legacy V1/V2 成功任务保持兼容，`SUCCEEDED` 且有产物仍视为基础预演可发布。
  - Strict Run 只有在状态为 `SUCCEEDED`、manifest 为 `strict_mode=true`、`fidelity_complete=true` 且来源不是 `PRESEEDED_LOCAL_SAMPLE` 时才可能 `eligible=true`。
  - `VERIFICATION_PASSED` 恒为 `eligible=false`、`verification_only=true`，reason 明确说明不可发布/不可继承 Strict PASS。
- 现阶段下游发布接口尚不存在，本次未宣称已验证真实发布阻断；资格 API 对验证小样强制返回 `eligible=false`。未来 V6 package/publish 入口必须消费同一个 `job_release_eligibility()`，不能只检查 `SUCCEEDED`。

## 输入身份合同与限制

- 运行时对 `strict/passes`、`strict/product`、`strict/mask`、`strict/background` 逐文件计算 sha256，写入 `strict/input_manifest.json` 与 metadata manifest。
- 该 manifest 只证明“执行时目录内文件当前内容”，不能证明上游来源、是否确由同一已批准产品版本生成。此为**可信预置输入**纵向切片，`fidelity_complete=false`、`input_trust=PRESEEDED_LOCAL_SAMPLE` 已明确记录。
- 输入 manifest hash 尚未写回 Run 快照（预置输入发生在 Run 创建之后）；后续应通过分层上传/注册接口把逐文件 hash 与产品版本身份冻结到 Run。
- 真正的 Blender 五通道来源绑定、背景工作流输入映射与 V3-03/04 完整闭环仍未完成。

## 修改文件

- `apps/api/productdirector_api/main.py`
- `apps/web/src/App.jsx`
- `tests/test_v3_strict_runtime.py`
- `docs/reports/V3_STRICT_RUNTIME_DEEPSEEK_REMEDIATION.md`（本报告）

## 实际命令与结果

专项：

```powershell
$env:PYTHONPATH = 'var\acceptance\2026-09-12-deepseek-remediation;' + (Get-Location).Path
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_v3_strict_runtime -v
```

结果：`Ran 8 tests ... OK`。

相关组合：

```powershell
$env:PYTHONPATH = 'var\acceptance\2026-09-12-deepseek-remediation;' + (Get-Location).Path
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_job_control tests.test_v3_run_fidelity_binding tests.test_v3_strict_runtime -v
```

结果：`Ran 40 tests ... OK`。

全量后端：

```powershell
$env:PYTHONPATH = 'var\acceptance\2026-09-12-deepseek-remediation;' + (Get-Location).Path
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -p 'test_*.py'
```

结果：`Ran 197 tests ... OK`。

## 行为正负例

正例：

- 可信预置 144 帧真实五通道 EXR + mask/product/background 小样，经 `execute_job` 真 Job 执行后状态为 `VERIFICATION_PASSED`，不进入 `SUCCEEDED`；manifest 含 `strict_mode=true`、冻结引用 hash、passes/composite report、input_manifest 与逐文件 hash。
- `GET /api/v1/jobs/{id}` 对验证小样返回 `release_eligible=false`、`verification_only=true`。
- `GET /api/v1/jobs/{id}/release-status` 对验证小样返回 `eligible=false`，reason 含“不可发布”。
- legacy V1/V2 成功任务仍由 `SUCCEEDED` 路径处理，不受 Strict 状态门影响。

负例：

- 缺 depth 通道：`QA_REJECTED`，error 含 `depth`。
- 合成输入缺 background 帧：`QA_REJECTED`，error 含 `background`。
- 冻结审核 hash 被改：`QA_REJECTED`，error 含 `hash`。
- 产品版本变化后旧 Run 执行：`QA_REJECTED`，error 含 `旧保真审批已失效`。
- 验证小样调用 `/retry`：HTTP 409，状态保持 `VERIFICATION_PASSED`。
- Strict Run 被 legacy worker complete 接口完成：HTTP 409，状态不进入 `SUCCEEDED`，`output_path` 不残留。


## 前端状态呈现

- `apps/web/src/App.jsx` 已增加 `VERIFICATION_PASSED` 中文状态：`验证通过·不可发布`，并同步显示 `release_status.reason`。
- 技术检查步骤显示 `验证通过·不可发布`，而非 `已通过`。
- 验证小样提供 `下载验证 MP4` 与 metadata 下载入口，并标注 `不可发布`，避免把预览当完整 Strict 成果。
- 本地沙箱运行 `npm run build` 与 `npm run test:sites` 均因 `spawn EPERM` 被拦截；请求升级运行被用户拒绝。前端改动已做静态语法/结构核对，但需控制端在真实环境复跑这两个命令。

## 剩余边界 / 未完成项

- 未接入真实 Blender 多通道渲染器把 pass 来源与 Run 的产品版本/资产身份绑定。
- 未实现背景工作流输入映射、阴影/反射/人物遮挡分层来源合同（V3-03）。
- 未实现 QA 阈值集版本化与人工批准 hash 闭环（V3-04）。
- 输入 manifest hash 尚未写入 Run 快照。
- 本地小样使用 144 帧合成 8×8 数据，只用于验证纵向控制流，不代表真实渲染质量或性能。
