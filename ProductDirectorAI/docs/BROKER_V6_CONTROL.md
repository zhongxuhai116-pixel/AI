# DeepSeek 开发与独立验收控制记录

2026-09-12。用户持续目标：使用 MixAgents Broker 的 `deepseek-pro` 编写 ProductDirectorAI，直到完整 V6。控制端负责规划、验收、集成；开发端负责有界实现，逐批交付后复验。本文不取代主规划，也不把局部修复当完整 V6。

## 恢复入口

- 当前主线仍在 V3。V1/V2 已由用户 ACCEPTED；以最新确认和 ADR-002 为准，不回到旧文档的 V1 PARTIAL。
- 首个开发任务：修复 Q01–Q06 及已约定的共同缺帧、可信 Alpha、预览状态、正式测试和依赖问题。
- Broker 路由：`deepseek-pro`，provider `deepseek`，model `deepseek-v4-pro`，backend `app_server`，workspace-write。
- Managed agent：`broker:deepseek-pro-rw-69e237024bd022f5:01a0967b-2ae2-7d53-9fad-8c3201062b31`。
- 最后已通过 Broker wait 确认 running。此句只作历史记录：恢复时必须先用相同 handle 调用 wait，不能凭本文断定仍运行，也不能因观察超时新建替代任务。
- `list_agents` 本轮因另一个 read-only runtime 的 active writer 恢复冲突报错。已定位为既有只读任务；不解除其锁、不停止它、不修改 Broker 配置。当前开发任务已成功创建并能独立 wait。
- 开发端禁止修改控制端独立验收报告；控制端不与其同时修改业务文件。后续派工复用同一 agent，通过 Broker send 提交下一有界任务。

## 全范围阶段门

以下映射**主规划原编号**；`V3_TASK_BRIEF.md` 有另一套历史工作包编号，必须按名称和本表映射，不能串号宣称完成。

