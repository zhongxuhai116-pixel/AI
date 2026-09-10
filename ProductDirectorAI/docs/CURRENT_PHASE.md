# 当前阶段

- 当前版本：V1 · 3D Director MVP
- 状态：PARTIAL；本机核心已有历史实现与技术证据，完整 V1 尚未验收，用户真实素材与新电脑环境均未复验
- 下一版本：V2 未授权
- 当前优先：先将文档/源码交接到 GitHub并在新电脑恢复；服务器排查与部署暂停
- 外部 API：旧电脑历史记录显示 MiniMax 中国区认证曾通过、文本生成曾受额度限制；DPAPI 凭证不可直接迁移，当前不调用收费 API

## 已实现范围

- 通用产品图片/GLB 上传与素材库
- 模板三镜头 DirectorPlan、镜头名称/机位编辑、保存与确认；编辑保存已有，但渲染器应用这些字段仍有差距，见 `V1_IMPLEMENTATION_GAPS.md`
- 图片 FFmpeg 预演、GLB Blender Headless 三镜头渲染
- SQLite 持久化任务、真实状态/阶段/进度、取消、错误记录
- MP4 与 metadata.json 下载
- 设置页 MiniMax 密钥输入、Windows 用户级加密保存、认证和最小生成测试
- 设置页展示的是购买前 GPU 候选快照；实际已购为优云智算华北二 A RTX 4090 24GB 节点，尚未部署 Blender/FFmpeg/ProductDirectorAI
- 深色侧栏、浅色卡片、橙色主动作的 V1 工作台

## 暂不实现

- MiniMax 参与 DirectorPlan、ComfyUI、字幕、配音、BGM
- Platform Profile、批量任务、成本中心、Automation API、一键发布
- 云端 Worker 的真实创建/启停/调度与外部平台发布（选型文档已完成，V2 未授权）

## 继续施工入口

先按 `docs/HANDOFF_NEW_COMPUTER.md` 克隆、安装与复验，再使用启动脚本。差距见 `docs/V1_IMPLEMENTATION_GAPS.md`，云端事实见 `docs/CLOUD_SERVER_HANDOFF.md`；不得根据历史 PASS 自动宣布 V1 完成。
