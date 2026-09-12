# V1 执行记录

## 2026-09-11 · 换电脑保存点

- 用户要求同步 GitHub 后更换电脑。新增 `NEXT_COMPUTER_START.md`、`reports/A05_SECURITY_HANDOFF.md`，统一继续入口，纠正“仅授权 V1”和“A01–A04 全部完成”的旧口径。
- 完成本轮 A05 私有单 Owner 会话/CSRF、Worker HTTP 认证与身份、Owner/项目/素材检查、同源代理、相对文件引用和 Linux Fernet 加密。缺配置时拒绝匿名访问，密钥不进入前端构建。
- 本地 compileall、后端 23/23、Vite build、Sites 4/4 通过。刷新 npm lockfile，增加测试依赖清单。worker-once 渲染测试为 mock，不代表真实成片。
- 本次 SSH 超时；未登录、未部署、未运行云任务、未调用真实 Provider。A04/A05/V1 保持 PARTIAL，后续具体缺口见新报告。
- Git 同步仅包括源码、测试、锁文件和脱敏文档；模型、私钥、会话/Provider 凭证、数据库、素材及成片不进仓库。版本以包含此保存点的 Git 提交为准。

## 2026-09-11

- A04 第一段验收：新增 Worker 租约/事件恢复基线，并保持原有 BackgroundTasks 兼容入口。
  - 数据库新增/迁移：`run_jobs.lease_owner`、`run_jobs.lease_expires_at`、`run_jobs.lease_epoch`、`job_events`。
  - 内部接口新增：`/internal/v1/workers/claim`、`/heartbeat`、`/complete`、`/fail`；公开事件接口新增 `/api/v1/jobs/{job_id}/events`，支持 `Last-Event-ID`。
  - 修复 Run 创建链路，确保 `jobs` 与 `runs/run_jobs/job_attempts` 同步创建，不只返回 job_id。
  - 新增测试：旧租约 epoch 完成回报被 409 拒绝，租约过期后新 worker 可重新领取；事件流可续读。后端 `compileall` 通过，`unittest discover` 14/14 通过。
  - 前端 build 与 Sites worker 测试仍因本机 `npm` 不存在而无法执行，未记为通过。
- A04 完整验收补齐：新增独立 worker 命令入口、过期 RUNNING 租约对账和 worker-once 执行测试。
  - 命令入口：`python -m productdirector_api.main worker --once --worker-id <id>`；本机冒烟输出 `{"claimed": false}`。
  - 对账接口：`POST /internal/v1/workers/reconcile`，可将过期租约的 RUNNING 任务恢复为 QUEUED。
  - 新增测试：`test_a04_reconcile_requeues_expired_running_job`、`test_a04_worker_once_executes_claimed_job`；后端 `compileall` 通过，`unittest discover` 16/16 通过。
  - 当前阶段推进到 A05；真实 Blender/FFmpeg 长任务的“杀 worker 后自动重领并完成”仍需在具备工具链的云端实测。
- A02/A03 完整验收：在本地 `.venv` 环境下执行 `python -m unittest discover -s tests -p 'test_*.py'`（12/12 通过）。重点回归结果：
  - A02：更新计划会新建并返回新合同版本，历史版本不被覆盖；`test_a02_contract_version_is_immutable_on_plan_update` 通。
  - A03：同一 `idempotency_key` 与相同请求哈希会复用同一 `run_id/job_id`；同键变更内容会返回 409；跨项目上下文越权请求返回 403。
  - 修正 `update_job` 里的 `run_jobs` 状态同步列映射：`progress` 不再写入 `run_jobs`（该表无该列），保证 `runs/create_job` 统一状态更新链路稳定。
- 新增验收记录：`docs/reports/A02_A03_ACCEPTANCE.md`（含三条核心用例结论与下一步入口）。
- A01 完整验收补测（本轮）：在 `work/github-document-sync/ProductDirectorAI` 使用 `pwsh` 执行本机基线复核与 API 健康核验。
  - `python` 依赖与 `compileall` 通过；
  - `PYTHONPATH=apps/api` 下 `uvicorn productdirector_api.main:app --port 18080` 后，`/api/v1/health` 返回 `status=ok`，但返回中 `ffmpeg/ffprobe available=false`。
  - `npm`、`npx` 均未检测到，前端 `npm run build` 与 `npm run test:sites` 未执行，A01-03 的前端复核仍 BLOCKED。
  - 脚本 `scripts/check-a01-env.ps1` 已重建并可由 `pwsh` 正常执行（`powershell.exe`（WinPS 5.1）存在解析兼容问题，建议统一使用 `pwsh`）。

