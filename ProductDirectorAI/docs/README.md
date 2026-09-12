# 文档总入口

更新日期：2026-09-12。适用于换电脑、换执行模型和按阶段继续到 V6。

## 先读这四份

**先读 [下一次操作交接说明](HANDOFF_NEXT_SESSION.md)**——它记录 V1 / V2 已 ACCEPTED、V3 做到哪一步、本轮推送到 GitHub 的内容和下次开工的入口。[NEXT_COMPUTER_START.md](NEXT_COMPUTER_START.md) 是 2026-09-11 换电脑时的历史入口，其中"V1 仍为 PARTIAL""云 SSH 超时"等描述已过时。

1. [下一次操作交接说明](HANDOFF_NEXT_SESSION.md)：当前状态、访问方式、下次开工步骤与工程纪律。
2. [当前阶段](CURRENT_PHASE.md)：当前范围、阻断与下一步。
3. [V3 任务书与排查记录](reports/V3_TASK_BRIEF.md)：V3-01…V3-10 的清单、当前进度与 Blender 5 踩坑记录。
4. [换电脑接手手册](HANDOFF_NEW_COMPUTER.md) / [云服务器交接记录](CLOUD_SERVER_HANDOFF.md)：克隆、恢复环境、私人数据、凭证与新节点运行时事实。

## 全部文档索引

| 文件 | 用途与状态 |
| --- | --- |
| [完整 V1–V6 规划](../ProductDirectorAI_V1-V6_Codex_Development_Plan.md) | 唯一主规格；用户已授权逐阶段推进到 V6 |
| [下一次操作交接说明](HANDOFF_NEXT_SESSION.md) | 当前状态、GitHub 归档范围与下次开工步骤；取代 2026-09-11 的换电脑入口 |
| [V1 → V6 加速执行计划](V6_ACCELERATION_PLAN.md) | 复用云端已安装 H3；七个 V1 收尾工作包、阶段依赖、验收和 GitHub 记录方式 |
| [V3 任务书与排查记录](reports/V3_TASK_BRIEF.md) | V3-01…V3-10 清单、进度、风险，以及 Blender 5.2 合成器 API 的完整踩坑记录 |
| [A01 环境与记录基线](reports/A01_ENVIRONMENT_BASELINE.md) | V1 收尾第一包：环境版本、构建复核、云端基线记录与变更约束 |
| [A02 / A03 验收](reports/A02_A03_ACCEPTANCE.md) | 合同不可变与幂等/重入访问验收记录 |
| [A04 验收](reports/A04_ACCEPTANCE.md) | Worker 租约、事件恢复与旧 epoch 拒绝的第一段验收记录 |
| [A04 恢复加固](reports/A04_RECOVERY_HARDENING.md) | 换电脑后复跑 28/28；事务内租约、长任务续租、取消竞争与租约时间缺陷修复 |
| [本机真实 FFmpeg 图片链路验收](reports/V1_LOCAL_FFMPEG_E2E.md) | 新电脑上真实（非 mock）跑通图片链路并留存 1080×1920 产物哈希；GLB 未在本机复验 |
| [云端部署与双链路出片验收](reports/CLOUD_DEPLOY_A05_ACCEPTANCE.md) | 本轮代码部署到云节点；图片与 GLB 双链路真实出片、A05 鉴权生效、GPU 采样与产物哈希 |
| [A06 用户流程与质量](reports/A06_USER_FLOW_QUALITY.md) | 媒体质量门（黑帧/可见性）实现与本地/云端验证；其余 A06 任务进行中 |
| [A07 V1 阶段验收](reports/A07_V1_ACCEPTANCE.md) | 用户真实产品图（非拳击品类）端到端出片、视觉复核与门状态清单；含待处理构图问题 |
| [A03 PostgreSQL 迁移与恢复演练](reports/A03_POSTGRES_DRILL.md) | 隔离库迁移 + pg_dump 恢复 + 逐表内容摘要核对 PASS；数据库层可切换，真实 API 已在 PostgreSQL 上出片；线上仍未切换 |
| [V2 H3 Provider 接入](reports/V2_H3_PROVIDER_INTEGRATION.md) | H3/ComfyUI 作为 Provider 接入：提交、轮询、下载校验、素材库登记全链路真实通过 |
| [V2 AI 导演](reports/V2_AI_DIRECTOR.md) | 描述 → 合法可编辑计划；10/10 样本通过验收门槛，含校验与两次修复链路 |
| [ADR-002 生成能力本地优先](decisions/ADR-002-LOCAL-FIRST-PROVIDERS.md) | 优先使用自托管 MiniMax H3；官方付费 API 降为可选，并记录本地 LLM 调查结论 |
| [执行指令](../CODEX_SOL56_START_HERE.md) | 给下一台电脑上的 Codex/SOL 5.6；先审计、复验、保留已有代码 |
| [工程约束](../AGENTS.md) | 版本、安全、测试与用户工作保护 |
| [项目 README](../README.md) | 当前项目和本机启动入口 |
| [执行记录](EXECUTION_LOG.md) | 历史工作和本次交接事实 |
| [GPU 历史选型](GPU_CLOUD_SELECTION.md) | 保留候选比较，已购节点事实以云交接记录为准；价格非实时报价 |
| [V1 验收](reports/V1_ACCEPTANCE.md) | 本机核心历史结果；完整 V1 为 PARTIAL，非负责人验收 |
| [V1 云 GPU 验收](reports/CLOUD_GPU_ACCEPTANCE.md) | 新节点环境、GPU 探针、API 全链路与未覆盖项 |
| [V1 分镜语义验收](reports/V1_DIRECTORPLAN_ACCEPTANCE.md) | 三段镜头编辑、冻结快照、Blender 相机与图片 2D 预演证据 |
| [P0 环境](reports/P0_ENVIRONMENT.md) | 旧电脑环境和历史测试，不代表新电脑/云端 |
| [视觉 QA](../design-qa.md) | 旧界面截图范围；GPU 卡和迁移后界面未复验 |
| [差异决策记录](decisions/ADR-001-V1-PROTOTYPE-BASELINE.md) | React JSX、SQLite、后台执行器与目标规格的区别 |
| [文档校验](../DOCUMENT_VALIDATION.md) | 本次校验范围、结果与局限 |
| [合同说明](../contracts/README.md) | 目标 Schema，不是当前 API 全量实现 |
| [V1 JSON Schema](../contracts/director-plan.v1.schema.json) | 规划级结构校验 |
| [有效合同示例](../contracts/director-plan.v1.example.json) | 非用户真实素材/ID；不可直接当运行 API 请求 |
| [V6 原始参考图](../assets/V6_Automation_UI_reference.png) | UI 风格依据，示例数字/产品不构成真实配置 |

