# V3 真实 Blender/H3 最小校准：控制端验收

日期：2026-09-12。结论：**Blender 5.2.1 单帧 Strict 通道与显示层一致性 PASS；H3 无参考图调用 PASS、背景内容 FAIL；V3-03 和 V3 全阶段仍 IN_PROGRESS。**

## 独立核验

- 云端隔离目录 `/home/ubuntu/pd-v3-calibration-20260912-01/out2` 有 Beauty、Alpha、Depth、Normal EXR，以及 product、mask、passes/mask PNG 各一帧。`blender_run2.log` 记录 `DIRECTOR_PASS_UNCHANGED files=4`；控制端在云端计算的三份 product/mask PNG SHA256 均为 `9ce0471bb272da3103bf86744803aa254b375db74d0db1ff55373f418f4f608c`。
- 第一遍显示 PNG 与第二遍 product PNG 的 256×256 RGBA 像素最大差为 0。真实文件探针：Alpha 最大差 0.001907（阈值 1/255）、显示 PNG 与 product PNG RGB 最大差 0，均 PASS。Beauty EXR 为 HDR 线性值，不能直接与经过显示变换的 PNG 逐值比较；该项明确 `NOT_VERIFIED`。缺 display PNG 的负例被探针拒绝。
- 云端 H3 history 的 prompt ID `f8ff544d-998d-4df1-b141-9d1b8141b5ba` 执行成功，graph canonical SHA256 为 `cb3566387c24e5e6d3b7a0945ebaae9e189222d880892ec77a7276de27ca9969`；控制端从 history 重新计算一致。graph 不含 LoadImage/LoadVideo，也未向 ImageToVideo 传 first/last keyframe。
- H3 输出只有 5 帧 64×64 RGB，视觉近黑，低于 Strict 背景规格；未证明产品隔离或背景质量。判定 `runtime_invocation=PASS`、`background_content=FAIL`、`producer_qualification=FAIL`，不得接入可发布 Strict Producer。
- 本地 `var/acceptance/2026-09-12-v3-real-calibration/evidence_files_sha256.json` 所列 24 个文件，控制端逐项重算 SHA256 全部匹配。新增探针正式测试 4/4 PASS；DeepSeek 完整后端回归 293/293 PASS，控制端 `git diff --check` 无错误。

## 仍需完成

当前结果只有一个产品的一帧 256×256 校准，不能替代主规划要求的多镜头、多背景、多产品真实 V3 验收。需要做符合目标规格和视觉质量的独立背景生成，冻结来源与保护区，再完成同帧合成、QA、故障注入及人工审核。`release_eligible=false` 保持不变。
