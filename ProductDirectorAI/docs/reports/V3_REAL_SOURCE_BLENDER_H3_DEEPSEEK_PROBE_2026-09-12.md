# V3-02/03 真实 Blender / 独立 H3 背景只读探针与兼容加固（DeepSeek）

2026-09-12。本批不启动 Blender/GPU，不调用付费服务；只做已有证据只读分类、Blender 5.2 compositor 隔离加固、最小 CPU 产物探针与正负测试。**本报告不代表真实 Blender 或 H3 已通过。**

## 结论

- 云端旧 `bg_h3/` 与历史 ComfyUI graph 只能列为历史样本，不能自动成为当前 Strict 受控 Producer 或可发布来源。
- 本地归档的 V2 工作流均被分类为 `REJECTED_FOR_STRICT_BACKGROUND`：含产品替换节点、整帧 H3、参考视频/图像，仅 prompt 写 “No product” 不足以升级。
- `blender/scripts/render_product.py` 的第二遍 product/mask 渲染现会显式解绑 Blender 5 `scene.compositing_node_group`，若解绑失败则 fail-closed；渲染后按 mtime/size/sha256 复核五通道未被重写。
- 新增 `scripts/verify_strict_blender_evidence.py`，可在云端用 CPU 只读已有 EXR/PNG 验证帧集、尺寸、有限值、Alpha 与 product/mask 对应；它不是 Blender 生成证据。
- `release_eligible=false` 与 `VERIFICATION_PASSED` 语义不变；真实 Blender 5.2.1 现场复验仍未由本批完成。

## 实际命令与结果

- 编译检查：
  - `python -X utf8 -m py_compile apps/api/productdirector_api/strict_background.py blender/scripts/render_product.py scripts/verify_strict_blender_evidence.py tests/test_strict_background.py tests/test_render_product_strict_contract.py tests/test_render_product_compositor_isolation.py`
  - 结果：0 错误。
- 只读 workflow 分类探针：
  - 结果写入 `var/acceptance/2026-09-12-v3-real-source-probe/workflow_classification.json`。
  - 三个本地归档 workflow 均返回失败，其中：
    - `16-原视频产品替换-3D-Blender-H3.json` sha256 `be7bf01fcd1775e58dc8cbd6da04d8da735c9dc68cc2754f6178040b224ba858`：含 `ProductBlenderRender`、`MiniMaxH3ReferenceToVideo`、`ProductReferenceShot`、`SaveVideo`。
    - `16-原视频产品替换-3D-Blender-H3-加速版.json` sha256 `b1ac645daf1ba5966c747da5bedf7ad711a059c8f44a67912638d4146ae04f4f`：含 `ProductBlenderCached`、`MiniMaxH3ReferenceToVideo`。
    - `图生视频-关图就是文生.json` sha256 `7eba912f13b2fafc547223f5012ed9cb418ac5c52e4891372c8a8c184218213a`：含 `MiniMaxH3SpeedCache`，新分类器已拒绝。
- 最小 CPU 产物探针自检：
  - `python -X utf8 scripts/verify_strict_blender_evidence.py --self-test`
  - 结果：`status=PASS`，2 帧合成样张；仅证明 OpenEXR/Pillow 读法，不证明真实 Blender 产物。
- 专项测试：
  - 命令为 `python -X utf8 -m unittest ...` 加载 `tests.test_v3_background_integration`、`tests.test_strict_background`、`tests.test_render_product_strict_contract`、`tests.test_render_product_compositor_isolation`、`tests.test_strict_composite`、`tests.test_validate_fidelity_passes`。
  - 结果：63/63 PASS。
- 全量后端回归：
  - 沙箱环境因 Windows `tempfile.mkdtemp` 子目录权限问题，使用项目内 `var/_unittest_tmp` 的等价 patch 后执行 `unittest discover -s tests -p 'test_*.py'`。
  - 结果：280/280 PASS，182.830s。
  - 控制端标准环境可直接运行 `python -X utf8 -m unittest discover -s tests -p 'test_*.py' -q` 复验。

## 修改文件

- `blender/scripts/render_product.py`
  - 第二遍前调用 `_suspend_pass_output_nodes`，Blender 5 下要求 `scene.compositing_node_group` 实际变为 `None`，否则拒绝渲染；预检发生在删除旧帧和隐藏对象之前。
  - 渲染前后对 `passes`（排除 `mask`）记录 `st_mtime_ns / size / sha256`，第二遍后复核；任一变化即 fail-closed。
  - `_suspend_pass_output_nodes` 在自身解绑失败时异常安全恢复 scene 原状态，保留原始异常；恢复失败仅记录且不吞原始异常。
  - 保留 V1/V2 非 `--passes` legacy 行为：不透明 RGB、含 Studio Floor。
