# V5-06 重演生成、引用追踪与回归（真实云端证据）

- 日期：2026-09-13（云端会话，117.50.44.60）
- 范围：主规划 V5-06「重演生成、原视频引用追踪、回归」；产出物为 3 条真实重演成片（含 1 条 V4 互动镜头）与 `V5_ACCEPTANCE.md` 草稿。
- 证据目录（云端，不入 Git）：`/home/ubuntu/pd-v506-recreate-20260913/{re1_interaction,re2,re3}/`，每目录含 `recreate_report.json`、`recreate.log`、渲染/合成/QA 中间产物与成片 `recreate.mp4`。

## 1. 本轮工程改动（先修缺口，再做真实验证）

| 缺口（真实存在） | 影响 | 修复 |
| --- | --- | --- |
| 重演计划无法启动渲染：`PlanUpdate` 强制「恰好 3 镜头 + 总时长 5–8 秒」 | 参考映射产生的 1–4 镜头、分数时长计划在 `POST /runs` 被 409 拒绝 | `create_run` 按 `from_reference` 分支：`_validate_from_reference_plan_snapshot` 逐镜头核对冻结映射（镜头数、shot id、量化时长、总帧数）；V1 合同不变 |
| 输出合同无显式帧数表达 | 分数时长（如 3.21s）无法通过 `OutputSpec` | 新增 `ReenactmentOutputSpec`（`frame_count` 显式 + `duration_seconds` 分数）与 `output_spec_for_plan` 分派；`OutputSpec` 与 V1 `ImagePreviewSpec` Schema 保持严格同步（漂移守卫通过） |
| 渲染器写死「恰好 3 镜头、单镜头 ≥24 帧」 | 单镜头/子秒分段无法渲染 | `render_product.load_plan` 支持 1–8 镜头、单镜头 ≥1 帧；V1 的 3 镜头约束仍在 API 层强制 |
| 末尾开放分段（`end_s = null`）静默按 1.0 秒 | `30126cfe`（3.0s 单片）被错误量化为 24 帧 | 用参考片 `timebase.duration_s` 求解真实尾段（纠正为 72 帧） |
| 子秒分段被 24 帧下限抬升 | RE1 第三镜头 0.917s → 24 帧，误差 2.0 帧，**超出 ≤1 帧/镜头门** | 改为量化整帧：22 帧，误差 −0.01 帧 |
| Strict QA 不识别镜头边界 | 硬切处相邻帧轮廓/尺寸突变被判为伪影，RE1 在帧 54 被误阻断 | CLI QA 读取计划镜头边界，跨边界帧对豁免（记录 `skipped_shot_boundaries`），镜内检查保持；新增 1 例测试 |
| Run manifest 无参考溯源 | 重演产物无法回到参考片/分析/映射 | 严格 Run manifest 新增 `reference_recreation`（参考片与代理 sha256、分析修订与 payload hash、映射 id/hash + 完整映射快照） |

## 2. 三条真实重演（完整链路，非占位）

链路：`render_product.py --passes --layers`（真实 Blender 多通道）→ `build_layers.py`（阴影/反射差分）→ `validate_fidelity_passes.py` → `h3_background.py`（自托管 ComfyUI 真实 H3 背景）→ `strict_composite.py`（冻结层合同 + 像素锁定）→ `dual_product_check.py` → FFmpeg 成片 → `strict_qa.py`。驱动器：`scripts/v5_recreate.py`（本轮新增，可复现）。

| 标注 | 参考片 | 计划 | 时长误差（帧/镜头，总） | 五通道 | 合成 | 双产品 | QA | H3 背景 | 成片 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| RE1（含互动） | `d70659be` 3.125s / 3 段硬切 | 75 帧（24/29/22） | 0.00 / 0.01 / −0.01，总 0.00 | passed | passed，`pixel_lock_ok`，豁免 3,793,344 px，产品显示值 PASS | 0 误报 | passed（边界 25/54 豁免） | SUCCEEDED 75 帧 sha `5678257e…` | 540×960 / 24fps / 75 帧 sha `6a662f96…` |
| RE2 | `9235db86` 2.0s / 2 段 | 48 帧（24/24） | 0.00 / 0.00，总 0.00 | passed | passed，`pixel_lock_ok` | 0 误报 | passed | SUCCEEDED 48 帧 sha `9a504e49…` | 540×960 / 24fps / 48 帧 sha `15784257…` |
| RE3 | `30126cfe` 3.0s / 1 段 | 72 帧（单镜头） | 0.00，总 0.00 | passed | passed，`pixel_lock_ok` | 0 误报 | passed | SUCCEEDED 72 帧 sha `876afbbd…` | 540×960 / 24fps / 72 帧 sha `8d61382c…` |

