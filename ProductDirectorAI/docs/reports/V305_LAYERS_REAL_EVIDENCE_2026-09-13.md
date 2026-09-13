# V3-05 独立层真实闭环证据（2026-09-13，云端）

结论：**V3-05 的“真实 AI 背景 + 阴影/反射/遮挡独立层”在云端真实数据上闭环 PASS，含真实遮挡物验证。** 产物全部在云端隔离目录 `/home/ubuntu/pd-v305-layers-20260913/`（不入 Git）。执行前云端 `main` 为合并提交 `b260f1f`（主线 Strict 运行时工作 × V3-05 独立层工作），随后修复 `edc2df7`（build_layers 帧号）与新增 `c8f0396`（`--occluder` 独立遮挡物 GLB）。全程未动 systemd 服务；H3 生成前查询 ComfyUI 队列为空。

## 1. 本轮代码变化（合并两条开发线）

| 提交 | 内容 |
| --- | --- |
| `b260f1f` | 合并主线（冻结计划合同、可信 Alpha、预检隔离、Strict 运行时）与云端 V3-05 独立层工作：`--layers shadow/reflection/occlusion`、遮挡区像素锁定豁免计数、中性层逐字节回归、`render_product.py --layers`（plate_full/plate/occlusion）、`validate_fidelity_passes.py --layers`、`build_layers.py`、`h3_background.py`、`tests/test_v305_layers.py`；`tests/test_strict_composite.py` 的层夹具同步到新层合同（阴影=16 位灰度因子，遮挡层必须带可信 Alpha） |
| `edc2df7` | 修复 `build_layers.py`：输出帧号沿用输入帧号（此前按 0 起编号，与 Blender 的 frame_0001 起编号在冻结帧集合合同下被正确拒绝——真实跑出来的缺陷） |
| `c8f0396` | 新增 `render_product.py --occluder <GLB>`：独立遮挡物 GLB（网格标记 `pd_occluder`、名称 `Occluder` 前缀），不属于产品、不参与产品包围盒，但随产品同一坐标对齐；**只收集本次导入新增的网格**（初版把已导入产品网格误标成遮挡物的缺陷在真实运行中暴露并修复，合同测试已钉住 `obj.name not in existing`） |

合并后的全量后端回归：**341/341 OK**（云端 222.0s）。

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

## 8. 真实遮挡物验证（--occluder，render_occ）

场景增加独立遮挡物 GLB（0.28m 方块，位于相机与产品之间、画面下方），`render_product.py --passes --layers --occluder <ev>/occluder.glb` 重渲 72 帧：

| 指标 | 结果 |
| --- | --- |
| 遮挡层（occlusion） | 72/72，`occluders=OccluderCube`；平均覆盖率 4.17%，遮挡像素 1,555,824（其中 342,917 在产品掩码内） |
| 五通道隔离 | `DIRECTOR_PASS_UNCHANGED files=288`（第二遍未改写五通道） |
| build_layers | shadow/reflection 与非遮挡运行完全一致（factor_min 0.0714 / energy_max 2.4883）——底板差分对两个 plate 都含遮挡物，正确抵消；occlusion coverage 0.0417 |
| strict_composite（冻结计划 + 三层全帧必需） | **passed=true / pixel_lock_ok=true / output_written=true**；`occlusion_exempted_pixels_total=348,881`；display 产品对照 PASS |
| 数值复核（帧 36） | 锁定区（掩码内未被遮挡）composite 与可信产品平均绝对差 0.00289（≤1/255 门）；遮挡区 composite 与遮挡层颜色平均绝对差 0.00207（遮挡物确实盖在产品上方） |
| 双产品检测 | composite_occ 72/72 PASS，零误报 |

## 9. 1080×1920 口径复验

同一 72 帧计划在 1080×1920 重渲（`--passes --layers`）：五通道 288 文件隔离不变、plate_full/plate/occlusion 各 72；`build_layers` shadow coverage 0.0277 / factor_min 0.0609、reflection energy_max 5.5273；`validate_fidelity_passes --layers` **passed**；H3 背景按同 seed 重新生成（SUCCEEDED，源视频 sha 与 540×960 相同——H3 原生 576×1024，1080×1920 背景是解码缩放，脚本文档即此合同）；`strict_composite` **passed / pixel_lock_ok / 72 帧**；`dual_product_check` 72/72 PASS 零误报。

## 10. 范围说明（如实记录）

1. 遮挡层已从“全零层”升级为**真实遮挡物验证**（合成夹具 + 真实渲染双重证据）。仍未验证：真实人物遮挡素材、遮挡物自身的投影（遮挡层只含遮挡物本体，遮挡物在背景上投下的阴影属后续工作，见下条）。
2. **遮挡物阴影语义**：plate 差分对里两个 plate 都包含遮挡物，因此遮挡物本身被抵消；但其在背景地面上的投影会残留在 shadow/reflection 层中（plate_full 有产品投影 + 遮挡物投影，plate 只有遮挡物投影——差值即产品效应，方向正确）。遮挡物投影在最终合成中出现的程度取决于 shadow 层强度，本轮未单独人工比对，属如实记录项。
3. **色彩语义**：shadow 因子与 reflection 能量来自 EXR 底板差分（原始线性值），按层合同编码为 PNG；合成在 display-linear 工作空间执行（背景 sRGB 解码）。该跨空间语义已在报告与本文档如实记录，scene-linear 全链路映射仍是后续工作。
4. 反射层 `coverage_mean≈0` 但 `energy_max` 非零（540×960 为 2.4883、1080×1920 为 5.5273）：能量集中在产品下方局部区域，覆盖阈值统计如实记录极值，不把“无大面积反射”写成“无反射”。
5. 本轮已验证 540×960 与 1080×1920 两个口径、各 72 帧；更长镜头（非 3×24 计划）未在本轮复验。
6. 真实运行暴露并修复的两个缺陷都记录在案：`build_layers` 帧号（`edc2df7`）、`import_occluders` 误把产品网格标成遮挡物（`c8f0396`）。