- `apps/api/productdirector_api/strict_background.py`
  - `verify_background_workflow_graph` 现在对 `MiniMaxH3ReferenceToVideo` 和 `MiniMaxH3SpeedCache` 整帧 H3 图一律拒绝；不会因存在 `ImageCompositeMasked` 等节点名自动放行。
  - 产品替换节点 `ProductBlenderRender/Cached` 仍直接拒绝。
- `scripts/verify_strict_blender_evidence.py`（新增）
  - 只读检查 beauty/alpha/product/mask 的帧集、尺寸、有限值、Alpha 一致性及核心区域 RGB 差异。
  - 输出 JSON，含明确 limitations；`--self-test` 仅生成合成 OpenEXR/PNG。
- `tests/test_render_product_compositor_isolation.py`（新增）
  - 伪 Blender 5 scene：`compositing_node_group` setter 被忽略、`use_nodes` 可置 False，仍必须在第二遍 render 前失败；同时断言旧产物不删除、对象可见性不改变。
  - `_restore_pass_output_nodes` 恢复失败必须抛出；新增 setter 直接抛异常时的 `_suspend` 异常安全测试。
- `tests/test_strict_background.py`
  - 新增产品替换、`MiniMaxH3ReferenceToVideo`、`MiniMaxH3SpeedCache`、简单非 H3 图与空图分类正负例。
- `tests/test_render_product_strict_contract.py`
  - 新增第二遍解绑、恢复及 pass 文件不变校验的静态断言。
- 证据目录 `var/acceptance/2026-09-12-v3-real-source-probe/workflow_classification.json`（新整改输出，不覆盖历史证据）。

## 云端历史 H3 证据判定

控制端只读提供的历史 ComfyUI job graph canonical sha256：
`4c69e764665dc28080cef82333aa4c19d5c069c1cb629d00defe7de0f5be90c5`。

该 graph 仍含 `LoadVideo(file=reference.mp4) -> GetVideoComponents -> MiniMaxH3ReferenceToVideo`，以及 `LoadImage(image=ref_0000.png) -> ImageCrop`。它使用参考视频+参考图，不是无参考/受控仅背景工作流；prompt 写 “No product” 不能证明产品保护。当前判定：

- 历史 H3 背景帧：`HISTORICAL_SAMPLE`，不进入当前 Strict 受控 Producer 的发布证据。
- 该 graph：`REJECTED_FOR_STRICT_BACKGROUND`，不因已有产物或 workflow hash 自动升格。
- 需要独立核实且节点/model/hash 明确的 background-only workflow 才可考虑 `INDEPENDENT_BACKGROUND_WORKFLOW`；未核实前 ComfyUI 路径 fail-closed。

## 已知限制

- 本批早期未在云端执行 Blender 5.2.1 真实最小 CPU 探针；随后已执行一次隔离最小校准，见文末“真实最小校准”章节。
- 本机 `blender.exe` 为 5.1.1，未用于证明 5.2.1 行为；`tests/test_render_product_compositor_isolation.py` 是伪 scene，不是真实 Blender 出片证据。
- `verify_strict_blender_evidence.py --self-test` 使用合成 OpenEXR/PNG，只证明读取逻辑，不证明现场文件来源。
- 旧 `pd-v305-layers-evidence/SUMMARY.md` 中 Beauty/mask 覆盖风险已通过代码 fail-closed 与 hash/mtime 后验降低，但真实 5.2.1 现场仍未复验。
- 全量回归在沙箱内使用 tempfile monkeypatch；标准环境结果以控制端复验为准。

## 已知限制（补充）

- 第二遍 `product/mask/passes/mask` 已使用原子暂存并做双故障/无初始目标回滚；异常不会写成功标记或 release 资格。
- `_suspend_pass_output_nodes` 在解绑失败时尽力恢复；若 Blender 恢复 setter 也持续失败，场景状态仍可能污染，脚本会打印 `DIRECTOR_COMPOSITOR_SUSPEND_RESTORE_FAILED` 并保留原始异常，要求现场重开 Blender 会话。

## 下一步

- 控制端在隔离目录运行：
  `python scripts/verify_strict_blender_evidence.py --beauty-dir <...> --alpha-dir <...> --product-dir <...> --mask-dir <...> --expected-frames 72 --width 540 --height 960 --out /tmp/blender_probe.json`
- 现场确认 Blender 5.2.1 中 `scene.compositing_node_group = None` 实际生效，并复跑第二遍后 hash/mtime 检查。
- 若需真实背景来源，先冻结并核实独立 background-only workflow 的节点/model/hash/protection_map；历史 V2 graph 不得复用。


