# 当前阶段

- 当前版本：V1 · 3D Director MVP
- 状态：PARTIAL；新云节点 V1 基础设施、GPU 渲染、通用 GLB/图片端到端链路、DirectorPlan 分镜语义及 1080×1920 基线导出已 PASS，完整产品合同、可靠任务、鉴权和用户真实素材仍待验收
- 下一版本：V2 已获用户明确授权，但必须等 V1 阶段门完成后再进入
- 当前优先：补齐 V1 产品差距并逐项验收；每一版本通过后再向 V6 推进
- 外部 API：旧电脑历史记录显示 MiniMax 中国区认证曾通过、文本生成曾受额度限制；DPAPI 凭证不可直接迁移，当前不调用收费 API

## 已实现范围

- 通用产品图片/GLB 上传与素材库
- 模板三镜头 DirectorPlan、镜头名称/机位/焦距/时长编辑、保存、确认和每作业冻结快照；GLB 将字段编译为 Blender 相机动作，图片预演将同一快照编译为 2D 平移/缩放镜头
- 图片 FFmpeg 预演、GLB Blender Headless 三镜头渲染
- SQLite 持久化任务、真实状态/阶段/进度、取消、错误记录
- MP4 与 metadata.json 下载
- 设置页 MiniMax 密钥输入、Windows 用户级加密保存、认证和最小生成测试
- 优云智算新节点已部署 Blender 5.2.1、FFmpeg 4.4.2、Node 22.23.2 与 ProductDirectorAI；API/Web 仅监听 localhost，systemd 服务已启用
- 云端通用 GLB 全链路已生成 540×960、24fps、144 帧、6 秒 H.264；GPU 探针记录 RTX 4090 峰值利用率 61%、显存 1114 MiB
- 云端 1080×1920 回归作业 `bdaee1e7-8109-4610-b0e0-4dd25d243714` 已完成：H.264、1080×1920、24fps、144 帧、6 秒，产物 SHA-256 `0c0f3603e98fad32f8e0d8f66c9a1f35bd812154f8b76c9700b308a848ab62ed`
- 已验收三镜头修改真正驱动 Blender：85mm 定格 24 帧、24mm 侧向移动 72 帧、55mm 环绕 48 帧；Manifest 记录冻结计划 SHA-256
- 已验收同一组修改真正驱动图片 FFmpeg 成片：四个边界帧全部不同，输出为 540×960、24fps、144 帧、6 秒；图片的 `hero_orbit` 明确是 2D 视差近似，不宣称物理 3D 环绕
- 深色侧栏、浅色卡片、橙色主动作的 V1 工作台

## 暂不实现

- MiniMax 参与 DirectorPlan、ComfyUI、字幕、配音、BGM（属于后续版本，不计入 V1）
- Platform Profile、批量任务、成本中心、Automation API、一键发布
- 云端 Worker 的远程创建/启停/调度与外部平台发布（已授权后续版本，但不属于 V1）

## 继续施工入口

从 `docs/V1_IMPLEMENTATION_GAPS.md` 的剩余门逐项推进；云端证据见 `docs/reports/CLOUD_GPU_ACCEPTANCE.md`、`docs/reports/V1_DIRECTORPLAN_ACCEPTANCE.md` 与 `docs/CLOUD_SERVER_HANDOFF.md`。V2 选择 MiniMax H3 的开放权重路线；现有单卡量化环境只作为后续集成候选，先完成 V1，不跨阶段宣布完成。
