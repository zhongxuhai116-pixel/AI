# V3-03 H3 场景校准：控制端验收

日期：2026-09-12。判定：**无参考 H3 graph 运行、帧数与来源 PASS；视觉候选 PARTIAL；Strict 背景 Producer 资格 FAIL。**

控制端从云端 ComfyUI history 独立核对 `prompt_id=14d0d922-4282-4f57-9e3b-76bb03a5e859` 执行成功，graph 输入 256×256、length 22、steps 4，实际输出 22/22 帧；规范化 graph SHA256 为 `f9f5f3fd9802a7ae4c6bbb5e2b376f8ee1685006d786bc802b2d126911fdc9ca`，与证据一致。graph 无产品图/视频输入或 first/last keyframe。`var/acceptance/2026-09-12-v3-real-h3-scene-calibration/evidence_files_sha256.json` 的 28 个文件经控制端逐项重算，全部匹配。

控制端查看 `contact_sheet.png` 与首帧：浅灰/白色无缝摄影棚渐变，基本静态，未见明显产品或文字。这个低规格样本可作为方向候选，但不是 9:16、72 帧的目标背景；没有可信产品隔离检测和人工批准。故 `visual_usability=PARTIAL`、`product_isolation=NOT_VERIFIED`、`producer_qualification=FAIL`，不进入可发布 Strict 流程。

主规划 §9.5 要求在线性空间合成并在 Manifest 记录显示变换，但尚未锁定是 scene-linear 还是统一的 display-linear 工作空间。当前对 AgX display PNG 做 sRGB EOTF 得到 display-linear，不等同 Beauty EXR scene-linear；代码和 Manifest 必须明确这一语义并证明所有输入层在同一工作空间。Blender 单帧两个显示 PNG 相同不能替代完整色彩合同验收。下一步先锁定并实测颜色/显示变换，再定义 shadow、reflection、occlusion 独立层和目标规格背景的来源、帧集、保护区、混合顺序；V3-03 保持 `IN_PROGRESS`，`release_eligible=false`。
