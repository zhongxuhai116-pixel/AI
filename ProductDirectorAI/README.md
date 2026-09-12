# ProductDirectorAI

通用产品导演工作台（V1 → V6），不绑定拳击靶或任何单一品类。当前进度：**V1 与 V2 均为 ACCEPTED（2026-09-12 用户确认）**，正在推进 **V3 产品保真**——V3-01…V3-04 已通过、V3-05 核心通过但仍有未完成项，V3-06…V3-10 未开工；V4 / V5 / V6 尚未开始。

**接手请先读 [下一次操作交接说明](docs/HANDOFF_NEXT_SESSION.md)**：当前状态、云端访问方式、下次开工步骤与工程纪律都在那里；更细的证据与排查记录见 [V3 任务书](docs/reports/V3_TASK_BRIEF.md) 和 [A05 交接验收](docs/reports/A05_SECURITY_HANDOFF.md)。源码需要配置服务端访问密钥，Git 不包含运行数据、依赖、凭证或用户素材。

## 启动 V1

打开两个 PowerShell 窗口，在本目录分别执行：

```powershell
.\scripts\start-api.ps1
.\scripts\start-web.ps1
```

启动前必须在 API 窗口配置 `PRODUCTDIRECTOR_OWNER_TOKEN`，具体命令以最新恢复入口为准。访问 `http://127.0.0.1:4173/` 并登录。API 文档位于 `http://127.0.0.1:8000/docs`，也受鉴权保护。

## V1 能力

- PNG、JPEG、WebP、GLB 上传与本地素材库
- 三镜头模板计划、编辑、确认
- 图片 FFmpeg 预演与 GLB Blender Headless 预演
- SQLite 任务持久化、状态/阶段/进度/取消/失败信息
- MP4 与 metadata.json 下载
- 设置页配置中国区 MiniMax；API Key 由 Windows DPAPI 用户级加密保存

V1 已允许保存和测试 MiniMax 连接，但尚不让 MiniMax 改写 DirectorPlan；生成式 Provider 编排仍属于 V2。禁止把密钥写进前端或 Git。

## 规划与记录

- [完整 V1–V6 开发规划书](ProductDirectorAI_V1-V6_Codex_Development_Plan.md)：18 章产品与工程规格、分期任务、接口、验收和停止条件。
- [新电脑 / SOL 5.6 继续指令](docs/NEXT_COMPUTER_START.md)：已授权逐阶段到 V6，先继续 A04/A05，完整 V1 未通过。
- [原始 V6 UI 参考图](assets/V6_Automation_UI_reference.png)：用户提供的界面依据。
- [起始合同说明](contracts/README.md)、[V1 DirectorPlan Schema](contracts/director-plan.v1.schema.json)、[有效示例](contracts/director-plan.v1.example.json)。
- [文档总入口](docs/README.md)、[换电脑接手](docs/HANDOFF_NEW_COMPUTER.md)、[当前阶段](docs/CURRENT_PHASE.md)、[执行记录](docs/EXECUTION_LOG.md)、[V1 差距](docs/V1_IMPLEMENTATION_GAPS.md)、[V1 验收](docs/reports/V1_ACCEPTANCE.md)、[视觉 QA](design-qa.md)。
- [云服务器交接](docs/CLOUD_SERVER_HANDOFF.md) 与 [GPU 历史选型](docs/GPU_CLOUD_SELECTION.md)：RTX 4090 节点历史部署和基础出片通过；本次 SSH 超时，未部署新改动。候选价格只作历史快照。

将整个包一起保存可保留 Markdown 中的相对图片和合同链接。移动到产品仓库 `docs/` 后，要同步调整相对路径。若仓库已有 AGENTS.md 或源码，按主规划合并适用规则、保留已有工作。

V2–V6 仍是待执行规格；真实 Provider 编排、账号、云算力、价格和发布能力必须在对应版本实施时验证。