历史 UI 证据保留在 `docs/reports/`。未经脱敏的登录、服务器控制台、聊天和 API 密钥截图不加入交付包。

## V1–V6 阅读地图

| 版本 | 主规划章节 | 重点 | 当前状态 |
| --- | --- | --- | --- |
| V1 | 第 7 章 | 素材、模板导演、Blender/FFmpeg、任务、导出 | **ACCEPTED**（2026-09-12 用户确认） |
| V2 | 第 8 章 | AI Director、Provider、ComfyUI、MiniMax H3 | **ACCEPTED**（同日确认） |
| V3 | 第 9 章 | 产品保真、多视图、分层、QA | **进行中**：V3-01…V3-04 通过，V3-05 核心通过（有未完成项），V3-06…V3-10 未开工 |
| V4 | 第 10 章 | 人体 Proxy、动作、接触与交互审核 | 已授权；未实施，不能越级 |
| V5 | 第 11 章 | 参考视频分析、镜头语言重新演绎 | 已授权；未实施，不能越级 |
| V6 | 第 12 章 | Profile、字幕/配音/BGM、Automation API、批量、发布包、资源成本、一键发布 | 已授权；未实施，不能越级 |

每期的目标、界面、流程、模块、数据、API、Provider、状态、测试、禁止事项、施工任务和退出条件均保留在主规划，不再创建多套可能漂移的阶段规格。

## 事实优先级

用户最新明确请求 > 适用工程约束 > 当前状态/交接事实 > 主规划中的目标规格 > 历史报告。
这不是自动扩大授权的规则。发现规格冲突时记录差异，不静默降低验收标准。