| 主规划任务 | 必需交付/证据 | 当前控制判定 |
| --- | --- | --- |
| V3-01 | 不可变产品版本、策略、审核；版本变化撤销旧审批 | 合同已有；版本失效联动已实现并被测试覆盖（invalidated version blocks strict run） |
| V3-02 | Blender 五通道、稳定产品身份、单位/空间/颜色合同；本地/云一致性 | 五通道真实产出 + 校验修复已完成；稳定身份以产品专用遮罩渲染代替 Object Index；Depth/Normal 语义仍 NOT_VERIFIED |
| V3-03 | 保护区、实际背景工作流映射、Strict 合成及阴影/反射/遮挡独立层 | 已闭环（2026-09-13）：真实 H3 背景 + shadow/reflection/occlusion 独立层 + 真实遮挡物，pixel_lock_ok，证据见 V305 报告；scene-linear 全链路未闭环 |
| V3-04 | QA 阈值集、问题帧、Logo/颜色/轮廓/尺寸/Mask/ID/缺帧故障阻断、审批绑定 hash | 主体已实现：版本化阈值集、真实 QA 引擎（核心区/边缘/轮廓/Logo/资产 hash/编码媒体）、审批 hash 绑定与失效、QA→Run 状态联动（含终态→QA_REJECTED 降级修复）；API 级故障注入（Logo 抹除/尺寸放大/Mask 错位/产品变色→QA_REJECTED+发布拒绝）已测试。缺帧/轮廓/Logo 等也有独立 CLI 负例。仍缺：阈值集真实数据集校准冻结、ID 通道语义（无 Object Index，以遮罩渲染代替并如实记录） |
| V3-05 | 单图/多视图候选真实审核、未核实面与相机限制、尺寸证据 | 审核记录已实现；真实单图/多视图候选审核样例与运行时相机限制待验 |
| V3-06 | 版本审核、参考/通道/Master/成片对照、问题帧跳转及批准 E2E | 主体已上线（2026-09-13）：保真审核页（版本审核/质检报告/通道查看）、结构化问题帧定位、审批按钮注明 Manifest hash、线上真实 E2E（真实渲染 → QA 报告 → 审批门 409 → 帧预览 200），证据见 V306 报告；剩浏览器人工走查与运动镜头 Logo 位姿映射 |
| V3-07 | 两类必需产品各至少3镜头/2背景、透明反射明确边界；真机校准、旧版回归、性能/存储 | **V3_ACCEPTANCE.md 草稿已产出（2026-09-13）**：真机校准（取景根因 + 计划合同校准机位）、两类产品（CC0 纹理/细杆 + 官方刚性）×3 镜头×2 背景全矩阵（渲染/校验/合成/双产品全过）、legacy 回归、性能/存储实测、六类故障注入汇总；剩余负责人人工复核 + 草稿第 6 节七项（阈值集校准冻结、真实候选审核样例、透明材质显式不支持声明等） |
| V4-01…03 | 锚点/人物/动作版本；Proxy、IK/时序/碰撞；接触距离≤2cm或更严格锚点半径、偏差≤2帧、穿透≤1cm且不持续超过2帧 | V4-01 合同、V4-02/03 合同+校验引擎与**渲染侧 Proxy 预演已闭环**（2026-09-13）：确定性人体 Proxy 双通道输出 + 真实几何接触 QA（正例：偏差 1 帧/间隙 0.1mm/穿透 4mm passed；负例：不可达锚点手穿入产品 8.7cm 被拦截），证据见 V402 报告。**未做**：真人感人物层（V4-04）、击打（single_punch_target）真实几何与 IK、预演帧人工目检。V3 阶段门仍待负责人人工复核，不宣布 V3 PASS |
| V4-04…07 | 真人感人物层及来源、遮挡与可见产品保真、修订UI；走近/按按钮/击打/收回/庆祝/双人轮流，真实成片两轮人工审核 | V4-04 能力校验、V4-05 真实人物遮挡合成、V4-06 人物与互动界面已闭环/上线；**按按钮与击打（single_punch_target）真实几何正/负例已验证**；[V4_ACCEPTANCE 草稿](../docs/reports/V4_ACCEPTANCE.md) 已产出（2026-09-13）。**未做**：真人感人物层（生成工作流未配置/授权素材真实素材）、双人非同时动作（按主规划单人验收后扩展）、问题帧修订流程 E2E、浏览器人工走查、真实成片两轮人工审核。Proxy 通过不等同真人成片通过 |
| V5-01…03 | 授权参考上传/代理时间戳、切镜与人工修改、多模态证据及置信度 | V5-01 摄取、V5-02 切镜（10/10 合成夹具 F1=1.0）、V5-03 观察/推断分离（测量事实/启发式推断带置信度与 evidence，景别/焦距/相机路径如实 null）已实现（2026-09-13）；真实素材人工标注夹具与多模态模型（未配置）待补 |
| V5-04…06 | 计划映射/双栏时间线、真实重演；保留时长单镜头误差≤1帧、总长≤2帧 | V5-04 已实现（2026-09-13）：from-reference 适配规划（时长 24fps 整帧量化、运动类型→机位启发式、复用维度 kept/changed/unsupported、品牌/人物/音乐/文案不复用）、ReferenceMapping 冻结引用；线上冒烟误差 0.00/0.01/0.00 帧。**V5-05 已上线（2026-09-13）**：参考重演双栏工作台（播放器/修订与切镜观察/维度勾选/目标镜头表/差异检查器/双轴对齐时间线），后端补 `GET /plans/{id}`；线上冒烟 10/10，前端 build + Sites 4/4，证据见 [V505 报告](reports/V505_REENACTMENT_UI_2026-09-13.md)。**V5-06 已完成（2026-09-13）**：修 6 个真实缺口（重演计划无法启动渲染、输出合同无显式帧数、渲染器 3 镜头/24 帧写死、末尾开放分段按 1 秒、子秒段 2 帧误差超门、QA 不识别切镜边界）后，产出 3 条真实重演成片——RE1（75 帧 24/29/22，误差 0.00/0.01/−0.01，**含 V4 `press_button` 互动镜头**：接触帧 46/46、间隙 0.1mm、穿透 4mm、穿透帧 0）、RE2（48 帧）、RE3（72 帧），全部五通道/合成/双产品/QA 通过 + 真实 H3 背景 SUCCEEDED；Run manifest 新增 `reference_recreation` 引用溯源；后端 387/387，见 [V5-06 报告](reports/V506_REENACTMENT_EVIDENCE_2026-09-13.md) 与 [V5_ACCEPTANCE 草稿](reports/V5_ACCEPTANCE.md)。**剩**：10 条有权使用的 10–30 秒参考片 + 人工标注夹具（负责人）、浏览器人工走查；V5 保持 IN_PROGRESS |
| V6-01…05（A） | 六个Profile、es-MX文案/TTS/字幕、BGM/ducking/响度、Batch、封面与发布包 | **V6-01 已完成（2026-09-13）**：Platform Profile 与后期模板合同、六个种子 Profile、版本化 API、成片兼容性校验、账号能力如实 NOT_CONFIGURED；线上冒烟 16/16，见 [V6-01 报告](reports/V601_PLATFORM_PROFILE_2026-09-13.md)。**V6-02 已完成（2026-09-13）**：本地化文案（事实与生成分离、违规宣传扫描、标签校验）、字幕（SRT/VTT、时间轴校验、短句补齐）、**真实语音对齐**（ffmpeg silencedetect，3 段真实西语 → 3 条字幕 ALIGNED；语音段不足时回退并说明）、配音过长显式报告三种适配策略（不截断）、**真实 es-MX 离线配音**（espeak-ng `es-419`，标注 offline_preview）、授权配音上传（记录许可）、人工修订新 revision + 下游失效、libass/软字幕在真实成片上验证；线上 21/21，后端 430/430，见 [V6-02 报告](reports/V602_LOCALIZATION_TTS_2026-09-13.md)。**V6-03 已完成（2026-09-13）**：BGM 素材库（**必须带许可引用**，缺则 422）、真实混音（旁白优先 ducking + 响度归一 + 真峰值限制）、**响度实测与判定**（线上 −15.5 LUFS / −1.0 dBTP，最终 MP4 复测 −15.3 LUFS）、ducking **分支级验收**（语音段压低 8.6 dB vs 关闭时 0.9 dB，固定阈值不生效已改为按实测旁白电平自动推导）、音轨开关语义（`None`=自动 / `""`=显式关闭，四种组合产物不同）、商业 Profile 拒绝非商用 BGM（422 `MUSIC_NOT_COMMERCIAL`）、**时长适配三种策略**（延长非接触镜头 +13 帧 EXTENDED；调速超范围 REJECTED；改文案不改视频）、编码输出（成片 + 无字幕 Master + 封面 + 完整 lineage；9:16→1:1 用 letterbox 不拉伸，`reframe_3d` 拒绝）；线上 21/21、后端 452/452，见 [V6-03 报告](reports/V603_AUDIO_POST_2026-09-13.md)。**V6-04…05 未开始**：Batch 矩阵与失败恢复、发布包（Manifest/字幕侧车/文案/封面/ZIP） |**V6-04 已完成（2026-09-13）**：`batch.py` 矩阵展开（产品版本 × 计划版本 × Profile 版本 × 变体；**总数服务端给出**、上限 100）、矩阵与 `items[]` **互斥**、相容性冲突整批 422 并逐条列出、去重提示（不自动合并）、缓存键、状态聚合、只重试失败项；API `preview`（无副作用）/`create`（幂等 + 独立 Run）/`items`/`pause`（只停新调度）`/resume`/`cancel`（范围可选、重算聚合）/`retry-failed`；修 5 个真实缺陷（**批次项缺 `run_jobs` 队列行导致永不执行**、runs/jobs 外键顺序、**`job_events` 并发序号撞唯一键（真实致 15s 广告渲染 75% 失败）**、取消不重算状态、调度失败不可见）；测试 19+2 例，线上真实批次出片，见 [V6-04 报告](reports/V604_BATCH_2026-09-13.md)。**V6-05 已完成（2026-09-13）**：发布包结构/Manifest（凭证与临时链接检查）/许可清单（**音乐原文件默认不入包**）/无凭证发布模板/ZIP 防穿越与哈希复核/审批绑定内容哈希（改动即失效）/批次归档只收已审批包；API 7 个；测试 15 例；**真实闭环**：宇航员灯 15 秒西语广告（5 场景 360 帧真实渲染 + 5 段真实 H3 背景 + 逐场景合成 + es-MX 配音与混音 −16.2 LUFS/−1.0 dBTP + 精确场景字幕烧录）→ build/verify/approve → **ZIP 27MB 下载**；见 [V6-05 报告](reports/V605_PACKAGE_2026-09-13.md)。**如实缺口**：该片产品保真未达 brief（单视图重建无贴图、产品悬空、预览音色、无 BGM），需官方带贴图模型 / 多视图重建 / H3 产品替换参考视频 / 商业配音与授权 BGM 之一。**V6-06 已完成（2026-09-13）**：资源与成本账本（`ledger.py`）：用量事件按 (provider, operation_id, billing_item, event_id) 去重、四类金额（estimate/reserved/accrued/settled）且 **reserved 不与 settled 相加**、`available = limit − settled − reserved − accrued`、无已知报价**不按 0**（`amount=null` + 原因 + 未定价事件单列）、自管资源标注 `internal_estimate`、预算上限 revision 乐观锁、提交前预留与超支对账（`overrun`/`additional_accrual`/`release`）、**预留行 `settled_at` 防重复结算双计**；真实生产动作自动记账（Blender GPU 秒含失败任务、H3 生成请求、ffmpeg 混音 CPU 秒、TTS 字符数）；控制台新增「成本与用量」页；测试 23 例、后端 **516/516**、真机 PostgreSQL 冒烟 **18/18**、前端 build + sites 4/4，见 [V6-06 报告](reports/V606_COST_LEDGER_2026-09-13.md)。**V6-07 Automation API 已完成（2026-09-13）**：`automation.py`（Key `pda_<key_id>_<secret>` 只存哈希、7 个 scope 路由表且未登记路由不放行、Key 管理只允许 Owner 会话、IP 允许列表、每 Key/每工作区双层限流 60/分钟（标注本产品设置）、`Idempotency-Key` 按 (owner,调用方,接口,键) 隔离绑定请求哈希、Key 周期预算按账本 `actor_key_id` 汇总）；API：`POST/GET/DELETE /automation-keys`、`GET /automation/openapi`、`POST /automation/generate`（复用批次创建）、`GET /automation/batches/{id}`、`GET /automation/jobs/{id}`（生产/发布状态分栏）；控制台新增「自动化接入」页；**真机冒烟 21/21**（真 Key、真越权 403、真 428/409/429、撤销后 401，两次自动化批次各产出真实渲染项 SUCCEEDED）；顺手修产线发现的**批次项队列行缺失永久卡住**缺陷（对账补建 run_jobs + job_attempts，含回归测试）；测试 30+1 例、后端 **547/547**、前端 build + sites 4/4，见 [V6-07 报告](reports/V607_AUTOMATION_API_2026-09-13.md)。**V6-08 Outbox/签名 Webhook 已完成（2026-09-13）**：`webhooks.py`（事件目录、信封带 `aggregate.revision` 与 `at_least_once` 语义、载荷含凭证/媒体即拒绝、`HMAC-SHA256(secret, timestamp + "." + raw_body)`、300 秒容差、轮换期双密钥、1m/5m/15m/1h/6h/24h 共 7 次尝试后进死信、非 408/429 的 4xx 暂停目标、投递记录脱敏）；**事务性 Outbox**：业务状态与事件同事务写入（含回滚测试），已接入 `batch.started/completed`、`item.completed/failed`、`review.required`、`package.ready`、`budget.blocked`（`publish.*` 属 V6-10+，未发射不伪造）；API：`/webhooks` CRUD + `catalog`/`test`/`deliveries`/`rotate-secret`/`pause`/`resume`/死信 `replay` + 内部投递 Worker；控制台新增「事件与 Webhook」页；**真机冒烟 19/19**（真实接收端进程，含真 500 重试→死信→重放、签名独立复算、轮换宽限、暂停/恢复、脱敏）；测试 25 例、后端 **572/572**、前端 build + sites 4/4，见 [V6-08 报告](reports/V608_WEBHOOK_OUTBOX_2026-09-13.md)。**V6-09 总控台与负载/故障恢复已完成（2026-09-13）**：`GET /console/overview`（作业/队列/批次/账本/事件/投递全部按数据库实时 SQL 聚合，含 `jobs_without_queue_row` 与投递延迟 p50/p95）+ 控制台「总控台」页（6 指标卡、5 秒自动刷新）；**实测 100 项 / 2 个真实 Worker / 172.5 s 完成 / 34.78 项每分钟 / 100 成功 0 失败**，批次创建 84 ms、preview p95 48.65 ms（门：提交 p95 < 2 s），**注入 10 项队列行故障 → 10/10 恢复**，干净端点事件投递 **20/20、p50 982.8 ms / p95 1815.1 ms**（门：通常 3 s 内）；实测中发现并修复 **4 个真实缺陷**：① 项完成后无人推进批次（98/100 永久 PENDING）→ Worker 完成即推进 + `POST /internal/v1/batches/advance` 调度入口；② 并发调度撞 `runs` 唯一索引且事务中止后继续写导致 500 → 原子认领 + 每项独立事务 + 复用同键 Run + 认领超时回收；③ 新端点回填全部历史事件造成投递洪水 → `backfill_history` 默认关闭；④ 投递按全局时间取前 N 条导致新端点饥饿 → 按端点轮询 + 每端点 tick 预算 + 不可达端点冷却；新增回归测试 10 例、后端 **582/582**、前端 build + sites 4/4，见 [V6-09 报告](reports/V609_CONSOLE_LOAD_2026-09-13.md)。**V6-10…15 发布框架与四个平台连接器已完成（2026-09-13）**：`publishing.py`（发布状态机显式迁移表（**拒绝 DRAFT→PUBLISHED 这类跳跃**：提交成功 ≠ 已发布）、预检（包审批/哈希/QA/可见性/**不跨平台照搬隐私取值**/三项声明/文案/许可/时长时区）、审批绑定快照哈希与显式确认、绑定字段变化即失效、防重键 `package_version+account+publish_intent_id`、查询结果解释（未知→RECONCILING，**无链接不捏造 URL**）、取消支持程度与重试安全判定）+ `publishing_connectors.py`（TikTok/YouTube/Instagram/Facebook Page 四连接器：官方端点与 scope、未确认限制显式列出、**未配置凭据不生成假授权链接**、state+PKCE 真实 HTTP 流程（本机 stub provider 验证换 token 且令牌明文不落库不回传）、刷新失败→AUTH_REQUIRED 不盲目重试、TikTok creator_info 前置、**本部署不发起真实上传**、Instagram/TikTok 取消显式 unsupported、禁止浏览器自动化绕过平台交互）+ API 11 个（connectors/accounts/connect/callback/disconnect/preflight/approvals/jobs CRUD/reconcile/retry/cancel）+ 五张新表 + 控制台「发布与审批」页；测试 30 例、真机冒烟 **16/16**（连接器全 BLOCKED、无假链接、state 一次性、无账号预检阻断、scope 越权 403）、后端 **612/612**、前端 build + sites 4/4；官方资料调研入库 `docs/integrations/*.md`，见 [V6-10…15 报告](reports/V610_PUBLISHING_CONNECTORS_2026-09-13.md)。**如实缺口**：无真实授权 → 没有任何真实平台发布，真实上传/平台处理/公开链接未验证；`submit_publish` 有意不启用真实调用；OAuth 仅在 stub 上验证。**V6-16 未开始**：权限、全量回归、运行/部署/备份手册、OpenAPI、示例包、V6_ACCEPTANCE |
| V6-06…09（B） | 预算预留对账、Automation scope/幂等/限流、签名Webhook/Outbox/死信、真实总控台 | **V6-06 已完成（2026-09-13）**：预算/用量/账本三表 + 9 个接口 + 控制台页，真机冒烟 18/18（含超支、重复结算拦截、未定价不按 0）。**V6-07 已完成（2026-09-13）**：Automation Key/scope/幂等/双层限流/调用面 + 控制台页，真机冒烟 21/21（真越权 403、缺键 428、同键不同体 409、限流 429、撤销后 401，自动化批次真实出片）。**V6-08 已完成（2026-09-13）**：事务性 Outbox + 签名 Webhook（同事务写入含回滚测试、7 次尝试后死信、4xx 暂停目标、双密钥轮换、脱敏记录、内部投递 Worker），真机冒烟 19/19。**V6-09 已完成（2026-09-13）**：真实总控台 + 负载实测（100 项 / 2 Worker / 172.5 s / 34.78 项每分钟 / 10 项故障 10/10 恢复 / 事件 p95 1.82 s），并修 4 个实测缺陷。**V6-10…16 未开始**；100项/2Worker/10项故障恢复已实测 |
| V6-10…15（C） | OAuth/审批/发布框架、TikTok/YouTube/Instagram/Facebook Page 四个官方连接器、提交未知对账和多平台部分失败 | **V6-10…15 已完成（2026-09-13）**：发布框架 + 四个官方连接器（无授权一律 BLOCKED/NOT_CONFIGURED 且不生成假链接），测试 30 例、真机冒烟 16/16、后端 612/612。**如实缺口**：没有真实授权 → 真实发布未验证（私密成功也不能宣称公开权限已验）。**V6-16 未开始**：权限、回归、手册、OpenAPI、示例包与验收 |
| V6-16 | Editor/Reviewer/Publisher/Owner权限、全版本回归、运行/部署/备份恢复手册、OpenAPI、示例包、运营与验收证据 | 未开始；V6-A/B/C全部有证据且无P0/P1才可完整验收 |

