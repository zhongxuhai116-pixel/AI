# V3-03 方向审查与真实 H3 背景质量校准

2026-09-12。本批只做 1 个隔离自托管 H3 background-only job，不重启服务、不部署/推送、不调用付费 API。

## H3 场景校准证据

- `prompt_id=14d0d922-4282-4f57-9e3b-76bb03a5e859`
- 实际提交 graph canonical sha256：`f9f5f3fd9802a7ae4c6bbb5e2b376f8ee1685006d786bc802b2d126911fdc9ca`
- 参数：256x256、requested length 22、steps 4、seed 20260912；实际输出 22 帧。
- 执行状态：`runtime_invocation_status=PASS`，耗时约 12308ms。
- GPU 前后只读状态：`0 %, 22412/24564 MiB` -> `0 %, 22092/24564 MiB`；队列运行/排队均空。
- 证据目录：`var/acceptance/2026-09-12-v3-real-h3-scene-calibration/`
  - `h3_history.json`
  - `h3_background_only_payload.json`
  - `h3_background_only_graph.json`
  - `h3_output_image_stats.json`
  - `contact_sheet.png`
  - `h3_summary.json`
  - `evidence_files_sha256.json`

### 判定

- `runtime_invocation_status=PASS`：1 个 job 成功提交并执行。
- `frame_count_status=PASS`：实际输出 22/22 帧，与 graph/length 合同一致。
- `graph_provenance_status=PASS`：prompt_id、graph canonical hash、模型文件 SHA256 均可追溯。
- `visual_usability_status=PARTIAL`：256x256 低规格、浅灰/白色无缝摄影棚渐变、基本静态；不是可验证的丰富室内场景，也不是最终 9:16/72 帧规格。
- `product_isolation_status=NOT_VERIFIED`：尚无可信 Mask/Alpha 逐帧隔离证据。
- `v3_production_qualification_status=FAIL` / `producer_qualification_status=FAIL`：本样本不得接入 Strict Producer，也不能宣称 V3-03 完成。
- 上一批 64x64/5 帧结果仍判内容 `FAIL`；本批独立验收，不升级上一批。

## 色彩合同缺口

主规划 §9.5 要求线性空间合成，显示变换记录 Manifest。当前 `strict_composite.read_linear_rgb` 对 Blender AgX display-referred product PNG 仅做 sRGB EOTF，得到的是 display-linear，不是 Beauty EXR 的 scene-linear。out2 探针中单帧 `display_product_rgb_max_abs_diff=0` 只证明两个 display PNG 一致，不能证明 scene-linear 颜色/成片保真已通过。

因此：

- 不得把 display PNG 的 RGB 一致当作 scene-linear 颜色合同 PASS。
- `beauty_product_rgb_status` 必须继续为 `NOT_VERIFIED`，直到用 OCIO/AgX forward 把 EXR linear Beauty 转到 display-referred 后比较，或改用同一 scene-linear 参考。
- 背景、阴影、反射、遮挡层都必须与同一线性空间合同对齐，不得各自用不同 display transform 后直接混合。

## V3-03 独立层与剩余门槛

- 当前 Strict compositor 只有 product/background 基础合成；`shadow/reflection/occlusion` 尚未做成独立层。
- 主规划 §9.5 明确这些层单独处理，禁止用完整生成视频重绘可见产品主体；当前 H3 background-only 只覆盖背景候选，不能声称独立分层完成。
- §9.11 还要求：刚性不透明带 Logo、孔洞/细杆产品夹具；透明/强反射若不支持须显式限制；每类至少 3 镜头、2 背景，注入 Logo 缺失、尺寸变化、缺帧等负例全部阻断。

## 下一实现顺序

1. 先固定目标规格：背景候选至少锁定计划分辨率（如 9:16）与计划帧集（如 72 帧），不再把 256x256/22 帧当作可发布背景。
2. 修色彩合同：建立 scene-linear reference 与 display transform 的 Manifest 合同；`strict_composite` 改用正确线性参考，不把 sRGB EOTF 当作 scene-linear。
3. 补 display-referred 与 scene-linear 双路径校验：Blender Beauty EXR 经 OCIO/AgX forward 后与 display PNG 比较；同时保留连续 Alpha 与边缘合同。
4. 定义 shadow/reflection/occlusion 独立层输入合同：层类型、来源、帧集、保护区域、与 product/background 的混合顺序。
5. 用非 ML 的本地可复现样张先打通层混合控制流，再评估真实生成/分割来源；未证明隔离前一律 fail-closed。
6. 组装 V3-04/06/07 所需 QA、审批绑定与真机回归；`release_eligible=false` 不变，直至 §9.11 正负例全过。

## 测试与状态

- `tests/test_verify_strict_blender_evidence.py`：4/4 PASS。
- 全量后端：293/293 PASS，182.288s。
- 本批不提交第二个 GPU job。
