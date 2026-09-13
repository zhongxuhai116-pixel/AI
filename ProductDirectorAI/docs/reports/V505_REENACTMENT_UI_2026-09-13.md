# V5-05 参考重演双栏工作台（真实云端证据）

- 日期：2026-09-13（云端会话）
- 范围：主规划 V5-05「双栏播放器 + 对齐时间线 + 差异检查器」前端页面，以及页面所需的两个新只读 API。
- 状态：**代码落地 + 线上部署 + 全链路冒烟通过**。V5 整体仍为 IN_PROGRESS，未宣布 PASS（V5-06 真实重演与 V5_ACCEPTANCE 未完成）。

## 1. 交付物

### 1.1 后端（`apps/api/productdirector_api/main.py`）

- `GET /api/v1/plans` —— 计划列表（V4-06 已有，本页复用）。
- `GET /api/v1/plans/{plan_id}` —— 计划详情，V5 双栏对照页读取目标分镜用（新增强化：返回 payload 内含冻结 shots 与 output）。

### 1.2 前端（`apps/web/src/App.jsx`、`styles.css`）

新增导航项「参考重演」（VideoCamera 图标）与 `ReferencePage` 组件：

- 左栏「参考视频」：下拉选参考片（`GET /references`）→ READY 时内嵌代理播放器（`GET /references/{id}/proxy`）→ 修订列表（`GET /references/{id}/analyses`，rN / 改自 rM / 状态 / 切点数）→ 逐段「切镜观察」卡（段号、起止秒、转场类型、每条观察的值/置信度/method，null 显示为「—（不可推断）」，观察与推断分离如实呈现）。
- 右栏「目标计划」：已有计划下拉（`GET /plans` + `GET /plans/{id}` + `GET /plans/{id}/reference-mapping`）、六个复用维度勾选（时长/景别/构图/运动方向/动作节奏/转场）、产品版本下拉（`GET /product-versions`）、「从参考生成目标计划」（`POST /projects/{project_id}/plans/from-reference`，仅已批准分析版本可生成，前端同样拦截）、目标镜头表（镜头/机位中文标签/帧数/时长误差）。
- 映射差异检查器：每镜头 源秒区间 → 目标帧区间，保留/修改/不支持维度逐条列出；`capability_notes` 如实展示（含「shot_size/action_rhythm 无主体检测模型，未复用」）。
- 对齐时间线：上下两条比例条（源秒 ↔ 目标帧），一屏对照源分段与目标镜头分布。
- 样式新增 `.reference-grid` / `.reference-player` / `.segment-observations` / `.segment-block` / `.reuse-dimensions` / `.mapping-diff` / `.capability-note` / `.aligned-timeline` / `.timeline-row`，900px 以下自动单列。

## 2. 验证证据（全部在云端 117.50.44.60 真实执行）

| 检查 | 结果 |
| --- | --- |
| 后端全量测试 | **383/383 OK**（`unittest discover`，293.8s） |
| 前端构建 | `npm run build` ✓（Vite 5.60s，dist/client + dist/server + hosting.json 齐备） |
| Sites 测试 | **4/4 pass** |
| 线上服务 | `productdirector-v1-api` active（8000 端口，本轮重启并加载新代码） |
| 线上冒烟（页面全部 API 链） | **10/10 PASS**，明细见下 |

线上冒烟（真实 HTTP，会话登录 + CSRF，脚本见 `/tmp/smoke_v505.py`）：

```
[PASS] login status=200
[PASS] GET /references status=200 n=3
[PASS] GET /product-versions status=200 n=64
[PASS] GET /plans status=200 n=43
[PASS] GET /references/{id}/analyses status=200 n=1
[PASS] GET /references/{id}/proxy status=200
[PASS] GET /plans/{id} id=7dc6fb48
[PASS] GET /plans/{id}/reference-mapping plan=7dc6fb48 shots=3
[PASS] POST from-reference (full page chain) plan=dc516b12 shots=3 err=0.01
[PASS] GET new plan detail status=200
```

- 冒烟过程中真实走通了页面的**完整闭环**：选参考 → 分析列表 → 已批准分析 + 产品版本 → 生成目标计划（3 镜头，总时长误差 0.01 帧，≤2 帧门）→ 新计划详情可回读（映射冻结引用）。
- 该次冒烟在云端数据库新增 1 个参考重演计划（`dc516b12…`）与对应产品版本，属应用正常数据。

## 3. 如实未完成（不夸大）

- 浏览器人工走查未做（本环境无浏览器）：页面按既有组件约定（apiRequest/Pill/notice/tabs 风格）实现，API 链已真实 HTTP 验证，但交互观感需用户打开「参考重演」页复核。
- 页面只读展示已有参考与分析；**上传/分析/批准/编辑**仍走 API 或既有流程，未在本页做入口（与 V5 任务书一致，V5-05 只要求播放器+时间线+差异检查）。
- 代理播放器依赖同源会话 Cookie；若 Vite 开发基址跨源，需带凭证（生产部署同源，无此问题）。
- 对齐时间线为比例条（秒↔帧双轴），未做逐帧拖拽 scrub 同步（任务书未要求）。

## 4. 下一步

V5-06：至少 3 条真实重演成片（其中 1 条带 V4 互动镜头），10 条许可参考片的人工标注切点夹具，产出 `V5_ACCEPTANCE.md`。