## 本批补充：原子产物回滚与 background-only H3 schema 只读核验

2026-09-12 后续同一工作树，不扩大上一批边界。真实 Blender/H3 GPU 生成仍未执行，`release_eligible=false` 不变。

### `_replace_output_dirs` 双故障与无初始目标回滚

- 当前 pair 的 `stage.rename(target)` 与 `backup.rename(target)` 同时失败时，`recovery_hints` 会记录当前 pair 的 target/backup 及两个原始异常；若此前已有 pair 回滚也失败，顶层异常会把当前 pair 证据一并输出，备份文件保留。
- 已修复回滚路径：先前 pair 原本没有旧 target 时，回滚会先删除已切换到目标位置的新产物，再仅在备份存在时恢复，避免留下部分新文件。
- 新增/强化测试：
  - `test_current_pair_double_failure_reports_and_keeps_backup`
  - `test_current_pair_double_failure_is_in_top_level_error_when_prior_rollback_also_fails`
  - `test_rollback_removes_new_target_when_no_old_backup_existed`
- 命令：`python -X utf8 -m unittest tests.test_render_product_compositor_isolation tests.test_render_product_strict_contract -q`
- 结果：15/15 PASS。

### 云端 ComfyUI `/object_info` 只读 schema 核验

- 新证据目录：`var/acceptance/2026-09-12-v3-background-h3-schema-probe/`
  - `comfyui_object_info_snapshot.json`：13 个候选图节点的脱敏只读 `/object_info` 快照。
  - `summary.json`：采样时间、节点列表、`raw_file_sha256`（snapshot 磁盘字节 SHA256）、`canonical_json_sha256`（canonical JSON 重排后 SHA256）、模型/节点源码文件 SHA256、候选图状态；两者不同属预期，不是审计误判。
- 只读核验结论：
  - `MiniMaxH3ImageToVideo` required 为 `clip, vae, prompt, width, height, length`；`first_frame/last_frame` 均为 optional，因此候选图不接 keyframe 的结构合法。
  - 节点源码 `comfy_extras/nodes_minimax_h3.py` 中 `MiniMaxH3ImageToVideo.execute` 在 `first_frame=None, last_frame=None` 时仍会创建空 AV latent 并 tokenize prompt；这是静态源码事实，不等同于该 ref2va 权重在无参考 prompt-only 模式下的真实生成质量证明。
  - 候选图所有输入键、链接输出索引与目标输入类型已与快照 schema 静态校验一致；`CLIPLoader.type` 使用 `minimax`，`SaveImage.images` 接收 `VAEDecode` 输出 `IMAGE`。
- `BACKGROUND_ONLY_H3_RUNTIME_STATUS = NOT_VERIFIED`，`BACKGROUND_ONLY_H3_SCHEMA_STATUS = SCHEMA_VERIFIED_ONLY`。
- `run_background_only_h3_producer()` 在未获得现场独立工作流运行证据时 fail-closed，不会提交 ComfyUI；`tests/test_strict_background.py` 已覆盖 `NeverSubmitClient` 负例。
- 相关命令：
  - `python -X utf8 -m unittest tests.test_strict_background -q`（沙箱 tempfile patch 后）26/26 PASS。
  - 全量后端 `var/_run_full_tests.py`：289/289 PASS，186.296s。

### 仍不视为已验证

- 早期候选 H3 graph 只做静态 schema 校验；文末“真实最小校准”已执行 1 个 64x64 最小 job，但背景内容/规格/隔离仍 FAIL/NOT_VERIFIED。
- 模型 `minimax_h3_ref2va_pruned_int8_convrot.safetensors` 在 prompt-only 模式产出符合 Strict 背景隔离要求的视频，仍未由本批证明。


## 真实最小校准（本批新增）

2026-09-12，在云端隔离目录 `/home/ubuntu/pd-v3-calibration-20260912-01` 执行，不改部署服务，不提交/推送/部署。

### Blender 5.2.1 单帧 Strict passes

- 版本：Blender 5.2.1 LTS，build hash `9e2066aef7ef`。
- 命令：
  `LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe __GLX_VENDOR_LIBRARY_NAME=mesa EGL_PLATFORM=surfaceless /usr/local/bin/blender -b -noaudio --gpu-backend opengl --threads 4 --python render_product.py -- --input /home/ubuntu/pd-official/AAA104.glb --output /home/ubuntu/pd-v3-calibration-20260912-01/out2 --width 256 --height 256 --frames 72 --frame-end 1 --plan calibration_plan.json --passes`
