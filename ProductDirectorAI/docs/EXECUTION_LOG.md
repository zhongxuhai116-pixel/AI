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

### 2026-09-13 云端会话：主线合并 + V3-05 独立层真实闭环

本节记录在云端（117.50.44.60）继续施工的结果；证据报告见 [V3-05 独立层真实闭环证据](reports/V305_LAYERS_REAL_EVIDENCE_2026-09-13.md)。

- 基线：云端 HEAD `9f3c65c` 落后 GitHub `main` 3 个提交，且带未提交的 V3-05 独立层工作（`build_layers.py` / `h3_background.py` / `test_v305_layers.py` 与三个脚本的修改）。云端工作自测 179/179 OK。
- 合并两条开发线：云端未提交工作先提交到 `wip-v305-layers`，主线快进到 `59e99bd` 后合并，三个冲突文件（`render_product.py` / `validate_fidelity_passes.py` / `strict_composite.py`）以“主线冻结合同为基 + 云端分层语义”重写，层合同统一为：shadow=16 位灰度因子乘算、reflection=sRGB 能量加算、occlusion=RGBA 盖回 + 像素锁定豁免计数；有冻结计划时层可声明部分覆盖，无计划时提供层必须覆盖全部处理帧（fail-closed）。合并提交 `b260f1f`，全量后端 **339/339 OK**（云端 195.7s）。
- 真实闭环（云端，CC0 相机素材，540×960，72 帧）：`--passes --layers` 真实渲染（288 个五通道文件未被第二遍改写；plate_full/plate/occlusion 各 72）→ `build_layers.py` 差分（shadow 因子 min 0.0714、reflection 能量 max 2.4883、occlusion 全零注明）→ `validate_fidelity_passes.py --layers` **passed**（mask↔alpha IoU≈1、法线模长≈1、五层帧号与全片一致）→ `h3_background.py` 真实 H3 背景（SUCCEEDED，源视频 sha `fbe1cca4…`，72 帧）→ `strict_composite.py`（冻结计划 + 三层全帧必需）**passed**、`pixel_lock_ok=true`、掩码内与可信产品逐像素差 0.0；独立数值复核掩码外 composite↔H3 背景相关 0.9929、掩码内差 0.0。
- 双产品检测正例回归（真实 H3 背景）：`dual_product_check.py` 72/72 PASS 零误报。
- **真实遮挡物验证**：新增 `render_product.py --occluder`（独立遮挡物 GLB，`c8f0396`；真实运行还抓出初版把产品网格误标成遮挡物的缺陷）。0.28m 方块遮挡物真实重渲 72 帧：遮挡层覆盖率 4.17%、遮挡像素 1,555,824（342,917 在产品掩码内）；合成 `occlusion_exempted_pixels_total=348,881`、pixel_lock_ok、锁定区与可信产品平均绝对差 0.00289（≤1/255）、遮挡区与遮挡层颜色平均绝对差 0.00207；双产品检测仍 72/72 零误报。
- 真实缺陷与修复：`build_layers.py` 输出帧号从 0 起编号，被冻结帧集合合同正确拒绝（fail-closed 生效），修复为沿用输入帧号（提交 `edc2df7`）后重跑通过。
- 1080×1920 口径复验：同一 72 帧计划重渲 + 重建层 + 校验（passed）+ H3 背景（同 seed 同源视频，解码缩放——H3 原生 576×1024）+ 合成（passed / pixel_lock_ok）+ 双产品检测（72/72 零误报）。
- 主规划 V3-04 补强（QA 闭环）：修复真实缺陷——`run_qa_for_run` 在任务已处于终态（VERIFICATION_PASSED/SUCCEEDED）时调用 `update_job(status=QA_REJECTED)` 会被 `_apply_job_update` 的终态保护静默丢弃，QA 失败后任务仍显示成功；现允许唯一的终态→终态降级 QA_REJECTED（取消胜出仍优先）。新增 4 个 API 级真实 QA 故障注入测试（不 mock 引擎）：Logo 区抹除、部分帧掩码放大、掩码整体错位、产品变色——均触发对应致命失败（Logo/轮廓/核心区 MAE）、任务 QA_REJECTED、发布门拒绝；正例对照无致命失败。全量后端 **345/345 OK**（云端 228.3s）。
- 主规划 V3-06 审核界面上线（提交 `5c8eb6b`）：前端新增「保真审核」页（版本审核/质检报告/通道查看三页 + 问题帧定位 + 审批按钮注明 Manifest hash）；后端新增 `GET /qa-reports`、`GET /runs/{id}/strict-artifacts/{channel}/{frame}`（白名单帧预览）、`GET /plans/{id}/contracts`；QA 报告新增结构化 problems（check/frame/shot 定位）。真实运行修复两个缺陷：受控渲染验证小样此前**静默跳过 QA**（无背景工作流分支直接 VERIFICATION_PASSED，现落库真实 QA 报告）；QA 颜色对照**跨色彩空间误判**（AgX display PNG 直接对比 scene EXR，真实数据 432 个误报问题，现同空间对照/不可比则 NOT_VERIFIED）。**线上真实 E2E**：真实 CC0 GLB 上传 → 严格 Run → 真实 Blender 144 帧受控渲染 → QA 报告（NOT_VERIFIED、0 问题、color/edge/contour/logo/asset_hash PASS）→ 审批门 409 → 帧预览 200。全量后端 **349/349 OK**、前端 build + Sites 4/4、线上 api/web 均部署新代码。
- 主规划 V3-07 真机校准与验收组装：定位取景缺陷根因（CC0 Camera_01 归一化后仅 0.43m 高、镜头轴沿 Y，默认机位按 1.45m 产品设计 → 产品仅占画面约 5%）；用计划合同 `camera_path/position_m` 给出校准机位（**不改默认机位，V1/V2 回归不变**），CC0 掩码覆盖升至 12.5–25.1%、包围盒高 22–30%。完成**两类产品（CC0 纹理/细杆 + 官方刚性不透明）× 3 镜头 × 2 背景（H3 真实 + 程序化）**全矩阵：渲染 49–56s/72 帧、校验 passed、合成 pixel_lock、双产品 72/72 零误报；legacy 路径真实回归 14s/72 帧 RGB；存储 1.4GB/产品（EXR 为主）。产出 [V3_ACCEPTANCE.md 草稿](reports/V3_ACCEPTANCE.md)：七项未完成如实列出，V3 保持 IN_PROGRESS、待负责人人工复核。
- **主规划 V4-01 合同开工**（按项目先例：V2 曾在 V1 验收前开工，V3 仍待复核、不宣布 PASS）：交互锚点（产品本地规范坐标 + 单位法线 + 半径 + 允许动作白名单）、锚点修订不可改写、锚点集按修订冻结（幂等 + 哈希绑定）、**产品版本升级后锚点不自动沿用**（测试钉住）；人物规格（合成 Proxy / 授权素材必须带许可 + 同意记录）；四个必需动作模板（approach/press_button/single_punch_target/celebrate）；纯几何 bbox→世界坐标变换模块（不依赖 Blender，渲染侧 V4-02/03 接入）。新增 `tests/test_v4_contracts.py` 7 例，全量后端 **356/356 OK**。
- **主规划 V4-02/03 交互计划与校验引擎**：交互计划合同（版本化、绑定当前产品版本 + 冻结锚点集 + 人物 + 动作模板；未批准锚点集/动作无匹配锚点/帧越界 → 409/422；计划更新后旧交互失效 → 409）。纯数学校验引擎 `interaction_validation.py`（确定性 Proxy：可达带 0.15–1.15×身高、锚点高度按归一化最长边 1.45m 保守换算）：接触距离 ≤ min(2cm, 锚点半径)、穿透 ≤1cm 且 ≤2 帧、接触时序偏差 ≤2 帧、锚点动作白名单与人物能力；数据版预演返回事件时间线与逐帧代理手部位置。新增 `tests/test_v4_interactions.py` 9 例；全量后端 **365/365 OK**；线上 api 已部署并冒烟（锚点→锚点集→人物→交互→校验 passed→预演 60 帧时间线）。
- **主规划 V4-02/03 渲染侧闭环**：`blender/scripts/render_interaction_previz.py` 复用 render_product 构建确定性人体 Proxy，双通道输出（产品+Proxy 预演帧、仅 Proxy 透明底）+ 真实几何接触 QA（表面间隙 ≤min(2cm,半径)、包围盒体积穿透 ≤1cm 且 ≤2 帧、实际接触帧偏差 ≤2 帧）+ 逐帧轨迹 JSON。云端真实验证：**正例** CC0 相机顶盖 press_button（偏差 1 帧、间隙 0.1mm、穿透 4mm，passed）；**负例** 官方产品顶部锚点 + 矮人物（手真实穿入产品 8.7cm，exit 1 拦截）。渲染中修复两个真实缺陷：模块路径（parents[2]）、Blender 5 写静帧不带帧号（逐帧显式 filepath）。可达带修正 0.5→0.15×身高（弯腰可及低位锚点）。后端 **369/369 OK**，证据见 [V4-02/03 Proxy 预演报告](reports/V402_V403_PROXY_PREVIZ_2026-09-13.md)。
- **主规划 V4-04/05**：人物 `asset_refs` 校验存在与 Owner 归属（404/403）；`GET /characters/{id}/capability` 如实报告（Proxy 预演 AVAILABLE、生成路线 NOT_CONFIGURED、授权素材逐引用绑定状态、usable_for_final_person_layer 仅授权素材+记录齐备为 true）。**真实人物遮挡合成**：Proxy 帧作为 occlusion 层（交互窗口 10–60 必需）合成 72 帧——passed / pixel_lock / 豁免 4,554,420 像素 / 锁定区差 0.0009 / 遮挡区差 0.0004 / 双产品 72/72 零误报。后端 **370/370 OK**，证据见 [V4-04/05 报告](reports/V404_V405_PERSON_LAYER_2026-09-13.md)。
- **主规划 V4-06 人物与互动界面**：新增 `GET /api/v1/plans`（计划列表）；前端「人物互动」页（计划与锚点/人物/新建交互计划/交互计划校验与预演时间线柱状图）；线上已部署，真实 HTTP 冒烟走通页面全部调用链（plans→contracts→anchors→anchor-sets→characters→interactions→validate passed→previz 60 帧），web 服务含新页面；前端 build + Sites 4/4。后端 **371/371 OK**。浏览器人工走查仍待补（本环境无浏览器）。
- **主规划 V4-07 前置（击打几何 + V4_ACCEPTANCE 草稿）**：击打（single_punch_target）真实几何验证——官方产品 120 帧计划，接触帧 80/实际 79、间隙 0、穿透 0，API 引擎奇偶一致（偏差 0 帧）；过程抓到一个真实合同缺口（CLI 交互 JSON 缺 allowed_actions 被引擎如实拦截，修正后通过）。产出 [V4_ACCEPTANCE.md 草稿](reports/V4_ACCEPTANCE.md)：V4-01…06 证据表 + 三项真实几何用例（按按钮正/负例、击打正例）+ 五项如实未完成（真人感人物层、双人动作、两轮人工审核、锚点拖拽 UI/IK/修订流程、浏览器走查）。V4 保持 IN_PROGRESS，不宣布 PASS。
- **主规划 V5-01 参考视频摄取**（V5 开工，按项目先例不宣布 V4 PASS）：`POST /references`（uploaded_asset_id 或 source_url）+ `POST /references/upload`（本地文件入口，V1 素材库只收图片/GLB 故参考视频走独立入口）；ffprobe 检测 + ffmpeg 代理生成 + **source_to_proxy_map 时间戳映射**（源/代理时长与缩放、fps 比率）；**URL 获取约束**：仅 http(s) 公网主机（防 SSRF，内网/本机 BLOCKED）、Content-Type 必须 video/*、500MB 上限、失败提示改用本地上传；损坏媒体 FAILED。测试 4 例（真实 ffmpeg 夹具：摄取/损坏/非视频/URL 约束），全量后端 **375/375 OK**；线上冒烟：真实视频上传 → READY（24fps/3s/72 帧 + 时间戳映射）→ 代理可取回。
- **主规划 V5-02 本地切镜与编辑**：`reference_analysis.py`——ffmpeg scene SAD 硬切检测（阈值 0.3）+ 渐变转场聚簇（[0.15,0.3) 单独评分、不与硬切混淆）+ `evaluate_cuts` 人工标注对照（±0.2s 一对一最近匹配，precision/recall/F1）。API：`POST /references/{id}/analyze`（本地同步分析）、`PATCH /reference-analyses/{id}`（仅草稿、新修订 + edited_from）、`POST …/approve`（冻结幂等）、列表/详情。**cut time 指标报告：10 条合成已知切点参考片（2–4 段随机时长），±0.2s 容差 F1 全部 1.0，10/10 通过 0.90 门**（报告 `/home/ubuntu/pd-v307-calib-20260913/v502_cut_time_report.json`，如实注明合成夹具、真实素材人工标注夹具待补）。测试 5 例 + 线上冒烟（上传→分析 cuts=[1.0]→修订→批准）全通；后端 **380/380 OK**。
- **主规划 V5-03/04 观察模型与计划映射**：逐段**确定性观察**（亮度均值/标准差/运动能量为测量事实；运动类型 static/moving 为启发式推断带置信度与 evidence 帧；景别/焦距/相机路径/主体位置如实 null + method 说明——观察与推断分离）；`POST /projects/{id}/plans/from-reference`（仅已批准分析版本）把源分段映射为本产品可执行 DirectorPlan：时长按 24fps 量化为整帧（每镜头误差 ≤1 帧、总误差 ≤2 帧，主规划门）、运动类型启发式映射机位（static→static/moving→side_track）、复用维度 kept/changed/unsupported 逐条记录、品牌/人物/音乐/文案默认不复用；`GET /plans/{id}/reference-mapping` 冻结引用取回。修复一个真实缺陷：观察窗口多包下一段首帧导致跨切点颜色跳变误判为运动（窗口上界修正）。测试 3 例 + 线上冒烟（分析→批准→映射 24/29/24 帧、误差 0.00/0.01/0.00）全通；后端 **383/383 OK**。
- **主规划 V5-05 参考重演双栏工作台**：前端新增「参考重演」页（VideoCamera 导航项 + `ReferencePage`）——左栏参考视频（下拉 + 内嵌代理播放器 + 修订列表 + 逐段切镜观察卡，观察/推断与 null 如实呈现），右栏目标计划（计划下拉、六个复用维度勾选、产品版本选择、从参考生成目标计划、目标镜头表），映射差异检查器（源秒区间→目标帧区间 + 保留/修改/不支持维度 + capability_notes），上下双轴对齐时间线（源秒↔目标帧比例条）；后端补 `GET /plans/{id}`（详情回读）。**线上真实冒烟 10/10**：登录、references(3)、product-versions(64)、plans(43)、analyses、proxy 流、计划详情、reference-mapping（3 镜头）、**from-reference 完整生成链**（新计划 3 镜头、总时长误差 0.01 帧 ≤2 帧门）、新计划详情回读；前端 build + Sites 4/4；后端 **383/383 OK**。证据见 [V5-05 报告](reports/V505_REENACTMENT_UI_2026-09-13.md)。浏览器人工走查仍待用户复核（本环境无浏览器，如实记录）。
- **主规划 V5-06 重演生成与引用追踪（3 条真实成片 + V4 互动镜头）**：先修 6 个真实工程缺口——①重演计划此前**无法启动渲染**（`PlanUpdate` 强制恰好 3 镜头 + 5–8 秒，参考映射的 1–4 镜头/分数时长计划被 409 拒绝）→ 按 `from_reference` 分支逐镜头核对冻结映射；②新增 `ReenactmentOutputSpec`（显式整帧帧数 + 分数时长）与 `output_spec_for_plan` 分派，`OutputSpec` 与 V1 Schema 保持严格同步；③渲染器从「恰好 3 镜头、单镜头 ≥24 帧」放宽到 1–8 镜头、≥1 帧（V1 约束仍在 API 层）；④末尾开放分段（`end_s=null`）此前静默按 1 秒 → 改用参考片实际时长（`30126cfe` 由 24 帧纠正为真实 72 帧）；⑤子秒分段被 24 帧下限抬升产生 2 帧/镜头误差（超 ≤1 帧门）→ 量化整帧（RE1 第三镜头 22 帧、误差 −0.01 帧）；⑥Strict QA 相邻帧轮廓/尺寸检查不识别镜头边界、硬切处误报伪影 → 按计划声明切镜豁免跨边界帧对（镜内检查保持）。**真实重演（云端完整链路：Blender 五通道 → 独立层差分 → 五通道校验 → 真实 H3 背景 → Strict 合成 → 双产品检测 → FFmpeg → Strict QA）**：RE1（参考 `d70659be`，75 帧 24/29/22，误差 0.00/0.01/−0.01、总 0.00，**含 V4 `press_button` 互动镜头**：申报/实际接触帧 46/46、保持窗间隙 0.1mm、最大穿透 4mm、穿透帧 0；人物遮挡层豁免 3,793,344 像素）；RE2（`9235db86`，48 帧，误差 0.00/0.00）；RE3（`30126cfe`，72 帧单镜头，误差 0.00）。三条均：五通道 passed、合成 passed + `pixel_lock_ok`、双产品 0 误报、QA passed、真实 H3 背景 SUCCEEDED（75/48/72 帧，记录源视频 sha256）。Run manifest 新增 `reference_recreation` 引用溯源（参考片/代理 sha256、分析修订、映射 hash + 完整映射快照）。新增 `scripts/v5_recreate.py` 驱动器与 5 例测试，后端 **387/387 OK**；产出 [V5_ACCEPTANCE.md 草稿](reports/V5_ACCEPTANCE.md)（11.9 逐条核对，剩 10 条许可素材人工标注与浏览器走查待负责人），证据见 [V5-06 报告](reports/V506_REENACTMENT_EVIDENCE_2026-09-13.md)。V5 保持 IN_PROGRESS，不宣布 PASS。
- 推送：`b260f1f`、`edc2df7`、`c8f0396` 已推送到 GitHub `main`；云端 `wip-v305-layers` 分支保留本地。
- 未完成（如实记录）：V3/V4 负责人人工复核（两份 ACCEPTANCE 草稿）；V4 尾巴（真人感人物层、双人动作、修订流程、浏览器走查）；V5-05/V5-06 的浏览器人工走查与 10 条许可参考素材的人工标注夹具（[V5_ACCEPTANCE 草稿](reports/V5_ACCEPTANCE.md) 第 4 节）；V6。
