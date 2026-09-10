# ProductDirectorAI

V1 已在本机实现。它是通用产品导演工作台，不绑定拳击靶或任何单一品类：上传产品图片可生成二维推近预演，上传 GLB 可由本机 Blender 生成真实三镜头三维预演。

## 启动 V1

打开两个 PowerShell 窗口，在本目录分别执行：

```powershell
.\scripts\start-api.ps1
.\scripts\start-web.ps1
```

然后访问 `http://127.0.0.1:4173/`。API 文档位于 `http://127.0.0.1:8000/docs`。

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
- [SOL 5.6 首条执行指令](CODEX_SOL56_START_HERE.md)：当前只授权 V1，P0 通过后完成 V1。
- [原始 V6 UI 参考图](assets/V6_Automation_UI_reference.png)：用户提供的界面依据。
- [起始合同说明](contracts/README.md)、[V1 DirectorPlan Schema](contracts/director-plan.v1.schema.json)、[有效示例](contracts/director-plan.v1.example.json)。
- [当前阶段](docs/CURRENT_PHASE.md)、[执行记录](docs/EXECUTION_LOG.md)、[V1 验收](docs/reports/V1_ACCEPTANCE.md)、[视觉 QA](design-qa.md)。

将整个包一起保存可保留 Markdown 中的相对图片和合同链接。移动到产品仓库 `docs/` 后，要同步调整相对路径。若仓库已有 AGENTS.md 或源码，按主规划合并适用规则、保留已有工作。

V2–V6 仍是待执行规格；真实 Provider 编排、账号、云算力、价格和发布能力必须在对应版本实施时验证。