- 规划交付：新增 `V6_ACCELERATION_PLAN.md` 并加入文档入口。用户再次确认云端已安装 MiniMax H3；按“已安装、待项目接入”规划 V2，保留原主规格中的其他必需 Provider 验收。V1 拆为 A01–A07 七个收尾工作包，V6 保持 A/B/C 三个门；本次不改代码、不改变阶段通过状态、不执行模型安装或真实数据迁移。
- A01 基线启动：新增 `docs/reports/A01_ENVIRONMENT_BASELINE.md`（环境版本、构建复核、云端基线、记录约束），并将 `CURRENT_PHASE.md` 更新为“开始 A01”。本次仅进行文档层可复现基线，不启动代码/数据迁移；后续将以该文档作为 A01 的验收输入。
- A01 基线复核：本地完成环境最小检查（`.\.venv\Scripts\python.exe --version`=Python 3.12.14、requirements 安装通过、`compileall` 成功），确认 `node` 可运行但 `npm` 不在 PATH，`blender`/`ffmpeg`/`ffprobe` 未找到，且未发现 `apps/web/package-lock.json`，`node_modules` 为空；已将阻塞项写入 `A01_ENVIRONMENT_BASELINE.md`，作为下一步修复输入。
- A01 自动化：新增 `scripts/check-a01-env.ps1`，用于一键输出基线快照并回写 `docs/reports/A01_ENVIRONMENT_BASELINE.md`（含 Python 版本、依赖安装、compileall、工具可见性、npm 构建/测试状态）。本次仍未完成 A01-03 的 full build/test，因为本机缺少 `npm`、`blender`/`ffmpeg` 命令可见性。
- V1-06 PASS：后端 `PlanRequest` 支持 `output` 规格（540×960 / 1080×1920）、执行期按快照输出规格校验宽高/帧率/帧数；前端导演台新增输出分辨率选择；`tests/test_director_plan.py` 增加 1080 兼容构建用例。已补齐云端回归与现网验收。
- V1-06b PASS（本地验收）：补充 `OutputSpec` 与输出规格链路的单测覆盖，`python -m unittest tests/test_director_plan.py` 通过（9/9）；新增失败边界覆盖（`duration_seconds` 与 `output.duration_seconds` 不一致、非 9:16 输出会被拒绝、`ffprobe` 帧率字段非标准格式容错）；修复测试环境下 `ffmpeg.exe` 路径扫描权限导致的导入中断（WinGet 目录 OSError 兜底）。  
  - 本地限制：当前工作站未检测到 `npm` 命令，无法在本地完成前端 build 与 Sites worker 测试；需在具备 Node/NPM 的环境补跑 build（`apps/web`）与后续 worker 回归测试。