- 结果为 `DIRECTOR_PASS_UNCHANGED files=4`，`DIRECTOR_STRICT_LAYERS product=.../out2/product mask=.../out2/mask passes_mask=.../out2/passes/mask frames=1 color=RGBA`。
- `verify_strict_blender_evidence.py` 对 out2 运行单帧 CPU 只读探针：
  - 命令：`python -X utf8 scripts/verify_strict_blender_evidence.py --beauty-dir .../out2/passes/beauty --alpha-dir .../out2/passes/alpha --product-dir .../out2/product --mask-dir .../out2/mask --display-dir .../out2 --expected-frames 1 --width 256 --height 256 --out var/acceptance/2026-09-12-v3-real-calibration/out2_probe.json`
  - 结果：`status=PASS`，`frames_checked=1`，`beauty_product_alpha_max_abs_diff=0.001907`，`display_product_rgb_max_abs_diff=0.0`。
  - 状态拆分：`alpha_consistency_status=PASS`、`display_product_rgb_status=PASS`、`beauty_product_rgb_status=NOT_VERIFIED`（`beauty_product_rgb_comparison=NOT_COMPARABLE`）。
  - 说明：Beauty linear EXR 与 product 8-bit display PNG 不直接跨色彩空间比较；可比颜色证据是 first-pass display PNG 与 product PNG，二者逐像素一致。该 PASS 仍只覆盖本单帧校准样本，不等同 V3 全片颜色/QA 验收。
- 产物 SHA256、尺寸与 H3 证据写入 `var/acceptance/2026-09-12-v3-real-calibration/`：
  - `real_calibration_summary.json`
  - `calibration_plan.json`
  - `blender_run2.log`（最终 audited run 原始日志）、`blender_run1.log`（首次运行日志）
  - `frame_0001_first_pass.png`、`frame_0001_product_pass.png`（逐像素一致，仅 PNG 编码字节不同）
  - `h3_history.json`、`h3_output_image_stats.json`
  - `evidence_files_sha256.json`（本地证据文件 SHA256 ledger，排除 ledger 自身；含 `verification_command`）
- 代码变更：
  - `render_product.py` 增加 `--frame-end` 校准参数，只渲染到指定帧，不改变 `--frames` 对冻结计划完整帧数的校验。
  - `_assert_protected_pass_files_unchanged` 增加 `DIRECTOR_PASS_UNCHANGED files=N` 日志。
  - `tests/test_render_product_strict_contract.py` 同步断言新帧数变量；本地相关测试 15/15 PASS。

### 最小 H3 background-only 候选图

- 再次确认队列空：`nvidia-smi` 0% util、5864/24564 MiB；`/queue` 为 `queue_running=[] queue_pending=[]`。
- 提交 1 个 prompt-only `MiniMaxH3ImageToVideo` 图，宽高 64x64、length 5、steps 1、seed 0；无 first/last keyframe、无 LoadImage/LoadVideo/产品参考节点。
- ComfyUI 返回 `prompt_id=f8ff544d-998d-4df1-b141-9d1b8141b5ba`，`node_errors={}`，`status=success`，输出 5 帧 PNG。
- 实际历史 graph canonical sha256：`cb3566387c24e5e6d3b7a0945ebaae9e189222d880892ec77a7276de27ca9969`。
- 输出文件 SHA256 见 `real_calibration_summary.json` 与 `h3_output_image_stats.json`；5 帧均为 64x64 RGB 暗色低方差。
- 状态拆分：
  - `h3_runtime_invocation_status=PASS`：候选图成功提交、执行并产出 5 帧。
  - `h3_background_content_status=FAIL`：64x64、近纯黑，低于 Strict 背景规格，不能作为真实背景层或质量 PASS。
  - `h3_product_isolation_status=NOT_VERIFIED`：未运行可信产品检测器，不能凭 prompt 或图像统计证明无产品。
  - `h3_producer_qualification_status=FAIL`：不接入受控 Producer。

### 状态与剩余边界

- `release_eligible=false`；`one_job_limit_consumed=true`，本批不再提交 GPU 任务。
- 单帧/64x64/5 帧最小校准不等于 Strict 全片真实来源闭环；还需 Blender 全片、逐帧背景保护、可信 Mask/Alpha 与 QA/审批全链证据。

### 测试证据

- 新增 `tests/test_verify_strict_blender_evidence.py`：真实 OpenEXR/PNG 门控正负例 4 项。
  - 缺 `display-dir` -> `FAIL`
  - display RGB 被篡改 -> `FAIL`
  - 正常 display/product 与 Alpha 容差内 -> `PASS`
  - Alpha 越界 -> `FAIL`
- 命令：`python -X utf8 -m unittest tests.test_verify_strict_blender_evidence -q`
- 结果：4/4 PASS。
- 全量后端：293/293 PASS，182.288s。
