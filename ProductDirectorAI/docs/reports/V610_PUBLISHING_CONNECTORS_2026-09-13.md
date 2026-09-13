# V6-10…15 发布框架与四个平台连接器

- 日期：2026-09-13（云端会话）
- 范围：主规划 12.11（一键发布与平台连接器）、12.12（发布状态与授权绑定）、12.13（发布 API）；四个默认原生连接器 TikTok / YouTube / Instagram / Facebook Page。
- 状态：**框架完成**（测试 30/30、真机冒烟 16/16）；四个连接器**在无真实授权时如实 BLOCKED/NOT_CONFIGURED**；真实平台发布未执行（无授权，属阻塞项而非缺陷）。V6 整体仍为 IN_PROGRESS。

## 1. 交付物

### 1.1 `apps/api/productdirector_api/publishing.py`（规则层）

| 能力 | 实现与诚实边界 |
| --- | --- |
| 状态机 | `DRAFT → PREFLIGHT → WAITING_APPROVAL → QUEUED → UPLOADING → PROCESSING → PUBLISHED`，分支 `AUTH_REQUIRED / BLOCKED / RECONCILING / FAILED / CANCEL_REQUESTED / CANCELLED`；迁移表显式列出，**`DRAFT → PUBLISHED` 这类跳跃被拒绝**（提交成功 ≠ 已发布） |
| 预检 | 逐条阻断：包非 APPROVED、包哈希缺失/不一致、QA 未通过、缺审批引用、未选可见性、**可见性取值不跨平台照搬**、声明字段缺失、合成内容声明缺失、文案超限、BGM 无许可、字体无许可、时长超平台/为 0、定时发布缺时区；能力未实时查询给 WARNING |
| 审批绑定 | 必须引用预检 `snapshot_hash`，并显式确认 `package_hash/account/visibility/disclosures`；绑定字段任一变化 → `approval_still_valid` 判定失效（逐字段比对，不做"大致相同"） |
| 防重 | 键 = `package_version + account + publish_intent_id`；同键复用已有任务，用户再次发布必须新建 intent |
| 结果解释 | `interpret_query`：只有上游明确 PUBLISHED 才标已发布；未知状态 → `RECONCILING`；**没有公开链接时只保留平台 ID，不捏造 URL** |
| 取消/重试 | `cancel_capability` 按平台如实返回支持程度；`retry_decision` 在提交结果未知时拒绝重试（先对账），授权过期先重新授权 |

### 1.2 `apps/api/productdirector_api/publishing_connectors.py`（连接器层）

四个连接器共用官方端点/scope/素材限制，全部来自 `docs/integrations/*.md` 的官方来源；**未在官方文档确认的数值不写死**，作为 `unconfirmed_limits` 在预检里给 WARNING。

| 平台 | 官方端点 | scope | 取消 | 未确认项（示例） |
| --- | --- | --- | --- | --- |
| TikTok | authorize / oauth token / creator_info / video init / status fetch | `video.publish`、`video.upload` | **不支持**（官方仅 PULL_FROM_URL 下载任务可 best-effort 取消） | 封面单独上传限制、话题上限、定时发布 |
| YouTube | Google OAuth / resumable `videos.insert` | `youtube.upload` + `youtube.readonly` | 支持（删除已发布视频是独立动作） | `videos.insert` 配额成本（官方页面自相矛盾）、单文件上限 |
| Instagram | Facebook Login / `media` + `media_publish` | `instagram_basic`、`instagram_content_publish`、`pages_read_engagement`、`pages_show_list` | **不支持**（官方三个参考页均写 not supported） | 速率上限 50 vs 100、User/Page token、permalink 时机 |
| Facebook Page | Graph 视频发布（host: graph-video） | `pages_show_list`、`pages_read_engagement`、`pages_manage_posts` | 支持（删帖 / 中止在途上传语义不同） | 普通视频体积与时长、Page token 有效期 |

连接器行为：
- 缺任一必需凭据 → `status=NOT_CONFIGURED`，列出**缺失项与需要的环境变量名**（`PRODUCTDIRECTOR_<PLATFORM>_CLIENT_ID` 等）。
- `begin_authorization`：**未配置凭据时不生成假授权链接**；配置后生成带 `state` + PKCE(S256) 的官方授权 URL。
- `exchange_callback` / `refresh_authorization`：真实 HTTP 流程；刷新失败返回 `AUTH_REQUIRED` 且**不循环重试**；令牌只保存引用（`env:…ACCESS_TOKEN`），明文不落库、不回传。
- `get_account_capabilities`：TikTok 走 `creator_info`（官方要求发布前查询），YouTube 走 `channels.list`，Meta 走 Graph。
- `submit_publish`：本部署**不发起真实上传**（无真实授权与平台审批），返回 `BLOCKED` 且说明原因；TikTok 缺 `creator_info` 时先报 `creator_info_required`。
- `cancel_publish`：不支持的平台显式抛 `cancel_unsupported`。
- 平台要求的人工交互（隐私/可见性选择、合成内容声明、页面角色、App Review）必须保留；**禁止浏览器自动化绕过**。

### 1.3 API（主规划 12.13）

