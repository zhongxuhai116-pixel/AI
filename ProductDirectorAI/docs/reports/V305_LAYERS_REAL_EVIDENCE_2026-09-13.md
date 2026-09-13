# V3-05 独立层真实闭环证据（2026-09-13，云端）

结论：**V3-05 的“真实 AI 背景 + 阴影/反射/遮挡独立层”在云端真实数据上闭环 PASS；遮挡层以全零层形态通过（场景无遮挡物），真实遮挡资产仍未验证。** 产物全部在云端隔离目录 `/home/ubuntu/pd-v305-layers-20260913/`（不入 Git）。执行前云端 `main` 为合并提交 `b260f1f`（主线 Strict 运行时工作 × V3-05 独立层工作），随后修复 `edc2df7`（build_layers 帧号）。全程未动 systemd 服务；H3 生成前查询 ComfyUI 队列为空。

## 1. 本轮代码变化（合并两条开发线）

| 提交 | 内容 |
| --- | --- |
| `b260f1f` | 合并主线（冻结计划合同、可信 Alpha、预检隔离、Strict 运行时）与云端 V3-05 独立层工作：`--layers shadow/reflection/occlusion`、遮挡区像素锁定豁免计数、中性层逐字节回归、`render_product.py --layers`（plate_full/plate/occlusion）、`validate_fidelity_passes.py --layers`、`build_layers.py`、`h3_background.py`、`tests/test_v305_layers.py`；`tests/test_strict_composite.py` 的层夹具同步到新层合同（阴影=16 位灰度因子，遮挡层必须带可信 Alpha） |
| `edc2df7` | 修复 `build_layers.py`：输出帧号沿用输入帧号（此前按 0 起编号，与 Blender 的 frame_0001 起编号在冻结帧集合合同下被正确拒绝——真实跑出来的缺陷） |

合并后的全量后端回归：**339/339 OK**（云端 195.7s）。

## 2. 真实渲染（CC0 相机，540×960，72 帧，3 镜头 ×24）

命令：`blender -b -P blender/scripts/render_product.py -- --input /home/ubuntu/pd-cc0/Camera_01_textured.glb --output <ev>/render --width 540 --height 960 --frames 72 --plan <ev>/plan.json --passes --layers`。

| 产物 | 帧数 | 说明 |
| --- | ---: | --- |
| passes/beauty、alpha、depth、normal | 72×4 | 日志 `DIRECTOR_PASS_CHANNELS beauty,alpha,depth,normal` |
| **第二遍隔离** | — | `DIRECTOR_PASS_UNCHANGED files=288`：product/mask 第二遍渲染未改写五通道 |
| product / mask / passes/mask | 72×3 | `DIRECTOR_STRICT_LAYERS … frames=72 color=RGBA` |
| passes/layers/plate_full（含产品完整场景 EXR） | 72 | `DIRECTOR_LAYER_PLATE_FULL` |
| passes/layers/plate（干净底板 EXR） | 72 | `DIRECTOR_LAYER_PLATE` |
| passes/layers/occlusion（RGBA） | 72 | `no_occluders_zero_layer`——场景无遮挡物，如实注明全零层 |
| 反射面检测 | — | `no_reflective_surface`；反射能量由底板差分提取 |

## 3. 独立层构建（build_layers.py）

`--beauty plate_full --plate plate --mask passes/mask --occlusion occlusion --out <ev>/layers`：

- shadow：72 帧 16 位灰度因子；`coverage_mean=0.0281`、`factor_min=0.0714`（产品下方真实阴影）；
- reflection：72 帧 8 位 RGB 能量；`energy_max=2.4883`（真实互反射/高光溢出），覆盖率≈0（局部能量）；
- occlusion：全零层如实注明（场景无遮挡物，合成侧按无遮挡处理）；
- `layers_report.json` 记录公式、编码、极值与首帧 SHA-256。

