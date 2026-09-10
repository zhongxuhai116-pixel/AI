# 交给 Codex SOL 5.6 的首条执行指令

先将本规划包放到目标产品仓库中。选择你准备使用的 SOL 5.6 执行任务，然后复制下方指令。当前交付只有开发规格和合同示例，尚未创建产品源码或运行产品测试。

## 可直接复制的指令

你负责实现 ProductDirectorAI。请完整读取 `ProductDirectorAI_V1-V6_Codex_Development_Plan.md`、`assets/V6_Automation_UI_reference.png`、`contracts/director-plan.v1.schema.json`、`contracts/director-plan.v1.example.json`，以及当前仓库全部适用工程说明。

先检查仓库、现有实现、未提交改动和运行环境，保留用户工作，复用合适代码。将总规划纳入 `docs/MASTER_PLAN.md`，确保其中参考图和合同文件的相对链接在移动后仍然有效；建立或合并 `AGENTS.md`、`docs/CURRENT_PHASE.md` 与 `docs/EXECUTION_LOG.md`。按完整总规划提取 `docs/phases/V1.md`，保留对共用架构/数据/API/Provider/验收规范的引用。不要现在实现其他版本。

当前仅授权 V1。先执行 V1-00 的 P0 技术验证；P0 通过后继续 V1-01 至 V1-09。完成 V1 后停止，不进入 V2–V6，不自动修改授权版本。

P0 必须实测 Blender Headless、FFmpeg/ffprobe、数据库与可执行 Worker，完成 GLB 导入、尺寸/朝向规范化、hero_orbit/dolly_in/side_track、真实帧序列、MP4 和 metadata。没有用户真实产品模型时，使用明确标识、可再生成的测试模型进行技术验证；不得将测试模型当用户真实产品。没有可用 Blender、节点或必要依赖时，完成安全诊断和不依赖这些条件的工作，记录准确的 BLOCKED 项，不能用占位视频让 P0 通过。

V1 实现独立 Web 工作台：产品图片/3D 入口、产品版本审核、模板三镜头 DirectorPlan、分镜编辑与确认、3D/图片预演、真实渲染任务、真实进度/错误/取消/重试、视频与 Manifest 下载、本地及已配置云端 Worker。界面使用参考图的深色导航、浅色卡片和橙色主动作，按 V1 范围展示功能。

不要提前实现 AI Provider、真人、参考视频、批量自动化或发布。DirectorPlan 的 `fidelity_mode=STRICT` 在 V1 是保护产品的请求约束；只有 V3 的保真链路与 QA 验收完成后才能显示“Strict 保真已验证”。图片输入不能默认获得真实 3D 环绕能力。

每个任务按本期合同实现并运行适当测试，保存任务 ID 对应证据。先给出仓库/环境结论和任务清单，然后开始实施，不要只复述规划。所有 API Key、token 和凭证只能保存在后端凭证存储，不能进入前端、Git、workflow、日志或导出包。

不要自动购买云服务、花费未授权 API 预算或向外部平台发布。遇到缺账号、模型或节点时，继续完成不依赖它的工作，准确标记相关真实集成尚未验收，不以 mock、skip 或静态截图代替。

最终提交 `docs/reports/P0_ENVIRONMENT.md`、`docs/reports/V1_ACCEPTANCE.md`、启动/测试步骤、实际可播放成果及界面截图，说明哪些通过、失败、未测试或 BLOCKED。验收报告完成后停止，等待我明确解锁 V2。

## 之后每期使用

当前已验收版本为 V(n-1)，现在明确授权执行 Vn。读取总规划、Vn 规格、当前状态、前期验收和 ADR。检查实际依赖与旧数据兼容性，列出本期任务 ID，然后实施本期全部必需任务。保留前期可用功能和用户修改，遵守统一 Provider、状态机、保真、预算和发布合同。完成真实链路、恢复、界面与回归测试，输出 `Vn_ACCEPTANCE.md` 及证据后停止，不进入下一期。外部条件缺失写 BLOCKED，不以模拟结果代替真实通过。
