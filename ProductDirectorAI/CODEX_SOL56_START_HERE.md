# 交给 Codex SOL 5.6 的首条执行指令

选择你准备使用的 SOL 5.6 执行任务，然后复制下方指令。当前仓库已经包含 V1 本机实现、真实图片/GLB 验收记录和完整 V1–V6 规划；V2 尚未授权。

## 可直接复制的指令

你负责实现 ProductDirectorAI。请完整读取 `ProductDirectorAI_V1-V6_Codex_Development_Plan.md`、`assets/V6_Automation_UI_reference.png`、`contracts/director-plan.v1.schema.json`、`contracts/director-plan.v1.example.json`，以及当前仓库全部适用工程说明。

先检查仓库、现有实现、未提交改动和运行环境，保留用户工作。读取根目录 `AGENTS.md`、`docs/CURRENT_PHASE.md`、`docs/EXECUTION_LOG.md`、`docs/reports/P0_ENVIRONMENT.md`、`docs/reports/V1_ACCEPTANCE.md` 和 `design-qa.md`。不要重写已经通过的 V1，不要现在实现其他版本。

当前仅授权维护 V1。先在本机复跑前端 build、Sites worker 测试、API 健康检查和一个最小图片预演；发现回归时仅修复 V1。复验通过后停止，不进入 V2–V6，不自动修改授权版本。

若用户提供真实产品素材，用它做业务复验；否则保留 `tests/fixtures/generic-product.glb` 作为明确标识、可再生成的通用测试夹具。产品不一定是拳击靶，禁止加入品类绑定。不能用占位视频让验收通过。

V1 实现独立 Web 工作台：产品图片/3D 入口、产品版本审核、模板三镜头 DirectorPlan、分镜编辑与确认、3D/图片预演、真实渲染任务、真实进度/错误/取消/重试、视频与 Manifest 下载、本地及已配置云端 Worker。界面使用参考图的深色导航、浅色卡片和橙色主动作，按 V1 范围展示功能。

不要提前实现 AI Provider、真人、参考视频、批量自动化或发布。DirectorPlan 的 `fidelity_mode=STRICT` 在 V1 是保护产品的请求约束；只有 V3 的保真链路与 QA 验收完成后才能显示“Strict 保真已验证”。图片输入不能默认获得真实 3D 环绕能力。

每个任务按本期合同实现并运行适当测试，保存任务 ID 对应证据。先给出仓库/环境结论和任务清单，然后开始实施，不要只复述规划。所有 API Key、token 和凭证只能保存在后端凭证存储，不能进入前端、Git、workflow、日志或导出包。

不要自动购买云服务、花费未授权 API 预算或向外部平台发布。遇到缺账号、模型或节点时，继续完成不依赖它的工作，准确标记相关真实集成尚未验收，不以 mock、skip 或静态截图代替。

最终提交 `docs/reports/P0_ENVIRONMENT.md`、`docs/reports/V1_ACCEPTANCE.md`、启动/测试步骤、实际可播放成果及界面截图，说明哪些通过、失败、未测试或 BLOCKED。验收报告完成后停止，等待我明确解锁 V2。

## 之后每期使用

当前已验收版本为 V(n-1)，现在明确授权执行 Vn。读取总规划、Vn 规格、当前状态、前期验收和 ADR。检查实际依赖与旧数据兼容性，列出本期任务 ID，然后实施本期全部必需任务。保留前期可用功能和用户修改，遵守统一 Provider、状态机、保真、预算和发布合同。完成真实链路、恢复、界面与回归测试，输出 `Vn_ACCEPTANCE.md` 及证据后停止，不进入下一期。外部条件缺失写 BLOCKED，不以模拟结果代替真实通过。
