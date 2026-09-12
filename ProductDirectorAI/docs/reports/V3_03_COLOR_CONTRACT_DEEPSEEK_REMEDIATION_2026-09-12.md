# V3-03 色彩合同修复与实测（DeepSeek 开发端）

日期：2026-09-12

## 结论

选用合同 A：把 Blender AgX display product PNG 与 H3 背景 sRGB PNG 都按 sRGB EOTF
解码到统一 `display-linear` 光值工作空间，合成后再按 sRGB OETF 编码输出。
Manifest 明确记录 Blender AgX 已预应用、`display-linear` 不是 `scene-linear`。

未选用合同 B，因为它要求可信 scene-linear 背景映射；当前现场 H3/ComfyUI 证据只能
证明历史整帧参考工作流或低规格候选背景，没有可审计的独立背景专用 scene-linear
映射。Beauty EXR 是 HDR scene-linear，product/display PNG 是 AgX display-referred，
二者不能做 raw RGB 跨空间比较。

## 代码改动

- `scripts/strict_composite.py`
  - 新增 `read_scene_linear_rgb`、`read_display_linear_rgb`、`read_product_rgb`、
    `read_background_rgb`；`read_linear_rgb` 仅作兼容入口并明确两种返回语义。
  - 新增 `_resolve_color_contract`：冻结计划优先，CLI 参数其次，display-srgb 默认；
    CLI 与冻结计划冲突直接失败。
  - 新增 `--product-color-space`、`--background-color-space`、`--layer-color-space`。
  - 全量预检与第二遍都按解析后的 `color_contract` 读取，不再依赖 `args` 隐式默认。
  - 报告新增 `color_contract`、`approved_display_product_mae_max`、
    `approved_display_product_match_status`、`approved_display_product_compared_frames`、
    `approved_display_product_visible_frames`。
  - 批准 display 产品对照改为读取实际写出的 8-bit PNG，并与量化后的 display 参考比较；
    未覆盖全部可见帧时状态为 `NOT_VERIFIED` 并阻断完整 PASS。
- `apps/api/productdirector_api/main.py`
  - 新增 `STRICT_COLOR_CONTRACT`，写入 `strict_plan.json` 的 `color_contract`，并在
    `strict_composite` 调用中显式传入产品/背景/可选层色彩空间。
- 测试
  - `tests/test_strict_composite.py`：新增背景 sRGB→display-linear、连续边缘 Alpha、
    display-linear 合同字段、scene-linear PNG 拒绝、scene-linear 背景拒绝、
    非法计划色彩合同拒绝、CLI/冻结计划冲突拒绝等正负例。
  - `tests/test_v3_strict_runtime.py`：完整小样贯通测试追加断言 Manifest 的
    `composite_report.color_contract` 为 display-linear/display-srgb/AgX。

## 实际命令与结果

- `.\.venv\Scripts\python.exe -X utf8 -m py_compile scripts/strict_composite.py apps/api/productdirector_api/main.py tests/test_strict_composite.py tests/test_v3_strict_runtime.py`
  - 结果：通过，无输出。
- `.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_strict_composite -v`
  - 结果：23/23 PASS。
- `.\.venv\Scripts\python.exe -X utf8 var/_run_color_selected_tests.py`
  - 结果：V3 Strict runtime 完整小样贯通 1/1 PASS（运行时清理后已删除该临时 runner）。
- `.\.venv\Scripts\python.exe -X utf8 var/_run_full_tests.py`
  - 结果：300/300 PASS，191.657s。
- `git -c safe.directory='C:/Users/Administrator/Desktop/MEET BENI 4K/GitHub-AI-Archive' diff --check -- scripts/strict_composite.py apps/api/productdirector_api/main.py tests/test_strict_composite.py tests/test_v3_strict_runtime.py`
  - 结果：无 diff 错误；仅提示工作树 LF→CRLF 转换。
- 前端 `npm run build`、`npm run test:sites`
  - 结果：两者均被本开发端沙箱 `spawn EPERM` 阻断，未获得通过结果；本批未改前端。
    控制端可在标准环境复跑。

## 实测正负例边界

- 正例：display-srgb 产品与背景解码到 display-linear 后，核心产品区输出与批准
  display 产品在 8-bit 量化后最大差 ≤1/255。
- 正例：连续 Alpha 边缘（64/255）按 display-linear 公式混合，实测输出与预期一致。
- 负例：产品 PNG 标成 scene-linear 时拒绝，不隐式升级为可信 scene-linear。
- 负例：背景标成 scene-linear 时拒绝，因没有可信 scene-linear 背景映射。
- 负例：冻结计划与 CLI 色彩合同冲突时拒绝，且不写成功产物。

## 已知限制

- 这只是色彩合同代码切片，不等于 V3-03 全片颜色/最终成片保真已通过。
- 当前选择 display-linear 合同，默认只接受 display-srgb PNG。Beauty EXR scene-linear
  仍由 `verify_strict_blender_evidence.py` 标 `NOT_VERIFIED`，不跨空间比较。
- 真实 Blender 全片、独立 background-only H3/ComfyUI producer、shadow/reflection/
  occlusion 真实来源仍未闭环；`release_eligible=false` 保持。

## 下一层工作

1. 用已批准的真实 Blender display PNG 与 product/mask 帧做全片帧集和逐帧颜色/Alpha
   门，而不是单帧校准。
2. 建立独立背景专用 workflow 证据，并在同一 display-linear 合同下接入背景颜色。
3. 对 shadow/reflection/occlusion 层声明并验证同一色彩空间与混合顺序。
4. 最终成片 MP4 编码显示变换/元数据写入 Manifest，避免只依赖合成 PNG 报告。
