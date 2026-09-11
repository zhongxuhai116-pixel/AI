# 当前阶段

- 当前版本：V1 · 3D Director MVP
- 状态：PARTIAL；新云节点 V1 基础设施、GPU 渲染、通用 GLB/图片端到端链路、DirectorPlan 分镜语义及 1080×1920 基线导出已 PASS，完整产品合同、可靠任务、鉴权和用户真实素材仍待验收
- 下一版本：V2 已获用户明确授权，但必须等 V1 阶段门完成后再进入
- 当前优先：补齐 V1 产品差距并逐项验收；当前已完成 A01（环境与记录基线）、A02（合同与版本约束）、A03（Run 幂等与上下文）、A04（独立执行与恢复），下一步进入 A05（安全远程闭环）。
- 加速执行入口：[V1 → V6 加速执行计划](V6_ACCELERATION_PLAN.md)；云端 H3 已确认存在，V2 继续按现有环境接入排期，不重复安装，不替代 V1 关键门。
- 外部 API：旧电脑历史记录显示 MiniMax 中国区认证曾通过、文本生成曾受额度限制；DPAPI 凭证不可直接迁移，当前不调用收费 API
- A01 完整验收结果（2026-09-11 14:11:19）：
  - A01-01：通过（Python、requirements、compileall 可跑通）
  - A01-02：文档基线维持（本机执行结果与前期云端核验已归档；如需本机补验，需提供可连接云端 SSH 后再重跑）
  - A01-03：本机 `/.`  API 健康链路通过（`/api/v1/health` 返回 ok）；前端 build/test 仍受 `npm` 缺失阻塞
  - A01-04：文档入口与变更记录已保持更新
  - A01-05：无新增敏感凭据或产物写入 Git

  关键阻塞项：`npm`/`npx`/`vite`（当前运行时仅提供 node），`ffmpeg` / `ffprobe` CLI 可见性未生效（`health` 接口上 `available=false`），暂不能关闭 A01-03 的前端与 Sites 复核项。
- A02 完整验收结果（2026-09-11 15:26:00）：
  - `tests/test_job_control.py::test_a02_contract_version_is_immutable_on_plan_update` PASS（计划更新时新版本合同落地，旧版本保留）
- A03 完整验收结果（2026-09-11 15:26:00）：
  - 同键复用幂等：`test_a03_run_idempotency_conflict` PASS（同键同内容复用同一 `run_id/job_id`）
  - 冲突检测：`test_a03_run_idempotency_conflict` PASS（同键不同内容返回 409）
  - 上下文访问：`test_a03_run_access_requires_project_contract_context` PASS（越权 project 场景返回 403）
- A04 完整验收结果（2026-09-11 16:05:00）：
  - Worker 领取/心跳/完成/失败接口已落地。
  - 旧租约 epoch 回报会返回 409；租约过期后新 worker 可重新领取。
  - `job_events` 持久化任务事件，`/api/v1/jobs/{job_id}/events` 支持 `Last-Event-ID` 续读。
  - 独立 worker 命令入口已落地：`python -m productdirector_api.main worker --once --worker-id <id>`。
  - 过期 RUNNING 租约可通过 `/internal/v1/workers/reconcile` 对账回 QUEUED。
  - 后端 `compileall` 与 `unittest discover` 通过（16/16）。

## 已实现范围

- 通用产品图片/GLB 上传与素材库
- 模板三镜头 DirectorPlan、镜头名称/机位/焦距/时长编辑、保存、确认和每作业冻结快照；GLB 将字段编译为 Blender 相机动作，图片预演将同一快照编译为 2D 平移/缩放镜头
- 图片 FFmpeg 预演、GLB Blender Headless 三镜头渲染
- SQLite 持久化任务、真实状态/阶段/进度、取消、错误记录
- MP4 与 metadata.json 下载
- 设置页 MiniMax 密钥输入、Windows 用户级加密保存、认证和最小生成测试
- 优云智算新节点已部署 Blender 5.2.1、FFmpeg 4.4.2、Node 22.23.2 与 ProductDirectorAI；API/Web 仅监听 localhost，systemd 服务已启用
- 云端通用 GLB 全链路已生成 540×960、24fps、144 帧、6 秒 H.264；GPU 探针记录 RTX 4090 峰值利用率 61%、显存 1114 MiB
- 云端 1080×1920 回归作业 `bdaee1e7-8109-4610-b0e0-4dd25d243714` 已完成：H.264、1080×1920、24fps、144 帧、6 秒，产物 SHA-256 `0c0f3603e98fad32f8e0d8f66c9a1f35bd812154f8b76c9700b308a848ab62ed`
- 已验收三镜头修改真正驱动 Blender：85mm 定格 24 帧、24mm 侧向移动 72 帧、55mm 环绕 48 帧；Manifest 记录冻结计划 SHA-256
- 已验收同一组修改真正驱动图片 FFmpeg 成片：四个边界帧全部不同，输出为 540×960、24fps、144 帧、6 秒；图片的 `hero_orbit` 明确是 2D 视差近似，不宣称物理 3D 环绕
- 深色侧栏、浅色卡片、橙色主动作的 V1 工作台

## 暂不实现

- MiniMax 参与 DirectorPlan、ComfyUI、字幕、配音、BGM（属于后续版本，不计入 V1）
- Platform Profile、批量任务、成本中心、Automation API、一键发布
- 云端 Worker 的远程创建/启停/调度与外部平台发布（已授权后续版本，但不属于 V1）

## 继续施工入口

从 `docs/V1_IMPLEMENTATION_GAPS.md` 的剩余门逐项推进；`A01`–`A04` 的执行依据为 `docs/reports/A01_ENVIRONMENT_BASELINE.md`、`docs/reports/A02_A03_ACCEPTANCE.md` 与 `docs/reports/A04_ACCEPTANCE.md`。下一步执行 A05：同源会话/CSRF、Worker 身份边界、远程 API 基址配置与 Linux 凭证适配。云端证据见 `docs/reports/CLOUD_GPU_ACCEPTANCE.md`、`docs/reports/V1_DIRECTORPLAN_ACCEPTANCE.md` 与 `docs/CLOUD_SERVER_HANDOFF.md`。V2 选择 MiniMax H3 的开放权重路线；现有单卡量化环境只作为后续集成候选，先完成 V1，不跨阶段宣布完成。

当前阻塞项：需补齐 `npm`、前端 `vite` 路径可读权限（当前出现 `Access is denied` 导致 `vite.config.mjs` 加载失败）、`blender`、`ffmpeg` / `ffprobe` 命令可执行性后，才能完成前端 build/sites 复核与真实渲染联调。