详细字段、接口、负例、许可、范围和退出条款见 [唯一主规划](../ProductDirectorAI_V1-V6_Codex_Development_Plan.md) 第9–12、14章。V1的单Owner排除项不能自动豁免V6明确要求的角色权限。

## 本阶段下一批建议（Q01–Q06 独立复验后派工）

1. ~~固定 Strict 运行快照：产品版本、审核版本、策略版本、允许视角、各输入层引用/哈希及输出规格。~~ 已实现（`load_strict_run_snapshot` + 冻结复核，2026-09-13 验证）。
2. ~~补真实分层合成：背景替换、阴影、反射、人物遮挡的输入合同与支持边界需分开记录。~~ 已闭环（真实 H3 背景 + 三层独立层 + 真实遮挡物，见 V305 报告）；剩余遮挡物自身投影人工比对、真实人物遮挡素材、scene-linear 全链路。
3. ~~QA结果与Run状态联动、完整人工审核及批准hash绑定、修改失效。~~ 已实现并补 API 级故障注入（2026-09-13，含终态→QA_REJECTED 降级修复）；剩余阈值集真实数据集校准冻结与 QA 问题帧 UI。
4. 真实素材、故障注入、UI、旧版回归及性能验收；不足项继续本阶段，不越级开后续业务。→ 下一批：**V3-06 审核界面（版本审核页/通道查看器/质检页 + 问题帧跳转 + 批准 E2E）**，然后 V3-07 真机校准与 V3_ACCEPTANCE.md。

