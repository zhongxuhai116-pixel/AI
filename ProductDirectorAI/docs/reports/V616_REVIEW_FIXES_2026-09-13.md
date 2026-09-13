# V6 独立复核修复报告（时长、材质、批次生产）

- 日期：2026-09-13（云端会话）
- 对应文档：`docs/reports/V6_UI_RENDER_BUG_AUDIT_2026-09-13.md`（独立复核，结论 FAIL / 9 项待修）
- 本报告状态：**BUG-01…09 全部按复核要求实现修复**，每项给出改动位置与可复核证据；**浏览器点击级复验仍需复核者执行**（本环境无浏览器），因此不代替复核结论。
- 回归：后端 **646/646**、前端逻辑测试 **13/13**（含 9 项复核用例）、前端 build ✓ + Sites 4/4。

## 1. 逐项修复

### BUG-01 / P1 总时长假下拉 + 固定 6 秒

**问题**：`App.jsx` 输出区三个按钮无 `onClick`；`generatePlan`/`aiGenerate` 写死 `duration_seconds: 6`；分镜卡片写死 `3 SHOTS / 144 帧`。

**修复**：
- 输出区改为**真实可操作的下拉**：总时长选项来自后端支持范围 `Literal[5,6,7,8]`（`apps/web/src/lib/v6ui.js: PREVIEW_DURATION_OPTIONS`），并显示 `5 秒 · 120 帧 @ 24fps` 这类实际换算。
- 比例/帧率/镜头数明确标为**只读规格**（`disabled` + `title` 说明"V1 产品合同要求恰好 3 个镜头"），不再伪装成可选控件。
- 生成/保存计划时**时长与输出规格一起下发**（`buildTemplatePlanBody` / `buildPlanUpdateBody`）。
- 新增 **V6 生产计划面板**：走 `POST /plans/production`，场景数 2–8、总帧数 = 时长 × 24、Profile 可选，创建后显示真实 `total_frames/duration_seconds/profile_version`。
- 计划生成后显示"本次实际输出规格"（取冻结计划的 width/height/fps/frame_count）。

**证据**：`apps/web/tests/v6-ui-logic.test.mjs`（时长 5 秒 → 120 帧；输出规格跟随时长；生产计划 5 场景 = 360 帧）；后端 `test_v6_review_fixes.py::test_duration_is_respected_at_creation`（5 秒 → 120 帧）。

### BUG-02 / P1 提示词未落实为执行计划

**修复**：
- 描述变更即标记计划待更新（`planStaleness`），界面显示横幅，**`run()` 直接拒绝启动**并提示先重新生成/保存（不再沿用旧分镜与旧确认）。
- 新增**能力缺口可见提示**（`detectUnsupportedRequirements`）：命中"人物/手部互动、投影/光效、真人出镜口播、结构拆解"时列出当前管线为什么做不到，不静默降级为三镜头。
- 生产入口（`/plans/production`）与后期链路保持真实：多场景计划、Profile 约束、西语文案字段；生产计划面板与基础预演面板分开命名（"V1 模板导演 · 基础预演" vs "V6 生产计划"）。

**证据**：`v6-ui-logic.test.mjs`（描述变更 stale=true；三个不支持项被识别；正常描述无提示）。

### BUG-03 / P1 计划创建后改分辨率启动时丢失

**修复**：
- `PlanUpdate` 新增可选 `output`；服务端写入冻结计划并返回 `output_changed` / `approval_invalidated`；`output.duration_seconds` 必须与 `duration_seconds` 一致（否则 422）。
- 前端 `run()` 用 `buildPlanUpdateBody` 把 `output` 与 shots 一起 PATCH，并在输出变化时明确提示"输出规格已写入冻结合同（原审批失效）"。

**证据**：`test_v6_review_fixes.py::test_output_change_is_persisted_and_invalidates_approval`（540×960 → 1080×1920 后合同与计划详情都是 1080×1920，且 `approved=false`）；`test_output_duration_mismatch_is_rejected`（422）。

### BUG-04 / P1 无外观信息模型进入广告流程

