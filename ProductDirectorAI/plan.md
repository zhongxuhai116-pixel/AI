# V3-06 + V3-07 开工计划（2026-09-12，方案 A · 云端执行）

依据：`docs/HANDOFF_NEXT_SESSION.md` 第 6/8 节、`docs/reports/V3_TASK_BRIEF.md` 任务表。
目标：把"坏结果自动拦住"做出来——V3-06 双产品检测、V3-07 Strict QA。
纪律：不以 mock 冒充真实云端证据；不改动既有文件行为；全部测试通过后才标完成。

## 阶段拆解

### Stage 1 — 并行实现（2 个 coder，互不冲突，不碰 git/SSH/既有文件）

| Worker | 交付物（仅新增文件） | 本地验证 |
| --- | --- | --- |
| 工程师_V306双产品检测 | `scripts/dual_product_check.py` + `tests/test_v306_dual_product.py` | 合成正/负例单测全过 |
| 工程师_V307StrictQA | `scripts/strict_qa.py` + `tests/test_v307_strict_qa.py` | 五类故障合成负例单测全过 |

约定：
- 每个 Worker 在独立副本目录工作（`workspace\v306_work` / `workspace\v307_work`），只新增自己的两个文件。
- OpenEXR 延迟导入（本机无此库；与 strict_composite.py 同款写法）。
- 测试只用 numpy + Pillow，unittest 风格，可被 `unittest discover` 收编，不依赖 fastapi。
- 出口码：0=通过，非 0=阻断；报告 JSON 含逐帧判定、问题帧、Shot 定位（可选 --plan）。

### Stage 2 — 集成与本地回归（Orchestrator 本人）

1. 把两组新文件合并回 `workspace\AI\ProductDirectorAI`。
2. 跑新单测（托管 python）+ 全量 152+ 回归（旧 venv：`Desktop\MEET BENI 4K\GitHub-AI-Archive\ProductDirectorAI\.venv`）。
3. 提交并推送 GitHub main。

### Stage 3 — 云端真实证据（1 个 coder，串行）

SSH：`ssh -i ~/.ssh/pd_ed25519 ubuntu@117.50.44.60`，云端仓库 `/home/ubuntu/AI`。

1. `git pull --ff-only`，云端全量 unittest（原 152 + 新增，全过）。
2. V3-06 真实证据：CC0 相机（`/home/ubuntu/pd-cc0/Camera_01_textured.glb`）`--passes` 渲染 → 干净背景合成（应通过）；构造含第二个产品影像的背景合成（应阻断 + 热图 + 问题帧）。
3. V3-07 真实证据，五类故障各一个负例：
   - Logo 缺失：CC0 相机正常渲染 vs 抹除 Logo 区域渲染 → 阻断；
   - 轮廓异常 / 尺寸变化 / Mask-ID 错误 / 缺帧：在真实通道产物上构造 → 各自阻断并定位 Shot/frame；
   - 未篡改的完整真实通道集 → 全部通过。
4. 证据（热图、报告 JSON、帧定位）落盘并记录；更新 `V3_TASK_BRIEF.md` 的 V3-06/V3-07 状态与证据，提交推送。

### Stage 4 — 验收汇报（Orchestrator 本人）

汇总证据、测试计数、云端状态，向用户报告 READY_FOR_REVIEW（是否 ACCEPTED 由用户判定）。

## 排除项（本期不做）

- V3-05 剩余的真实 AI 背景、阴影/反射/遮挡独立层（方案 B 内容，用户已改选 A）。
- V3-08 / V3-09 / V3-10。
- 不重启/改动云端四个 systemd 服务配置；渲染复用现有 Blender 5.2.1，不装新软件。
