# V3-03 Blender Strict 产品/Mask 输出整改报告

日期：2026-09-12
代理：deepseek-pro（Broker worker）
范围：让 `blender/scripts/render_product.py` 在受控 `--passes` 路径自动生成 Strict 合成所需的逐帧 `product` 层与可信连续 `mask`，并保证首遍五通道 Beauty/Alpha 与 product/mask 来自同一产品可见性。

## 结论

- `--passes` Strict 首遍现在隐藏 Studio Floor 等所有非产品 Mesh，并保持 `film_transparent=True`、`color_mode=RGBA`；五通道 Beauty/Alpha 不再混入地面/阴影。
- 非 `--passes` V1/V2 legacy 路径保持原行为：不透明 RGB、保留 Studio Floor。
- 第二遍只渲染产品网格，输出 `output/product/frame_*.png`；`output/mask` 与 `output/passes/mask` 由同一帧 product RGBA 逐字节复制，避免二次渲染导致轮廓漂移。
- 产品 PNG 语义：8-bit RGBA、sRGB 编码、straight alpha。Strict compositor 的 `read_linear_rgb` 会 sRGB→linear，`read_product_alpha` 读取直通 alpha；`strict_composite.read_mask` 的 `>0.5` 阈值只用于二值保护区，低 alpha 边缘仍由连续 `product_alpha` 与边缘校验处理。
- 仅静态/离线 mock 证明控制流；**未声称真实 Blender 出片语义已在现场验证**。

## 修改文件

- `blender/scripts/render_product.py`
  - `add_lighting()` 返回 Studio Floor 对象。
  - 新增 `_hide_non_product_meshes()` / `_restore_render_visibility()`。
  - 原 `render_mask_pass()` 改为 `render_strict_product_and_mask()`：输出 product、复制同帧 mask 与 passes/mask，并校验帧数等于 `args.frames`。
  - `main()` 在 `--passes` 下首遍隐藏非产品 Mesh、开启透明和 RGBA；`finally` 恢复可见性；首遍后调用 Strict product/mask 第二遍。
- `tests/test_render_product_strict_contract.py`
  - 新增 5 项 AST/源码级契约检查，不导入 `bpy`。
- `tests/test_v3_background_integration.py`
  - 离线 mock Blender 改为写出全尺寸五通道 EXR，并把同帧 product 复制为 mask/passes/mask。
  - 新增缺少 Strict product/mask 的受控渲染负例，断言 `QA_REJECTED` 且不生成 product/mask 目录。
  - 正例增加 product 与 mask 文件字节一致性断言。

## 实际命令与结果

### 语法检查

```powershell
.\.venv\Scripts\python.exe -X utf8 -m py_compile blender/scripts/render_product.py apps/api/productdirector_api/main.py apps/api/productdirector_api/strict_background.py tests/test_v3_background_integration.py
```

结果：通过，无输出。

### 脚本契约检查

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_render_product_strict_contract -v
```

结果：`Ran 5 tests ... OK`。

### 背景集成专项

本沙箱中标准 `unittest` 调用在 `tempfile.mkdtemp()` 后创建子目录触发 `WinError 5`（`tempfile.mkdtemp` 创建目录的 ACL 被当前受限环境拒绝对子目录继续写）。为在本地得到可复验结果，使用不修改被测代码的临时测试包装器，把 `tempfile.mkdtemp` 替换为等价的 `os.makedirs` 随机目录：

```powershell
$env:TMP = Join-Path (Get-Location) 'var/tmp_wrap'
New-Item -ItemType Directory -Force -Path $env:TMP | Out-Null
.\.venv\Scripts\python.exe -X utf8 -c "... tempfile.mkdtemp=lambda ... os.makedirs ...; unittest ..."
```

背景集成结果：`Ran 5 tests ... OK`。

### 控制端指出的三模块联合回归

`tests.test_render_product_strict_contract` + `tests.test_v3_strict_runtime` + `tests.test_v3_background_integration`：

结果：`Ran 44 tests ... OK`。

### 全量后端回归

同一临时目录包装器下运行 `unittest.TestLoader().discover('tests')`：

结果：`Ran 253 tests ... OK`。

## 正负例覆盖

- 正例：受控 Blender mock 输出全尺寸五通道 + product/mask 同帧，背景受控导入合成后为 `VERIFICATION_PASSED`，`release_eligible=false`，`input_trust=CONTROLLED_IMPORT_VERIFICATION_SAMPLE`，且 product/mask 字节一致。
- 负例：受控 Blender mock 仅输出五通道、漏掉 Strict product/mask，Run 进入 `QA_REJECTED`，无 product/mask 目录，未退回预置路径。
- 负例：独立背景 workflow 文件缺失时不自动回退受控导入。
- 负例：受控导入跨 Job 复用目录被拒。
- 静态负例语义：`main` 的非 passes 分支仍为不透明 RGB；Strict 分支隐藏 floor 且透明。

## 已知限制

- 本机没有可用 Blender，无法真实执行 `bpy.ops.render.render`；以上 product/mask 同帧、透明边缘、黑色产品/反射/logo 材质保真等均为代码契约与 mock 控制流，不是真实 EXR/PNG 现场证明。
- 真实 Blender 5/4 的合成器输出目录与分辨率仍需控制端在隔离目录用既有真实资产做 CPU 只读复验。
- 真实 H3/ComfyUI 背景工作流仍缺，当前背景正例是 `CONTROLLED_IMPORT` 验证样本，不可发布。
- 本轮未运行前端 build/sites，因改动仅在后端渲染脚本和测试；如需我可在下一批补跑。

## 下一步

- 由控制端在真实 Blender 上复验：72 帧真实 GLB 的 Strict 首遍透明产品、五通道 beauty/alpha 尺寸与 product/mask 一致性。
- 继续接入真实系统受控背景 workflow 与 H3/ComfyUI 现场接口证据，再推进 V3-03/04 全链闭环。
- 保持 `release_eligible=false` 与 `VERIFICATION_PASSED`，未完成真实来源/QA/人工批准前不得升格。