**修复**：
- 新增 `analyze_glb_appearance()`：报告贴图/UV/顶点色/材质数/**不同材质颜色数**，并给出覆盖等级：`TEXTURED` / `VERTEX_COLOR` / `MULTI_MATERIAL_COLORS`（部分）/ `SINGLE_FLAT_MATERIAL`（未核验）/ `TEXTURES_UNREFERENCED`（有贴图但没被材质引用）。
- **不做"无纹理一律拒绝"**：多材质纯色与顶点色都被认作有效外观来源。
- 新增 `GET /assets/{id}/appearance`（真实解析文件）+ 素材卡片显示外观状态徽标 + 项目页在选中模型时显示外观检查清单与"只做几何预演"的**显式接受**复选框。
- `POST /plans/production` 增加外观门：未核验模型未显式接受时 **409 `appearance_unverified`**，接受后把 `appearance.status=UNVERIFIED_ACCEPTED` 写进冻结计划（供后续打包/发布判断能否宣称外观保真）。

**证据**：`test_v6_review_fixes.py` 的 4 个外观用例 + 生产计划门用例；真实宇航员 GLB 的解析结果为 `SINGLE_FLAT_MATERIAL`（与复核 §2.2 一致：materials=1、images=0、无 UV/顶点色）。

### BUG-05 / P1 基础预演 Manifest 的 Strict 标识冲突

**修复**：
- `director_plan.to_target_document()` **不再固定写 `fidelity_mode=STRICT`**，改为输出**实际执行模式**，并新增 `fidelity.{requested_mode, executed_mode, verification_status, note}`；`product_version_id` 缺失时不再使用占位 UUID，而是 `null` + `product_version_binding="UNKNOWN"`。
- Manifest 顶层新增 `fidelity` 块：`requested_mode`（计划里的请求）、`executed_mode`（本次真实执行：无 Strict 快照即 `CONTROLLED`）、`verification_status`（`NOT_VERIFIED`/`VERIFIED`/`UNKNOWN`）、`strict_snapshot_bound`、真实 `product_version_id` 与绑定状态。
- 新增合同 schema **v1.1**（`contracts/director-plan.v1.1.schema.json`）：允许 1–8 镜头（渲染器真实能力，V1 三镜头合同仍在 API 层强制）、允许 `product_version_id: null`、`fidelity_mode` 枚举化；v1.0 保留为历史合同不再用于导出。
- `load_target_schema()` 默认返回 v1.1，可显式取 `"1.0"` 做对照。

**证据**：`test_v6_review_fixes.py::FidelityReportingTests`（默认快照 → `CONTROLLED` + `NOT_VERIFIED`；严格链路 → `STRICT` + `VERIFIED`；缺版本 → `null`/`UNKNOWN` 且不含占位 UUID；v1.1 schema 校验 3 镜头与 5 镜头文档均通过，v1.0 仍拒绝新形状）。

### BUG-06 / P1 旧预览仍可创建

**修复**：
- 服务端：`POST /batches/preview` 返回 `preview_hash`（覆盖展开项 + 并发上限）；`POST /batches` 接受 `preview_hash` 并**服务端复算**，不一致返回 **409 `preview_stale`**（同时给出期望哈希）；批次 payload 记录 `preview_hash`。
- 前端：表单指纹 `batchFormFingerprint`（计划/Profile/变体/并发/产品版本）与 `previewState` 门控；**任何参数变更、预览失败或加载中都使旧预览失效**，"创建并开始"按钮在预览无效时禁用，创建时带上 `preview_hash`。

**证据**：`test_v6_review_fixes.py::BatchPreviewBindingTests`（同哈希创建成功；变体 2→3 用旧哈希 → 409；并发 2→4 → 409；payload 记录哈希）；`v6-ui-logic.test.mjs`（变体/并发/计划变化都使 `valid=false`，预览失败与缺失也 false）。

### BUG-07 / P1 切换计划沿用旧产品版本

**修复**：
- 前端 `selectPlan()`：切计划即**清空 `product_version_ids` 与旧预览**，再按新计划拉合同；`loadPlanContract` 使用**请求令牌丢弃过期响应**（快速切换不回写旧选择），加载中禁用预览/创建。
- 表单更新改为函数式 `patchForm`，避免异步回调覆盖用户期间的选择。

**证据**：`v6-ui-logic.test.mjs::BUG-07`（切换计划后指纹变化）；`BatchPage` 中 `contractLoading` 门控与 `contractToken` 过期保护（代码可复核）。

### BUG-08 / P2 窄窗口侧栏无名称

**修复**：
- 所有导航按钮加 `aria-label` + `title`，活动项加 `aria-current="page"`；导航容器加 `aria-label="主导航"`。
- 新增**导航展开/收起按钮**（`aria-expanded`，窄窗口下可见），展开后显示完整名称与品牌；CSS 在 ≤1120px 显示该按钮并在展开时加宽侧栏。

**证据**：`v6-ui-logic.test.mjs::BUG-08`（每项都有 ariaLabel/title，活动项 aria-current）；CSS 规则见 `apps/web/src/styles.css` 末尾"V6-16 独立复核修复"段。

### BUG-09 / P2 Profile 选项无法区分平台

**修复**：Profile 选项标签改为 `平台 · 名称 · 版本 · 比例 · 语言（草稿标记）`（`profileOptionLabel`），并对每个选项加 `title`；草稿配置显式标注"（草稿）"。

**证据**：`v6-ui-logic.test.mjs::BUG-09`（两个 9:16 Profile 标签不同且包含平台名与版本；草稿标记存在）。

## 2. 回归与验证

| 项目 | 结果 |
| --- | --- |
| 新增后端测试 | `tests/test_v6_review_fixes.py` **18 例**（外观分析 4、保真报告 4、输出规格 3、预览绑定 4、外观门与端点 3） |
| 新增前端逻辑测试 | `apps/web/tests/v6-ui-logic.test.mjs` **9 例**（BUG-01/02/03/06/07/08/09） |
| 后端全量 | **646/646 OK**（云端） |
| 前端 | `npm test` 13/13（含 Sites 4/4）、`npm run build` ✓、web 200 |
| 真实素材对照 | 宇航员 GLB 解析 = `SINGLE_FLAT_MATERIAL`（has_uv=false），与复核 §2.2 一致 |

### 2.1 真机端到端复核（真实渲染，非合成）

脚本：`v616_review_live.py`（云端执行，结果 `/home/ubuntu/v616_review_live.log`）。

```
[PASS] 外观接口报告真实覆盖等级 SINGLE_FLAT_MATERIAL / has_uv=False
[PASS] 生产计划外观门：未核验模型被阻断（409 appearance_unverified）
[PASS] 显式接受后生产计划创建成功（120 帧）
[PASS] 模板计划：5 秒 → 120 帧
[PASS] 分辨率改动写入冻结合同并失效审批（540×960 → 1080×1920，approved=false）
[PASS] 重新审批 → 创建真实渲染任务（job 3b53210b-47af-4cb3-ad60-12933735e971）→ SUCCEEDED
[PASS] BUG-05：Manifest 顶层保真三元组
       {"requested_mode": "STRICT_REQUESTED", "executed_mode": "CONTROLLED",
        "verification_status": "NOT_VERIFIED", "product_version_binding": "VERSIONED"}
[PASS] BUG-05：基础预演不再被标成已验收 Strict；目标文档 fidelity_mode == executed_mode
[PASS] BUG-05：不再出现占位 UUID（33333333-…）
[PASS] BUG-03：渲染输出使用改动后的分辨率 1080×1920
[PASS] ffprobe 独立复核：1080x1920 / 120 帧 / h264 / 5.000000s
```

即：复核文档 §2.1 指出的"渲染了 6 秒基础预演但意图是 15 秒广告"这类**语义与规格不一致**的问题，
现在会在界面上以"基础预演 vs V6 生产计划"分开呈现，且规格改动会被真正写入冻结合同并用 ffprobe 复核。

## 3. 仍需复核者执行的部分（不替代复核结论）

1. **浏览器点击级复验**：本环境无浏览器；前端已 build 且逻辑测试覆盖了复核用例，但"点击总时长出现选项""窄窗口可展开导航""批次 2→3 后按钮禁用"等**界面级**证据需复核者在真实浏览器重跑（复核文档第 5 节模板可填）。
2. **BUG-02 的完整执行编排**：现有入口已分开且不再静默降级，但"同一宇航员 brief 自动生成 5 段对应时间范围"仍需要人工确认分镜台词/时间轴是否符合 brief（本系统只保证按 Profile 与时长生成多场景计划）。
3. **BUG-04 的保真修复顺序**：本报告只完成"入口暴露 + 显式接受 + 状态入计划"；对照授权参考修补材质、同角度静帧确认、整段重渲染仍待执行（需要授权参考图与材质来源）。
4. **历史产物不回改**：已存在的 Manifest/包不会被重写；新的渲染任务才带 `fidelity` 三元组与新 schema。