`GET /publishing/connectors`、`GET /publishing/accounts`、`POST /publishing/accounts/connect`、`POST /publishing/oauth/{platform}/callback`（state 一次性 + PKCE）、`DELETE /publishing/accounts/{id}`（断开并阻断在途任务）、`POST /publishing/preflight`、`POST /publishing/approvals`、`POST /publishing/jobs`（202 + 幂等）、`GET /publishing/jobs`、`GET /publishing/jobs/{id}`、`POST /publishing/jobs/{id}/reconcile|retry|cancel`。

新增五张表：`connected_accounts`、`oauth_states`、`publish_preflights`、`publish_approvals`、`publish_jobs`（内联 SQLite 与 `db/schema.postgres.sql` 同步）。Automation scope：读用 `packages:read`、写用 `publish:write`；`POST /publishing/jobs` 进入强制幂等键路由表。

### 1.4 控制台页面「发布与审批」

`apps/web/src/App.jsx` 新增 `PublishPage`（导航第 18 项）：四个连接器状态与官方资料链接、授权入口（未配置凭据时明确说明）、已授权账号与断开、预检表单（包/账号/可见性/文案/三项声明）、审批与快照哈希展示、发布任务列表（对账/取消）与四条诚实性说明。

## 2. 验证证据

### 2.1 测试 `tests/test_v6_publishing.py`（30 例，全部通过）

规则层：状态机拒绝跳跃、防重键、预检阻断（QA/审批/可见性/声明/字体/BGM/时长）、快照哈希随绑定字段变化、审批必须显式确认、绑定字段变化即失效、查询结果不升级为已发布、取消支持程度、重试需安全失败。
连接器层：四连接器未配置时 NOT_CONFIGURED 且列出环境变量、未配置不生成假链接、无凭据换 token 被拒、**用本机 stub provider 走通真实 HTTP 换 token**（校验 `client_key`/`code_verifier` 且令牌明文不回传）、刷新失败 → AUTH_REQUIRED、提交发布 BLOCKED、TikTok creator_info 前置、Instagram 取消 unsupported、平台限制只阻断官方确认项（未确认给 WARNING）。
接口层：连接器诚实性、state 一次性与未知 state、无账号预检阻断且不产快照、不可执行预检不许审批、Automation scope 越权 403、**注入 READY 连接器后的完整流程**（审批 → 202 创建 → 选项不一致 → WAITING_APPROVAL → 同键复用 → 新 intent 新任务 → 对账 RECONCILING → 取消如实不支持）、重试安全判定、断开幂等并阻断在途任务。

### 2.2 真机 PostgreSQL 冒烟（16/16 PASS）

```
[PASS] 连接器：四个原生连接器诚实 BLOCKED（ready=[]）
[PASS] 连接器：列出缺失凭据与环境变量名
[PASS] 连接器：声明平台要求的人工交互与禁止浏览器自动化
[PASS] 连接器：未确认限制显式列出（n=4）
[PASS] OAuth：回调地址与 state/PKCE 说明
[PASS] 授权：未配置凭据时不生成假链接（url=None）
[PASS] 回调：未配置凭据明确 409 not_configured
[PASS] 回调：state 一次性（重放被拒）
[PASS] 回调：未知 state 400（防 CSRF）
[PASS] 账号：真实部署没有已授权账号
[PASS] 预检：无已授权账号时阻断且不产出快照（no_connected_account）
[PASS] 审批：不可执行预检不许创建审批
[PASS] 任务：真实部署没有发布任务（未授权不产生假任务）
[PASS] Automation：packages:read 可读连接器；缺 publish:write 时 403
```

### 2.3 全量回归与前端

- 云端全量：**612/612 OK**（V6-09 为 582/582，本阶段新增 30 例）。
- `npm run build` 成功、`npm run test:sites` 4/4、web 200。

## 3. 诚实边界（未完成 / 不能声称的部分）

1. **没有任何真实平台发布**：四个连接器都没有真实应用凭据与账号授权，`/publishing/accounts` 为空、`/publishing/jobs` 为空；真实发布链路（上传、平台处理、最终状态、公开链接）**未被验证**。配置凭据并在真实账号上完成授权与平台审批后才能启用。
2. **`submit_publish` 在本部署不发起真实上传**：这是有意的安全默认值，代码里有官方端点与参数映射，但未启用真实调用。
3. **平台素材限制只写官方确认值**：未确认项（见各连接器 `unconfirmed_limits`）不参与阻断，需要在真实账号上实测后写入配置。
4. **OAuth 只在本机 stub provider 上验证**：没有对真实平台 OAuth 端点做端到端授权（需要真实应用与账号）。
5. **自动化策略审批**（`automation_policy`）只实现了范围字段与失效规则，没有实现"按平台规则自动发布未来内容"的执行器。
6. **发布状态对账依赖上游查询**：`query_publish` 在没有凭据时返回 UNKNOWN → `RECONCILING`；没有回调（webhook）入口用于平台侧状态推送。
7. **Marketplace/Amazon/Pinterest** 仍是"人工导入指引"路线（主规划允许），未实现原生连接器。
