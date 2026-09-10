# 文档总入口

更新日期：2026-09-10。适用于换电脑、换执行模型和继续 V1。

## 先读这四份

1. [换电脑接手手册](HANDOFF_NEW_COMPUTER.md)：克隆、恢复环境、私人数据、凭证与接手提示。
2. [当前阶段](CURRENT_PHASE.md)：当前范围、阻断与下一步。
3. [V1 实现与规划差距](V1_IMPLEMENTATION_GAPS.md)：不要把原型或历史 PASS 当作完整 V1 验收。
4. [云服务器交接记录](CLOUD_SERVER_HANDOFF.md)：已完成扩容、GPU 核验和 SSH 排查；尚未部署 Blender。

## 全部文档索引

| 文件 | 用途与状态 |
| --- | --- |
| [完整 V1–V6 规划](../ProductDirectorAI_V1-V6_Codex_Development_Plan.md) | 唯一主规格；V2–V6 未授权实施 |
| [执行指令](../CODEX_SOL56_START_HERE.md) | 给下一台电脑上的 Codex/SOL 5.6；先审计、复验、保留已有代码 |
| [工程约束](../AGENTS.md) | 版本、安全、测试与用户工作保护 |
| [项目 README](../README.md) | 当前项目和本机启动入口 |
| [执行记录](EXECUTION_LOG.md) | 历史工作和本次交接事实 |
| [GPU 历史选型](GPU_CLOUD_SELECTION.md) | 保留候选比较，已购节点事实以云交接记录为准；价格非实时报价 |
| [V1 验收](reports/V1_ACCEPTANCE.md) | 本机核心历史结果；完整 V1 为 PARTIAL，非负责人验收 |
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
| V1 | 第 7 章 | 素材、模板导演、Blender/FFmpeg、任务、导出 | 本机核心已有实现；完整退出门未通过 |
| V2 | 第 8 章 | AI Director、Provider、ComfyUI、MiniMax | 规格，未实施/未授权 |
| V3 | 第 9 章 | 产品保真、多视图、分层、QA | 规格，未实施/未授权 |
| V4 | 第 10 章 | 人体 Proxy、动作、接触与交互审核 | 规格，未实施/未授权 |
| V5 | 第 11 章 | 参考视频分析、镜头语言重新演绎 | 规格，未实施/未授权 |
| V6 | 第 12 章 | Profile、字幕/配音/BGM、Automation API、批量、发布包、资源成本、一键发布 | 规格，未实施/未授权 |

每期的目标、界面、流程、模块、数据、API、Provider、状态、测试、禁止事项、施工任务和退出条件均保留在主规划，不再创建多套可能漂移的阶段规格。

## 事实优先级

用户最新明确请求 > 适用工程约束 > 当前状态/交接事实 > 主规划中的目标规格 > 历史报告。
这不是自动扩大授权的规则。发现规格冲突时记录差异，不静默降低验收标准。

