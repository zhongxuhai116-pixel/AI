# V1：实现事实、规格差距与待验收项

> 2026-09-11 补充：下文为前期审计基线。当前已有租约/事件/Worker CLI，以及新增 A05 鉴权、CSRF、存储隔离和 Linux 加密，但未完成真实云端闭环。最新事实与剩余项见 [A05 交接报告](reports/A05_SECURITY_HANDOFF.md)。

审计日期：2026-09-11。依据：仓库代码、历史报告、新云节点命令输出与端到端产物。
本文件不是新验收通过证明。历史记录中的“V1-01”等编号曾作为实现进度编号使用，与主规划任务定义不完全对应；新执行者必须按主规划逐项对照，不能仅凭相同编号标记完成。

## 1. 现有实现

- 前端：React JSX + Vite + Three.js；不是已迁移到 TypeScript 的实现。
- 后端：FastAPI + SQLite；数据库表为 assets、plans、jobs、provider_credentials。
- 执行器：FastAPI BackgroundTasks、进程内线程锁/子进程表；已有 SQLite 租约、epoch、事件续读与独立 worker CLI，但仍是同机数据库/文件系统，不是主规划要求的独立持久 Worker 系统。
- 素材：单文件图片/GLB 上传；本机 var/ 存放原件、数据库和成果。
- 模板：固定 6 秒、24fps、9:16，默认 540×960 三镜头。
- 预演：图片 FFmpeg，GLB Blender + FFmpeg；每次 Run 在作业目录冻结 DirectorPlan。GLB 将三段 `camera`、`focal_length_mm`、`duration_frames` 编译为 Blender 相机关键帧；图片将同一快照编译为 2D 平移/缩放片段。
- MiniMax：中国区认证/测试入口；DPAPI 加密仅实现 Windows 路径。历史生成测试受额度限制，不代表当前额度状态。
- 云 GPU 卡片：历史选型展示，不是当前租赁资源查询或远程调度功能。

## 2. 差距与完成门

| 范围 | 当前事实/缺口 | 后续验收要求 |
| --- | --- | --- |
| 项目与产品版本 | 当前围绕 asset/plan/job；无完整 Project/ProductVersion 审核与不可变版本模型 | 主规划第 4、7 章实体与授权校验 |
| 计划合同 | Pydantic 简化请求与根目录 JSON Schema 不是同一全量合同；未统一验证 | 明确迁移/兼容方案，正负例与语义校验 |
| 分镜编辑影响真实片 | GLB 与图片均已通过真实云端验收：冻结计划、相机模板、焦距和三段时长会进入对应渲染器；图片 `hero_orbit` 是可见的 2D 视差近似 | 保留 GLB/图片快照回归；物理 3D 环绕仅适用于 GLB 或后续多视图资产 |
| 状态与恢复 | 已有状态、取消、SQLite 保存、租约/epoch/事件续读与过期对账；完成/失败已在写事务内校验租约，长任务会续租，取消胜出且终态不可回退（见 [A04 恢复加固](reports/A04_RECOVERY_HARDENING.md)）；仍无 Outbox/SSE 与编码失败自动重试，真实云端中断未测 | 真实中断、重试幂等、取消竞争、重启对账的云端验收 |
| 输出与 QA | 540×960 与 1080×1920 基础云端验收已通过；存在性检查不等于全量质量门 | 黑帧/可见性/用户真实素材复验与持续稳定性 |
| 远程节点 | 新云节点已本机部署并通过 GLB 全链路；尚无受鉴权的远程 Renderer/Worker 调度 | 远程存储、权限、调度、真实任务与恢复 |
| Linux 密钥存储 | Windows ctypes/DPAPI 调用不能直接在 Linux 保存/解密凭证 | 平台安全存储适配；禁止明文回退 |
| API 访问 | 当前本机原型没有完整身份/权限层；前端 API 地址写为 localhost:8000 | 远程接入配置与鉴权；不能直接公网暴露 |
| 安装迁移 | PowerShell 脚本含旧电脑路径回退，依赖检查不完整 | 新用户/路径干净安装并检测每条命令退出码 |
| Blender/GPU | Blender 5.2.1 headless/EEVEE 已锁定并通过；独立采样峰值 GPU 61%、显存 1114 MiB | 保留版本与探针回归；后续 Cycles/OptiX 需另行举证 |
| 视觉验证 | 旧截图为历史证据；新增 GPU 选型卡未有本次可读浏览器证据 | 新电脑复验实际界面，不拿旧图冒充 |
| 厂商 API | MiniMax 仅设置/认证/最小生成测试；未编排 DirectorPlan；无 ComfyUI | V2 明确解锁后按合同实施 |
| 泛产品定位 | 演示图片仍可为拳击产品，但业务不得依赖它 | 使用至少一个非拳击品类做复验 |
| 数据迁移 | SQLite 和 Manifest 存在旧绝对路径；DPAPI 绑定旧环境 | 安全迁移、路径重映射、重新输入凭证并测试 |

## 3. 当前真实 API（前缀 /api/v1）

| 方法 | 路由 | 当前用途 |
| --- | --- | --- |
| GET | /health | 本机组件可用性 |
| GET/POST | /assets | 素材列表、multipart file 上传 |
| GET | /assets/{asset_id}/content | 读取素材 |
| POST | /plans/template | product_asset_id、intent、固定 ratio/duration |
| PATCH | /plans/{plan_id} | intent 与三镜头字段 |
| POST | /plans/{plan_id}/approve | approved |
| POST | /runs | plan_id，创建后台任务 |
| GET | /jobs、/jobs/{job_id} | 列表、详情 |
| POST | /jobs/{job_id}/cancel | 取消请求 |
| GET | /jobs/{job_id}/video、/manifest | 产物下载 |
| GET | /providers/minimax/status | 脱敏状态 |
| POST | /providers/minimax/credentials | 仅后端保存；不要将真实请求写进文档 |
| POST | /providers/minimax/test | 可能产生额度消耗，未授权不执行 |

运行时 /openapi.json 为该代码版本的接口真值；主规划 API 表是目标，不是现有接口清单。不要把 Schema 示例直接 POST 到简化 API。

## 4. 后续施工顺序

1. 在新电脑恢复已有核心；保留源码，不重写工作台。
2. 复验图片/GLB 与关键编辑语义，建立失败用例。
3. 用户继续部署后处理云连接和基础渲染，分开验收接入与出片。
4. 根据主规划补齐仍属 V1 的合同、镜头执行、可靠任务和远程接口；迁移数据库需先方案、备份与回归，不把本次文档审计当作迁移授权。
5. 用户真实产品复验；所有必需门有证据后再报告 READY_FOR_REVIEW，由用户决定 ACCEPTED。
6. V2–V6 已获用户明确授权，但必须逐版本达到退出门，不能跨阶段标记完成。

不得把 SQLite 原型作为主规划 PostgreSQL/租约要求已完成的替代证据。本次不改变实现、不降低主规格。