### 2.1 RE1 的 V4 互动镜头（受支持动作）

- 动作：`press_button`，锚点「相机顶盖」（产品本地归一化坐标 + 单位法线 + 半径 0.0725m，来自 V4 锚点合同）。
- 申报帧：准备 30 / 接触 46 / 结束 52（落在 shot_02 的 25–53 帧区间内）。
- 真实几何 QA（`previz/contact_qa.json`）：**passed**；申报接触帧 46 = 实际接触帧 46（偏差 0 帧）；保持窗最大表面间隙 **0.1mm**（门：≤ min(2cm, 半径)）；最大穿透 **4mm**（门：≤1cm）；穿透帧 **0**（门：≤2 帧）。
- 人物层作为 `occlusion` 独立层参与合成，遮蔽区豁免像素锁定 3,793,344，遮挡区取人物 Proxy 颜色、未遮挡区与可信产品完全一致。

### 2.2 引用追踪（原视频不复用）

每条成片的 `recreate_report.json` 内 `reference_recreation_citation` 记录：

```
reference_id / reference_source_sha256 / reference_proxy_sha256
analysis_id / analysis_revision / analysis_payload_sha256
mapping_id / mapping_payload_sha256 / mapping（完整冻结映射：分段、目标帧区间、保留/修改/不支持维度、能力说明）
note：参考内容为外部数据；品牌/水印/音乐/人物不默认复制到成片
```

映射对每个镜头逐条给出 `kept_dimensions = [duration, motion_direction, transition]`、`changed_dimensions = [shot_size, composition, action_rhythm]`、`unsupported_dimensions`，能力说明明确「单目不恢复焦距/相机路径」「shot_size/action_rhythm 无主体检测模型，未复用」「品牌/人物身份/音乐/逐字文案默认不复用」。三条成片的背景均为真实 H3 生成的空白影棚，与参考片内容无关。

## 3. 回归

| 项 | 结果 |
| --- | --- |
| 后端全量测试（云端） | **387/387 OK**（V5-06 新增 5 例：引用追踪、重演输出合同与分派、重演计划启动严格任务、开放尾段时长、子秒段误差门） |
| 前端 | build ✓ + Sites 4/4（本轮未改前端） |
| 现场服务 | api / web / postgresql / comfy-h3 均 active；API 已重启加载本轮代码 |
| V3 保真链（三类产品 × 3 镜头 × 2 背景等历史矩阵） | 本轮 3 条重演全部重新跑通五通道校验 + 合成 + 双产品 + QA，未出现回归 |

## 4. 如实未完成

- 主规划 11.9 要求的「10 条有权使用的 10–30 秒参考片 + 人工标注硬切/转场/景别/运动」未完成：本轮重演使用 2–3.125 秒的测试参考片；人工标注必须由负责人完成。合成夹具 10/10（F1=1.0）仅作算法回归基线，不能替代真实素材夹具。
- V5-05 双栏页面的浏览器人工走查未做（本环境无浏览器）。
- 构图取景为确定性启发式（V3-07 校准机位族 + 固定注视高度 0.21m），未按产品包围盒自适应求解；属公开记录的 `changed_dimensions: composition`。
- 低照度/遮挡场景的切镜与运动推断能力未在真实素材上评估（依赖上述素材到位）。

结论：V5-06 的工程与真实重演证据齐备，`V5_ACCEPTANCE.md` 已按 11.9 逐条核对，剩余为需要负责人参与的素材标注与人工走查；在负责人确认前 V5 保持 IN_PROGRESS。