- V1-06c PASS（云端验收）：在新云节点执行 1080×1920 的真实 DirectorPlan 作业复验，参数为 `width=1080` / `height=1920` / `fps=24` / `duration_seconds=6`，作业 `bdaee1e7-8109-4610-b0e0-4dd25d243714` 终态 `SUCCEEDED`。manifest 记录 `1080 / 1920 / 24 / 144 / 6.0`；预览文件 SHA-256 为 `0c0f3603e98fad32f8e0d8f66c9a1f35bd812154f8b76c9700b308a848ab62ed`，`metadata.json` SHA-256 为 `97390ee5e5ba5a50c0ae0b6514a502fb8206e7d5351839af3368acdbb47afe3b`。Python 合同测试维持 4/4；与前版本相比该 run 保留冻结 DirectorPlan 快照与计划哈希，且无 `blackdetect` 黑帧告警。
- 新优云智算节点公网 SSH 已通过专用 ED25519 密钥连接；首次连接前从网页终端核对服务器 ED25519 指纹。仓库和文档不保存地址、实例 ID、密码或私钥。
- CLOUD-01 PASS：Ubuntu 22.04.4、RTX 4090 24564 MiB、驱动 570.153.02、16 核/94GB；新节点根分区约 97GB，不能沿用旧节点“291GB 已扩容”的结论。
- CLOUD-02 PASS：安装官方 Blender 5.2.1 LTS、官方 Node.js 22.23.2、Ubuntu FFmpeg/ffprobe 4.4.2；Blender 与 Node 下载均完成官方 SHA-256 校验。
- CLOUD-03/04 PASS：Blender 5.2.1 headless 生成 12 帧，独立 nvidia-smi 采样记录 GPU 峰值 61%、显存 1114 MiB；不再仅凭“能看到 4090”宣称 GPU 生效。
- CLOUD-05 PASS：通过真实 API 上传通用 GLB、创建/批准计划、启动任务并完成 Blender + FFmpeg 全链路。产物为 H.264、540×960、24fps、144 帧、6.000 秒；视频 SHA-256 为 `fadc6f3b41579289c1a81089554bd47df6d7b13d12a3d76fbf4ac026515baef7`。
- API 与 Vite Preview 通过 systemd 启用，仅监听 `localhost` 的 8000/4173 端口；未新增公网端口，也没有把无鉴权服务直接暴露到互联网。
- 云端复验：Python compileall、Vite build、Sites worker 测试 4/4 通过。`npm ci` 因仓库 lockfile 与 package.json 不同步失败，临时使用 `npm install --no-package-lock --no-save` 完成验收；锁文件差距仍须后续修复。
- 阶段判定：V1 云基础设施与通用 GLB 端到端门 PASS；V1 产品总状态仍为 PARTIAL，剩余差距见 `V1_IMPLEMENTATION_GAPS.md`。
- CLOUD-05b PASS：DirectorPlan 编辑现在影响真实 GLB 成片。云端作业以 85mm 定格 24 帧、24mm 侧移 72 帧、55mm 环绕 48 帧完成 6 秒视频；独立打开 scene.blend 验证相机关键帧，Manifest 记录冻结计划 SHA-256。Python 合同测试 3/3、前端 build 与 Sites worker 测试 4/4 通过。
- CLOUD-05c PASS：图片预演读取同一作业冻结计划，按 24 / 72 / 48 帧生成并拼接三段 2D 平移/缩放。真实 PNG 任务输出 H.264、540×960、24fps、144 帧、6.000 秒；四个边界帧哈希均不同，Manifest 含计划快照 SHA-256。Python 合同测试扩展为 4/4。单图 `hero_orbit` 明确是视差近似，未冒充物理 3D 环绕。
- 服务器状态复核：当前根盘可用约 19GB。发现独立 ComfyUI H3 单卡量化环境和可解码试验产物；它没有接入 ProductDirectorAI，不计入 V2 验收，也不重复下载模型。
- 用户明确授权按阶段继续到 V6，并要求每一步验收后前进。V2 首选 MiniMax H3 开放权重路线；官方完整 BF16 FL2VA 目录约 144GB，官方 SGLang 还提供单卡 RTX 4090 量化/卸载路径。后续 V2 优先复用现有受控环境完成集成证据。

## 2026-09-10

> 本节多数为旧电脑同日历史记录，不等于新电脑复验或完整 V1 负责人验收；当前总状态见 `CURRENT_PHASE.md`。

- V1-00：安装并验证 Blender 5.2.1 LTS、FFmpeg 9.0.1、Python/FastAPI、Node/Vite。
- V1-01：建立 React 工作台及参考 V6 图的信息架构；V1 只显示当前阶段功能。
- V1-02：实现通用 PNG/JPEG/WebP/GLB 上传、哈希、大小限制、本地素材存储。
- V1-03：实现固定 6 秒、24 fps、9:16 的三镜头模板 DirectorPlan。
- V1-04：实现分镜编辑、PATCH 持久化与显式批准。
- V1-05：实现 GLB 浏览器预览、Blender 模型归一化、推近/侧移/环绕三段相机动画。
- V1-06：实现图片 FFmpeg 预演，明确不把图片描述为真实三维环绕。
- V1-07：实现 SQLite 作业队列、真实状态、阶段、进度、取消与错误记录。
- V1-08：实现 MP4 与 Manifest 下载、产物存在性和大小检查。
- V1-09：完成图片与 GLB 端到端验收、前端构建、Worker 测试和视觉对照。
- 用户增量：在设置页加入 MiniMax 中国区 Provider 配置；密钥通过 Windows DPAPI 加密保存。认证与模型列表测试通过，最小文本生成返回 429 / 2056（Token Plan 用量上限）。
- 用户增量：核对优云智算创建页与官方计费，确认截图中的单卡 RTX 5090 32GB、14C64GB、按量计费适合 V1；要求系统盘至少 100GB，ComfyUI/视频建议 200GB 或独立云盘。
- 用户增量：增加 `docs/GPU_CLOUD_SELECTION.md` 与设置页 GPU 选型卡。主平台建议优云智算，AutoDL 作为低成本开发/备用；V1 不调用云平台 API、不创建实例、不产生费用。
- 后续事实覆盖购买前选型：用户实际购买优云智算华北二 A、RTX 4090 24GB、16 核/94GB、Ubuntu 22.04.4 节点；根分区已扩容到约 291G。网页 SSH 成功，旧电脑公网 SSH 在认证前超时且监听窗口未观察到对应 SYN，原因未定。
- 云部署状态：Blender、FFmpeg 和 ProductDirectorAI 尚未部署；ComfyUI 不属于 V1 退出门。最新请求暂停服务器排查/部署，优先完成 GitHub 文档与新电脑交接。
- 交接文档：新增 `docs/HANDOFF_NEW_COMPUTER.md`、`docs/CLOUD_SERVER_HANDOFF.md`、`docs/V1_IMPLEMENTATION_GAPS.md`、`docs/decisions/ADR-001-V1-PROTOTYPE-BASELINE.md`，并统一入口与历史验收口径。