## 现场只读复核

2026-09-12 本轮：云端 api/web/postgresql/comfy-h3 均 active；五通道历史目录仍在，带材质 CC0 Camera_01 GLB 存在；根盘约879G可用。背景历史帧从0编号，Blender从1编号，因此显式帧号映射是实际兼容要求。

GPU 当次采样100%、显存22658MiB，存在占用；未重启、打断或启动新GPU任务。后续真实复验优先对已有文件做CPU校验，生成前重新查询现场队列。上述值不是持续状态。

## 授权与交付

### 首轮开发中的独立检查

- 原 managed agent 持续由 wait 验证 running，没有重建替代任务。期间出现同一路径Delete+Add补丁失败及host interaction；通过原Broker wait/send继续，并要求保留用户改动、使用Update而非先删源码。
- 新版合成器已写入可信帧数/起始帧号、背景offset、预检暂存和Alpha逻辑，尚在开发测试中，不能判定通过。
- 控制端真实EXR探针：构造4×4 `beauty.R/G/B=0.2`、`beauty.A=1`，新版 `read_product_alpha()` 返回0.2（应为1）。根因是单字母`A`子串匹配命中分组名`beauty`，取到R。已送回同一开发端，要求精确标量通道匹配或读取RGBA第4分量并加入真实OpenEXR回归。
- 该探针与现场兼容性直接相关：云端72帧beauty的实际通道键就是`beauty`、形状960×540×4；Alpha、Depth为标量，Normal为X/Y/Z独立分量。未启动GPU。
- 接手先用上方managed agent handle等待交付，再按最新CLI明确传入帧数与起始帧号、背景offset，复跑独立正负例；旧探针默认无计划参数需在新的复验副本中适配，禁止覆盖历史证据。

用户已指定DeepSeek编码，后续同路线有界派工属于该持续目标。不打印凭证，不下载重复模型，不购买资源。真实平台发布与账号/OAuth交互需具备对应明确授权；准备工作和代码可以完成，不能伪造这部分证据。

每批返回可审代码和实际证据，控制端独立复验后整理提交/同步记录。阶段 READY_FOR_REVIEW 与用户 ACCEPTED 分开。完整目标保持 active，不能因一个任务结束而改成“只完成V3修复”。
