# 当前阶段

> 2026-09-12 最新结论：V1 仍为 PARTIAL，但本轮代码**已真实部署到云节点**（HEAD `8e6fe3c`），云端图片与 GLB 双链路真实出片通过，A05 鉴权在云端生效（匿名 401 / 带令牌 200），web 服务崩溃重启已修复。本地后端 28/28、前端 build、Sites 4/4 通过；本地图片链路真实出片通过。证据见 [云端部署与双链路出片验收](reports/CLOUD_DEPLOY_A05_ACCEPTANCE.md)、[A04 恢复加固](reports/A04_RECOVERY_HARDENING.md)、[本机 FFmpeg 验收](reports/V1_LOCAL_FFMPEG_E2E.md)。先读 [最新继续入口](NEXT_COMPUTER_START.md)。下方分时记录的“完整验收”标题仅反映历史局部检查。

- 当前版本：V1 · 3D Director MVP
- 状态：PARTIAL；新云节点 V1 基础设施、GPU 渲染、通用 GLB/图片端到端链路、DirectorPlan 分镜语义及 1080×1920 基线导出已 PASS，完整产品合同、可靠任务、鉴权和用户真实素材仍待验收
- 下一版本：V2 已获用户明确授权，但必须等 V1 阶段门完成后再进入
- 当前优先：在换电脑后的本机继续推进 V1。A01–A03 已有局部实现/证据，完整退出门仍须按主规划核对；A04 的事务内租约、长任务续租、取消竞争与重领已加固并回归通过，但真实云端中断恢复未验收；A05 真实远程闭环尚未完成。不再笼统声明 A01–A04 全部完成。
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
- A04 恢复加固（2026-09-11 换电脑后复跑，仍 PARTIAL）：
  - 后端回归 **28/28** 通过；新增 5 个用例覆盖取消竞争、旧 worker 写入拒绝、长任务续租正负对照、进程被杀后由新 worker 完成。
  - 修复真实缺陷：租约到期时间按整秒写入却与带微秒的当前时间做字符串比较，会在同一秒内被误判过期。
  - 关闭竞争窗口：完成/失败的状态写入、租约释放与事件写入合并为单一写事务；`claim_job()` 使用写事务；长任务由 `HeartbeatKeeper` 周期续租。
  - 语义明确：取消胜出、终态不可回退、失去租约的执行器不再改写任务与产物。
  - 仍未验收：真实 Blender/FFmpeg 长任务的进程被杀重领、远程任务输入下载与成果回收。见 [A04 恢复加固报告](reports/A04_RECOVERY_HARDENING.md)。

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
- 换电脑后本机真实（非 mock）图片链路复验通过：1080×1920、H.264、144 帧、6 秒，边界帧六张哈希互不相同，产物与清单哈希见 [本机 FFmpeg 验收](reports/V1_LOCAL_FFMPEG_E2E.md)；本机无 Blender，GLB 未在本机复验
- 云端已部署本轮代码（`8e6fe3c`）：修复 web 服务缺 `node_modules` 导致的崩溃重启；新增 `/etc/productdirector/v1.env`（root 0600）保存 Owner/Worker/Fernet 密钥并接入 systemd；匿名访问返回 401
- 云端双链路真实验收通过：图片作业 3.0 秒、GLB 作业 63.2 秒（真实 Blender 渲染 144 帧），均为 H.264、1080×1920、24fps、144 帧、6 秒，边界帧六张哈希互不相同；GPU 采样峰值 75%、显存 1641 MiB
- A06 进行中：媒体质量门已落地（镜头边界取样帧的均值/标准差 + `blackdetect` 黑屏占比，不通过即阻断作业并写入 Manifest），本地与云端真实链路均通过；失败/取消后的重试入口已实现并部署（新增 attempt、清错误与旧产物引用、写 `job.retry_requested` 事件，前端有重试按钮）。视角确认与 UI 浏览器端到端验收尚未完成，见 [A06 用户流程与质量](reports/A06_USER_FLOW_QUALITY.md)
- A07 进行中：两张用户真实产品图（个人护理电器、桌面机器人摄像头，均非拳击品类）已在本地与云端跑通端到端出片并留存哈希；横向素材的构图问题已由裁切锚点解决；第二张图的五个正交视角可在 V3 产品保真阶段复用。仍缺真实产品 GLB 与同 GLB 三次真实作业，见 [A07 V1 阶段验收](reports/A07_V1_ACCEPTANCE.md)
- A06 主要项已完成：媒体质量门（黑帧/可见性）、失败/取消重试、构图锚点与实时预览、真实 Chrome 端到端走查（桌面/窄屏/失败态）。剩「缺纹理 / 磁盘不足」的明确处理，见 [A06 用户流程与质量](reports/A06_USER_FLOW_QUALITY.md)
- 深色侧栏、浅色卡片、橙色主动作的 V1 工作台

## 暂不实现

- MiniMax 参与 DirectorPlan、ComfyUI、字幕、配音、BGM（属于后续版本，不计入 V1）
- Platform Profile、批量任务、成本中心、Automation API、一键发布
- 云端 Worker 的远程创建/启停/调度与外部平台发布（已授权后续版本，但不属于 V1）

## 继续施工入口

从 `docs/V1_IMPLEMENTATION_GAPS.md` 的剩余门逐项推进；`A01`–`A04` 的执行依据为 `docs/reports/A01_ENVIRONMENT_BASELINE.md`、`docs/reports/A02_A03_ACCEPTANCE.md`、`docs/reports/A04_ACCEPTANCE.md` 与 `docs/reports/A04_RECOVERY_HARDENING.md`。A05 的同源会话/CSRF、Worker 身份边界、远程 API 基址配置与 Linux 凭证适配已在本地落地，剩余工作是受控远程闭环（服务器登录、部署、授权任务输入与成果回收）；它的前置仍是本机全链路先能跑通。云端证据见 `docs/reports/CLOUD_GPU_ACCEPTANCE.md`、`docs/reports/V1_DIRECTORPLAN_ACCEPTANCE.md` 与 `docs/CLOUD_SERVER_HANDOFF.md`。V2 选择 MiniMax H3 的开放权重路线；现有单卡量化环境只作为后续集成候选，先完成 V1，不跨阶段宣布完成。

当前阻塞项：云 SSH 在建立连接前超时，本轮真实渲染和云端恢复联调未执行。前端构建阻塞已解除，npm lockfile 已刷新，build/sites 通过；新电脑按最新入口复验。A04/A05 具体代码缺口见交接报告。