## 4. 五通道 + 独立层校验（validate_fidelity_passes.py --layers）

`--passes <ev>/render/passes --frames 72 --start-frame 1 --layers <ev>/layers_root`：**passed: true**。

- 72 帧逐帧：遮罩二值度 0.9943–0.9968、mask↔alpha IoU 1.0（69/72 帧，其余 0.9999）、法线模长 0.9965–0.9991、产品区深度有限（最近 2.4004–4.2122 米，最大为 Blender 无命中哨兵 1e10）；
- 独立层：plate_full/plate/shadow/reflection/occlusion 各 72 帧，帧号与全片一致；occlusion 全零注明。

## 5. 真实 AI 背景（h3_background.py，自托管 ComfyUI，不收费）

`PYTHONPATH=apps/api .venv/bin/python scripts/h3_background.py --plan <ev>/plan.json --frames 72 --width 540 --height 960 --out <ev>/h3_bg`：

- ComfyUI 可达、队列为空（生成前查询）；prompt 由三镜头运动描述组装（push-in / lateral / orbit），显式声明无产品/无物体/无人物/无文字；
- 生成 **SUCCEEDED**，源视频 SHA-256 `fbe1cca4b9d8578a23b57dedc4ce69a2d9077bc5387bae6a5d54c1ed9fdf953c`，解码 72 帧 `bg_0001..bg_0072`（540×960）。

## 6. Strict 合成（strict_composite.py，冻结计划 + 三层全帧必需）

冻结计划：`frame_count=72 / start_frame=1 / background_frame_offset=0 / required_layers 三层 1..72 / display-srgb 颜色合同`。输入：product/mask 用渲染侧 product RGBA 与 mask（display-srgb 合同），背景用 H3 真实帧。

| 指标 | 结果 |
| --- | --- |
| passed / output_written | **true / true**（72 张 composite PNG + composite_report.json） |
| pixel_lock_ok | **true**（72/72） |
| approved_display_product_max_abs_diff | **0.0**（掩码内与可信产品逐像素一致） |
| layers.coverage_mean | shadow 0.0282 / reflection 0.0 / occlusion 0.0 |
| occlusion_exempted_pixels_total | 0（全零遮挡层，符合豁免计数语义） |

独立数值复核（帧 36）：掩码外扩外 composite 与 H3 背景相关系数 **0.9929**、平均绝对差 0.00027（层的影响）；掩码内 composite 与可信产品平均绝对差 **0.0**。

## 7. 双产品检测正例回归（真实 H3 背景）

`scripts/dual_product_check.py --frames <ev>/composite --product passes/beauty --mask passes/mask --dilate 2`：**72/72 PASS，零误报**（真实 AI 背景上没有把背景纹理误判成多余产品）。

## 8. 范围说明（如实记录）

1. **遮挡层**本轮以“全零层”形态通过（场景没有遮挡物）；遮挡乘算/豁免计数的数学与像素锁定豁免由 `tests/test_v305_layers.py` 的合成夹具验证（含中性层与基线逐字节一致），真实人物/物体遮挡素材仍未验证，属 V3-05 剩余项。
2. **色彩语义**：shadow 因子与 reflection 能量来自 EXR 底板差分（原始线性值），按层合同编码为 PNG；合成在 display-linear 工作空间执行（背景 sRGB 解码）。该跨空间语义已在报告与本文档如实记录，scene-linear 全链路映射仍是后续工作。
3. 反射层 `coverage_mean≈0` 但 `energy_max=2.4883`：能量集中在产品下方局部区域，覆盖阈值统计如实记录极值，不把“无大面积反射”写成“无反射”。
4. 本轮为 540×960 / 72 帧口径；1080×1920 与更长镜头未在本轮复验。
5. 首次 `build_layers` 运行暴露帧号缺陷（输出 0 起编号被冻结帧集合合同拒绝），修复提交 `edc2df7` 后重跑通过——缺陷与修复都记录在案。
