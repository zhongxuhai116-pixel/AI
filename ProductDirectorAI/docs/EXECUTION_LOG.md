# V1 执行记录

## 2026-09-11

- 新优云智算节点公网 SSH 已通过专用 ED25519 密钥连接；首次连接前从网页终端核对服务器 ED25519 指纹。仓库和文档不保存地址、实例 ID、密码或私钥。
- CLOUD-01 PASS：Ubuntu 22.04.4、RTX 4090 24564 MiB、驱动 570.153.02、16 核/94GB；新节点根分区约 97GB，不能沿用旧节点“291GB 已扩容”的结论。
- CLOUD-02 PASS：安装官方 Blender 5.2.1 LTS、官方 Node.js 22.23.2、Ubuntu FFmpeg/ffprobe 4.4.2；Blender 与 Node 下载均完成官方 SHA-256 校验。
- CLOUD-03/04 PASS：Blender 5.2.1 headless 生成 12 帧，独立 nvidia-smi 采样记录 GPU 峰值 61%、显存 1114 MiB；不再仅凭“能看到 4090”宣称 GPU 生效。
- CLOUD-05 PASS：通过真实 API 上传通用 GLB、创建/批准计划、启动任务并完成 Blender + FFmpeg 全链路。产物为 H.264、540×960、24fps、144 帧、6.000 秒；视频 SHA-256 为 `fadc6f3b41579289c1a81089554bd47df6d7b13d12a3d76fbf4ac026515baef7`。
- API 与 Vite Preview 通过 systemd 启用，仅监听 `localhost` 的 8000/4173 端口；未新增公网端口，也没有把无鉴权服务直接暴露到互联网。
- 云端复验：Python compileall、Vite build、Sites worker 测试 4/4 通过。`npm ci` 因仓库 lockfile 与 package.json 不同步失败，临时使用 `npm install --no-package-lock --no-save` 完成验收；锁文件差距仍须后续修复。
- 阶段判定：V1 云基础设施与通用 GLB 端到端门 PASS；V1 产品总状态仍为 PARTIAL，剩余差距见 `V1_IMPLEMENTATION_GAPS.md`。
- CLOUD-05b PASS：DirectorPlan 编辑现在影响真实 GLB 成片。云端作业以 85mm 定格 24 帧、24mm 侧移 72 帧、55mm 环绕 48 帧完成 6 秒视频；独立打开 scene.blend 验证相机关键帧，Manifest 记录冻结计划 SHA-256。Python 合同测试 3/3、前端 build 与 Sites worker 测试 4/4 通过。
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