## 关键决定

- 拳击产品仅为演示文件；默认文案、API、数据模型与渲染逻辑不绑定产品类别。
- MiniMax V1 仅做安全配置和连通测试；生成式 DirectorPlan Provider 编排留到 V2。
- 本机优先：素材、SQLite、帧、视频和清单全部保存在 `var/`，该目录不提交 Git。

## 2026-09-11（新电脑 · A04 恢复加固）

本节为换电脑后的首次真实执行记录，证据见 [A04 恢复加固与新电脑复验](reports/A04_RECOVERY_HARDENING.md)。A04 状态仍为 **PARTIAL**。

- 环境复验：Python 3.12.10、Node 24.14.0、npm 11.9.0、FFmpeg/ffprobe 8.0.1；本机无 Blender，渲染用例为明确 mock。
- 依赖恢复：新建 `.venv` 并安装 `requirements-test.txt`；`npm ci` 安装 67 个包，与交接记录一致。
- 回归结果：`compileall` PASS、`unittest discover` **28/28** PASS、Vite build PASS、Sites worker 4/4 PASS。
- 修复真实缺陷：租约到期时间按整秒写入却与带微秒的当前时间做字符串比较，会在同一秒内被误判过期；现统一保留微秒（`lease_expiry_text()`）。
- 关闭竞争窗口：抽出 `_apply_job_update()`，新增 `update_job_with_lease()`；完成/失败的状态写入、租约释放与事件写入合并为单一 `BEGIN IMMEDIATE` 事务；`claim_job()` 同样使用写事务。
- 长任务续租：新增 `HeartbeatKeeper`，`execute_claimed_job()` 执行期间按租约 1/3 的间隔续租，避免真实长渲染被其他 worker 抢走并重复执行。
- 取消与终态：取消胜出（迟到的完成回报改判 `CANCELLED`）；终态记录不可回退；失去租约的执行器不再改写任务与产物。
- 新增 5 个 A04 回归用例，含“有心跳不被抢 / 无心跳被抢”的正负对照，以及进程被杀后由新 worker 完成的恢复链路。
- 未完成：真实 Blender/FFmpeg 长任务中断恢复、远程任务输入下载与成果回收、编码失败自动重试、SSE 推送仍未实现；云端部署与出片验收仍 BLOCKED（未登录服务器）。

### 2026-09-12 追加：本机真实 FFmpeg 图片链路验收

- 用真实 uvicorn 进程 + 真实 FFmpeg（非 mock）跑通图片链路：上传 → 模板计划 → 编辑三段分镜 → 批准 → 后台作业 → 下载产物。
- 产物：1080×1920、H.264、24fps、144 帧、6.000 秒、206,693 字节；视频 SHA-256 `b689c76aa8003e336ee4b680913f3852d0ae1fcf7a9bb14307574fe77401f4b1`，Manifest SHA-256 `eb8fdeae901cee6c557af5fcdaa742d8eeb7f94e1f08963779e46518b7e1556b`。
- 分镜语义生效：第 1/24/25/96/97/144 帧六张边界帧哈希全部不同；Manifest 记录冻结计划的 SHA-256 `3ecab00c99b19b02445355b925931450af423e9625e0cbc8e5a6aa8aec48846d`。
- 本机没有 Blender，GLB 链路本次未复验；证据与复验步骤见 [本机真实 FFmpeg 图片链路验收](reports/V1_LOCAL_FFMPEG_E2E.md)。

