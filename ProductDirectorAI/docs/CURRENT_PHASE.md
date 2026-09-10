# 当前阶段

- 当前版本：V1 · 3D Director MVP
- 状态：本机实现完成，等待用户用真实产品素材复验
- 下一版本：V2 未授权
- 外部 API：MiniMax 中国区凭证已配置并认证；文本生成受 Token Plan 用量上限限制

## 已实现范围

- 通用产品图片/GLB 上传与素材库
- 模板三镜头 DirectorPlan、镜头名称/机位编辑、保存与确认
- 图片 FFmpeg 预演、GLB Blender Headless 三镜头渲染
- SQLite 持久化任务、真实状态/阶段/进度、取消、错误记录
- MP4 与 metadata.json 下载
- 设置页 MiniMax 密钥输入、Windows 用户级加密保存、认证和最小生成测试
- 深色侧栏、浅色卡片、橙色主动作的 V1 工作台

## 暂不实现

- MiniMax 参与 DirectorPlan、ComfyUI、字幕、配音、BGM
- Platform Profile、批量任务、成本中心、Automation API、一键发布
- 云端 Worker 与外部平台发布

## 继续施工入口

运行 `scripts/start-api.ps1` 与 `scripts/start-web.ps1`，浏览器打开 `http://127.0.0.1:4173/`。主规划与 SOL 5.6 执行提示分别位于根目录规划书和 `CODEX_SOL56_START_HERE.md`。
