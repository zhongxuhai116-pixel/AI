# Q01–Q06 DeepSeek 整改交付

日期：2026-09-12。执行端：DeepSeek。范围仅关闭 V3 的 Q01–Q06 整改及既定边界；不宣称整个 V3 通过，不启动 V4–V6。

## 1. 结论

- 原 6 个故障样例均被当前 CLI 拒绝；正常合成正例通过。
- 通道校验原始 `passes_valid` 真实/合成夹具恢复通过；掩码错位、中间损坏、缺法线分量、全零法线均拒绝。
- 既定边界：三组共同缺中间/尾帧、`--limit` 预览冒充全片、黑色产品空 Mask 均不再通过。
- 相关正式测试 25/25 通过；全量后端 177/177 通过。
- 前端 build 与 `test:sites` 本轮因 Windows 沙箱 `spawn EPERM` 无法运行，提权被用户拒绝，因此不能记为本次复验通过。

## 2. 修改文件

| 文件 | 修改 |
| --- | --- |
| `scripts/strict_composite.py` | 冻结帧计划、显式起始帧、背景 `+1` 映射、可信 Alpha、预检隔离、流式两遍写盘 |
| `blender/scripts/validate_fidelity_passes.py` | 精确帧集、通道 token 匹配、原始 beauty RGBA、有限值与法线分量 |
| `tests/test_strict_composite.py` | 修正错帧夹具；新增显式合同、背景映射、合法/非法空帧、真实 OpenEXR Alpha 正例 |
| `tests/test_validate_fidelity_passes.py` | 移除 mock 掩盖；新增真实 OpenEXR 正例、缺通道、非有限值、缺帧、重复帧 |
| `apps/api/requirements.txt` | 增加 `numpy==2.2.6`、`OpenEXR==3.4.15` |

未修改控制端两份独立验收报告；未修改原始 `var/acceptance` 历史脚本/输入/报告。新增证据目录：`var/acceptance/2026-09-12-deepseek-remediation/`。

## 3. 关键修复

### 3.1 可信帧数/起始帧/背景映射

- 完整 PASS 必须显式提供 `--plan`，或同时提供 `--expected-frames` 与 `--start-frame`。
- `--expected-frames=0` 只能推断，不能 `passed=true`。
- 背景逻辑帧 = 背景文件编号 + `--background-frame-offset`。
  - 真实云端样本：`product/mask frame_0001` 对应 `background bg_0000`，应传 `--background-frame-offset 1`。
- 映射后重复、缺号、多号、共同缺中间/尾帧均进入 `failures`。

### 3.2 可信 Alpha 与可见性

- RGB 全黑不再被当作“产品不存在”；可见性只依据可信 Alpha。
- 无可信 Alpha 时拒绝，不猜测全图可见。
- 可信 Alpha 有产品但 Mask 为空、Mask 有产品但 Alpha 不可见、轮廓 IoU < 0.995 均拒绝。
- 合法全空帧必须显式传 `--allow-empty-product-frames`。

### 3.3 失败不产出成功帧

- 第一遍只读预检，不缓存整帧像素、不写 PNG。
- 第二遍写暂存目录；全片通过才提升到 `--out`。
- 非预览失败删除暂存，`output_written=false`，不留下本次成功帧。

### 3.4 通道校验

- beauty 使用未降维原始 plane，校验 `(H,W,>=3)` 与有限值；保留真实分组 RGBA。
- `depth` 改为通道 token 匹配，避免被 `normal.Z` 误配。
- alpha/depth/normal 检查非有限值与形状；法线分量齐全且产品区模长在阈值内。
- 帧集按 `--frames` 与 `--start-frame` 精确比较，缺帧、重复帧、多帧均失败。

## 4. 实际命令与结果

```powershell
# 相关正式回归
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_strict_composite tests.test_validate_fidelity_passes -v
# Ran 25 tests in 0.204s
# OK

# 全量后端回归（沙箱内需测试用 tempfile 重定向）
$env:PYTHONPATH = 'var\acceptance\2026-09-12-deepseek-remediation;' + (Get-Location).Path
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -p 'test_*.py'
# Ran 177 tests in 32.474s
# OK
```

当前实现复跑控制端 `2026-09-12-deepseek-check-1` 原始输入（不写回原目录），合成用例补 `--expected-frames 3 --start-frame 1`：

| 用例 | 期望 | exit | 结论 |
| --- | --- | ---: | --- |
| composite_valid | PASS | 0 | 通过 |
| composite_missing_tail | REJECT | 1 | 拒绝 |
| composite_shifted_frame | REJECT | 1 | 拒绝 |
| composite_empty_mask | REJECT | 1 | 拒绝 |
| passes_valid | PASS | 0 | 通过 |
| passes_shifted_mask | REJECT | 1 | 拒绝 |
| passes_unsampled_fault | REJECT | 1 | 拒绝 |
| passes_missing_normal_components | REJECT | 1 | 拒绝 |
| passes_invalid_normal | REJECT | 1 | 拒绝 |
| boundary_common_gap | REJECT | 1 | 拒绝 |
| boundary_common_tail | REJECT | 1 | 拒绝 |
| boundary_preview_only | NOT_FULL_PASS | 1 | 预览不冒充全片 |
| boundary_black_product_empty_mask | REJECT | 1 | 拒绝 |

证据：`var/acceptance/2026-09-12-deepseek-remediation/summary.json`。

## 5. 前端

```powershell
cd apps/web
npm run build
npm run test:sites
```

两命令均因沙箱限制 `spawn EPERM` 失败；随后按交接说明申请沙箱外执行，被用户拒绝。因此前端 build 和 Sites worker 本轮**未复验通过**，不能引用为本次通过证据。

## 6. 已知限制

- 全量 unittest 在 broker 沙箱中需要 `sitecustomize.py` 把 `tempfile.mkdtemp` 重定向到工作区；这不是业务依赖，控制端标准环境可直接跑原始命令。
- 未启动 GPU，未对云端真实 72 帧 EXR 做本轮端到端 CPU 复验；已加入真实 OpenEXR 合成/校验正例作为本地证据。
- 真实背景帧起始编号为 0 的映射已支持，但需要调用方显式传 `--background-frame-offset 1`；旧调用若不传计划或偏移会被拒绝，这是合同收紧而不是兼容性破坏。
- 前端命令因用户拒绝提权未执行，等待控制端在可执行环境复验。

## 7. 下一步

1. 控制端在隔离目录对既有真实 EXR 做 CPU 复验，使用 `--start-frame 1 --frames <N>` 与背景显式映射。
2. 前端 build / `test:sites` 在沙箱外补验。
3. 通过后再派工 V3-06 双产品检测、V3-07 QA、V3-08 版本失效联动等，不跳阶段。