### 2026-09-12 追加：云端部署与双链路出片验收

- 恢复云 SSH：实例 SSH 长期超时的真实原因是云防火墙 `TCP:22` 只放行了旧设备出口 IP；放行当前出口后恢复。云端另有两个前置问题：`productdirector-v1-web` 因缺 `node_modules/vite` 崩溃重启 305 次；仓库停在 `68532ec` 且有未提交改动。
- 安全部署：先备份云端工作区补丁、未跟踪文件与 systemd 单元到 `/home/ubuntu/pd-backup-20260912-093845`，再 stash 本地改动并快进到 `8e6fe3c`。
- 依赖与构建：云端 `pip install -r apps/api/requirements-test.txt`、`apps/web` 执行 `npm ci`（67 包）与 `npm run build`，web 服务恢复。
- 鉴权落地：生成 `/etc/productdirector/v1.env`（root 0600）保存 Owner/Worker/Fernet 密钥，两个 systemd 单元加入 `EnvironmentFile` 并重启；匿名 `/api/v1/health` 返回 401，携带 Owner 令牌返回 200。
- 双链路验收：新增可复跑脚本 `scripts/v1_cloud_acceptance.py`。图片作业 `3acb27ad-…` 3.0 秒完成；GLB 作业 `afd2e8d0-…` 63.2 秒完成真实 Blender 渲染。两者均为 H.264、1080×1920、24fps、144 帧、6.000 秒，边界帧六张哈希互不相同。
- GPU 证据：渲染期间 43 次采样，峰值利用率 75%、峰值显存 1641 MiB、活跃采样 16 次。
- 未完成：真实杀进程重领、远程 Worker 输入/回收、用户真实素材与稳定性对照、前端登录页浏览器验收。证据见 [云端部署与双链路出片验收](reports/CLOUD_DEPLOY_A05_ACCEPTANCE.md)。

### 2026-09-12 收尾：V1 / V2 验收、V3 进度与 GitHub 归档

本节记录阶段判定与本次归档，详细状态与下次入口见 [下一次操作交接说明](HANDOFF_NEXT_SESSION.md)。

- 阶段判定：用户确认 **V1 ACCEPTED**、**V2 ACCEPTED**；排除项（外观/材质保真归 V3、不做多 Owner/多租户）一并获接受。确认结论记录在 [V1 验收确认单](reports/V1_READY_FOR_REVIEW.md)。
- V3 进度：**V3-01**（多通道渲染 72/72：beauty/alpha/depth/normal EXR + 产品遮罩 PNG）与 **V3-02**（通道校验 `passed: true`，遮罩与 Beauty Alpha 覆盖率逐帧一致、二值度 0.994–0.996、法线模长≈1）通过；**V3-03** FidelityPolicy 版本化合同完成；**V3-04** 产品版本审核与约束绑定完成；**V3-05** Strict 合成核心通过（72 帧、线性空间、外扩 3px、`pixel_lock_ok: true`）。V3-06…V3-10 未开工。
- V3-05 未完成项如实保留：背景仍为程序化图案（未接真实 AI 生成背景）；阴影 / 反射 / 人物遮挡尚未做成独立层。
- 测试：后端本地 **152/152**；云端同版本同样通过。云端 api / web / postgresql / comfy-h3 四个 systemd 服务均 active。
- 独立服务归档：H3 / ComfyUI 加速那一轮此前只存在于本机，现推送到私有仓库 `comfyui-h3-cloud-records` 的 `optimization/`（加速版工作流、加速节点代码、SageAttention 微基准、全过程耗时、加速前后抽帧对比、优化前工作流快照），提交 `cfdfd7f`。实测原流程 907.728 秒 → 加速 667.537 秒（约 −26.46%，单次同规格对比，非统计结论）。
- 出口边界：本次只推送源码、合同、脚本、测试与脱敏文档；用户真实素材、官方 STEP、CC0 素材、渲染产物、数据库、模型权重、SSH 私钥与云端登录信息均未入库。
- 未完成：V3-06…V3-10；V2 非阻塞项（多任务长时压测、费用换算、运行中任务协作式取消）。
