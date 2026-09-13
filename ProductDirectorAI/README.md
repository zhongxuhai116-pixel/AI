# ProductDirectorAI

通用产品导演工作台：图片/GLB 素材、图片重建 3D、导演分镜、逐场景视频、批次调度与成果管理。当前含 V6 实现；能力和验收限制以 docs/reports 最新报告为准，不代表全部场景已验收。

**换电脑请先读 [安装与迁移](docs/INSTALL.md)。** 支持连接现有云端，以及安装本地工作台。scripts/setup.ps1 创建环境、安装依赖、构建网页；scripts/doctor.ps1 检查依赖。模型清单和环境快照在 deploy/environment，云端定制节点在 deploy/comfyui。

源码不包含私人密钥、运行数据库、用户素材或大型模型；AI 生成需要可用的 ComfyUI 服务。Git clone 不是服务器磁盘备份。

- [当前阶段](docs/CURRENT_PHASE.md)
- [批次优化与验证](docs/reports/2026-09-13-batch-utilization.md)
- [文档目录](docs/README.md)
- [V1–V6 规划](ProductDirectorAI_V1-V6_Codex_Development_Plan.md)
- [历史交接](docs/HANDOFF_NEXT_SESSION.md)
