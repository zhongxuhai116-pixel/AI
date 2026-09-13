# Instagram 集成说明（Instagram API with Facebook Login · Reels 发布）

- 文档核验日期：**2026-09-13**（本机日期）。以下所有 endpoint、权限、字段与数值均来自当日实际抓取的 Meta 官方文档（来源见 §9）。
- ⚠️ 动手写连接器之前，**必须逐项重新核验**本文所有值。Meta 会改动 API 版本（官方同一批页面里同时出现 `v25.0` 与 `v26.0` 示例）、权限名称、配额与规格；本文只是核验时点的快照。
- 本文只描述官方文档明确写出的行为；未能确认的一律标注 `未确认`，**不臆造任何数值**。本文不包含任何凭据、App ID、IG User ID、Page ID 或 token；这些值只能由部署环境注入。
- 本文选择 **Instagram API with Facebook Login**（用户用 Facebook 凭据登录，Instagram 专业账号已关联 Facebook 主页）。官方另一条配置 **Instagram API with Instagram Login**（`graph.instagram.com`，权限为 `instagram_business_*`）不在本文范围内，其取值须单独核验。

## 1. 账号前提（不满足则整条链路不可用）

官方前提（Overview / Getting Started / Content Publishing）：

1. Instagram 账号必须是 **Professional 账号（Business 或 Creator）**。官方明确：Instagram API with Facebook Login **无法访问个人（consumer）账号**。
2. 该专业账号必须**已关联一个 Facebook 主页（Page）**。本配置下 **Facebook Page 为 Required**；官方同时要求请求方（app user）在关联主页上能执行 **`MANAGE` 或 `CREATE_CONTENT`** 任务（admin 级别等价任务）。
3. 若该主页要求 **Page Publishing Authorization（PPA）**，则 **PPA 未完成前无法发布**，请求会失败。官方进一步说明：**应用无法探知某个主页是否需要 PPA**，因此建议提示用户提前完成 PPA。
4. 若该主页要求两步验证（2FA），Facebook 用户也必须已完成 2FA，否则请求失败。
5. 发布内容必须托管在**公开可访问的服务器**上：官方原文说明会对传入的 URL 发起 cURL 拉取（见 §5）。

**权限（scope）**。发布链路（媒体容器创建 + 发布）官方列出的权限：

| 权限 | 用途 | 备注 |
| --- | --- | --- |
| `instagram_basic` | 读取 IG 账号与媒体 | 官方 Getting Started 第一步即请求 `instagram_basic` + `pages_show_list` |
| `instagram_content_publish` | 创建容器并发布内容 | 发布能力的核心权限 |
| `pages_read_engagement` | 读取主页互动数据（发布/容器接口的必需项） | 见 IG User Media / IG Container / content_publishing_limit 的 Requirements 表 |
| `pages_show_list` | 列出用户可管理的主页（用于发现 Page → IG User 的关联） | 出现在 Getting Started 与 IG User Media **读取**接口的 Requirements |
| `ads_management` 或 `ads_read` | **仅当**该 app user 是通过 Business Manager 被授予主页角色时需要 | 官方在多个 Requirements 表中重复此条件 |

- 权限按**接口**要求申请：`GET /<IG_ID>/media` 的 Requirements 写的是 `instagram_basic` + （`pages_read_engagement` **或** `pages_show_list`），与发布接口不完全相同，必须以实际调用的接口页面为准。
- 官方有**互相矛盾**的权限拼写：App Review 页把可申请 Advanced Access 的权限写成 **`instagram_content_publishing`**（带 `-ing`），而发布/容器/配额接口与 Getting Started 页写的是 **`instagram_content_publish`**。**两者是否同一个权限 `未确认`**，申请前到 App Dashboard 内核对实际可勾选项。
- 官方要求（App Review 页）**一个应用只能选 Facebook Login 或 Instagram Login 之一，不能同时使用**。

**Token 类型**。
- 官方 Requirements 表把发布接口的 Access Token 类型写为 **User（Facebook User access token）**；Content Publishing 指南的对照表把 Facebook Login 列为 **Facebook Page access token**。
- 官方 Getting Started / Facebook Login for Business 步骤确实通过 `GET /me/accounts?fields=id,name,access_token,instagram_business_account` 同时取回 **Page access token** 与 **IG User ID**。
- 因此**发布调用到底该用 User token 还是 Page token，官方文档表述不一致，标记 `未确认`**；实现时需要实测两者（二者都需具备上述权限），不得凭文档措辞直接假定。

## 2. 授权（Facebook Login for Business / OAuth）

**前置配置**。
- 需要一个 **Business 类型 Meta App**，添加产品：Instagram（API setup with Facebook login）、Facebook Login for Business、Webhooks。
- 在 **Facebook Login for Business > Settings > Client OAuth Settings > Valid OAuth Redirect URIs** 填入回调地址；回调必须与该白名单**完全一致**（注意 App Dashboard 可能自动追加尾斜杠）。
- 官方还提供 **Facebook Login for Business 的 IG 引导流程**，让用户在同一个窗口内完成"转专业账号 → 建主页 → 关联主页"，从而减少账号前提不满足的情况：
  `https://www.facebook.com/dialog/oauth?client_id=<APP_ID>&display=page&extras={"setup":{"channel":"IG_API_ONBOARDING"}}&redirect_uri=<REDIRECT_URI>&response_type=token&scope=<PERMISSIONS>`

**授权请求**。
- 官方 Facebook Login for Business 页面给出的 `response_type` 为 **`token`**：授权成功后重定向到 `redirect_uri`，并以 **URL fragment（`#`）** 追加 `access_token`（短效）、`expires_in`、`data_access_expiration_time` 与 **`long_lived_token`**；官方要求**保存 long-lived token**。
- 官方通用 Login 流程（Manually Build a Login Flow）说明 `response_type` 也可为 **`code`**（默认值，响应放在 URL 参数中），此时**必须服务端到服务端**用
  `GET https://graph.facebook.com/<VERSION>/oauth/access_token?client_id=<APP_ID>&redirect_uri=<REDIRECT_URI>&client_secret=<APP_SECRET>&code=<CODE>`
  换取 access token。用户取消授权时回调会带 `error=access_denied&error_reason=user_denied`，必须优雅处理。
- **`code` 与 `token` 两种 response_type 的取舍 `未确认`**（Facebook Login for Business 页只演示了 `token`）。本产品是服务端连接器，凭据不应经过浏览器，倾向 `code`，但需按官方 Facebook Login 文档实测确认该选项在 IG 场景下可用。

**短效 / 长效 token 与刷新**。
| 环节 | 官方值 |
| --- | --- |
| 授权码 / 短效 token | 授权码（`code`）**1 小时**且**只能使用一次**（OAuth Authorize 页）；短效 access token **1 小时**（Overview；Facebook Login for Business 示例 `expires_in: 4815`） |
| 换长效 User token | `GET https://graph.facebook.com/<VERSION>/oauth/access_token?grant_type=fb_exchange_token&client_id=<APP_ID>&client_secret=<APP_SECRET>&fb_exchange_token=<SHORT_LIVED_TOKEN>` |
| 长效 User token 寿命 | 官方 Overview：**60 天**，可在到期前刷新；Long-Lived Access Tokens 页："generally lasts about 60 days"。**刷新（而非重走登录）的具体调用与次数上限 `未确认`**——官方该页只描述用短效 token 换长效 token |
| 长效 Page token | 由长效 User token 调 `GET /{app-scoped-user-id}/accounts` 得到；**无到期日**，只在特定条件下失效或被作废（官方指向 token 失效/失效原因页） |
| 失效 / 安全约束 | **不能用已过期的 token 换长效 token**，此时必须让用户重新走登录流程；换 token 的调用**含 app secret，只能服务端执行**；官方要求替换存储中的旧 token；同一长效 token 不得用于多个 web 客户端 |

**App Review / Business Verification（触达非测试用户的前提）**。
- **Access Levels**：`Standard Access` 是默认级别，仅适用于有应用/业务角色的人（开发与自测）；**`Advanced Access`** 才可服务"你不拥有或不管理"的 Instagram 专业账号，且需要 **App Review + Business Verification**。
- 官方原文：**Advanced Access 现在要求 Business Verification**；未完成前，来自其他 Business 的 app user **无法授予**这些权限，所有 feature 处于未激活状态。仅有应用角色（role）的用户不受此限。
- 按 App Review 页的开发场景表：应用只服务自己拥有/管理的业务 → Standard Access → **不需要** App Review；作为 Tech Provider 服务多个业务 → Advanced Access → **必须** App Review。
- 提交要求包含：应用可被外部加载测试、登录按钮可见（含 screencast）、用例与逐步说明、每个权限的合规用法说明，且**申请某些 Advanced Access 权限前至少需要成功调用 1 次 API**。
- 结论：**未完成 App Review + Business Verification 前，连接器只能在测试角色账号上工作**，不得对其他用户宣称可发布。

## 3. 发布流程（Reels 两步法 + 轮询）

官方流程为"创建容器 → （视频异步处理）→ 发布容器"三步语义、两个 endpoint：

**第 1 步：创建媒体容器**
```http
POST https://graph.facebook.com/<LATEST_API_VERSION>/<IG_USER_ID>/media
  ?media_type=REELS
  &video_url=<REEL_URL>          # 必填（视频/Reels）
  &caption=<CAPTION>
  &share_to_feed=<TRUE_OR_FALSE>
  &cover_url=<COVER_URL>
  &thumb_offset=<THUMB_OFFSET>
  &location_id=<LOCATION_PAGE_ID>
  &collaborators=<USERNAMES>
  &audio_name=<AUDIO_NAME>
  &user_tags=<ARRAY>
  &trial_params=<TRIAL_PARAM>
  &is_ai_generated=<TRUE_OR_FALSE>
  &access_token=<TOKEN>
```

官方参数语义（IG User Media 参考）：

| 参数 | 官方说明 |
| --- | --- |
| `media_type` | 轮播/快拍/Reels **必填**，取值 `CAROUSEL` / `REELS` / `STORIES` |
| `video_url` | 视频与 Reels 必填；**会对该 URL 发起 cURL 拉取，必须在公开服务器上** |
| `caption` | 上限 **2200 字符、30 个 hashtag、20 个 `@` 提及**；**不支持**轮播内的单个图片/视频 |
| `share_to_feed` | Reels 专用；`true` 表示可同时出现在 Feed 与 Reels 标签。官方警告：**该值不决定 Reels 是否真的出现在 Reels 标签**，取决于资格条件与算法 |
| `cover_url` | Reels 专用封面图，必须公开可访问；若同时给 `cover_url` 与 `thumb_offset`，**用 `cover_url` 并忽略 `thumb_offset`** |
| `thumb_offset` | 视频/Reels 封面帧位置，**毫秒**，默认 `0` |
| `location_id` | 需要带地理位置数据的 Facebook Page ID；主页无位置数据会失败于 `INSTAGRAM_PLATFORM_API__INVALID_LOCATION_ID` |
| `collaborators` | 最多 **3** 个 Instagram 用户名；Feed 图片、Reels、轮播支持，**Stories 不支持** |
| `audio_name` / `trial_params` / `is_ai_generated` | 音频名**只能重命名一次**（创建时或之后在音频页）；试用 Reels 的 `graduation_strategy` 取 `MANUAL` 或 `SS_PERFORMANCE`；`is_ai_generated` 为 AI 自披露，轮播只能设在轮播容器上，设在子项会报错 |

- 成功返回容器 ID：`{"id":"<IG_CONTAINER_ID>"}`。**视频上传是异步的**：拿到容器 ID **不代表上传成功**，官方要求通过容器 `status_code` 确认（`FINISHED` 表示视频已成功上传）。
- 每个 IG 账号**滚动 24 小时内最多创建 400 个容器**；容器 **24 小时未发布即过期（`EXPIRED`）**。
- 另一条官方路径（仅 Facebook Login for Business 应用可用）：`upload_type=resumable` 创建可续传会话，响应额外返回 `uri`（`https://rupload.facebook.com/ig-api-upload/<VERSION>/<IG_CONTAINER_ID>`），再向该 host `POST` 上传本地文件（`offset` / `file_size` / `--data-binary @file`）或托管 URL（`file_url` 头）。可作备选，但需单独验收。

**第 2 步：轮询容器状态**
```http
GET https://graph.facebook.com/<API_VERSION>/<IG_CONTAINER_ID>?fields=status_code,status&access_token=<TOKEN>
```

`status_code` 官方取值：

| 值 | 官方含义 |
| --- | --- |
| `EXPIRED` | 容器 24 小时内未发布，已过期 |
| `ERROR` | 容器发布流程失败 |
| `FINISHED` | 容器与其媒体对象已就绪，可以发布 |
| `IN_PROGRESS` | 容器仍在处理中 |
| `PUBLISHED` | 容器的媒体对象已发布 |

- `status` 字段：当 `status_code` 为 `ERROR` 时，该值是**错误子码**（指向 Error Codes 参考）。
- 官方轮询建议（Content Publishing 排障节）：**每 1 分钟查询一次，最多查询 5 分钟**。
- 官方另提供 `copyright_check_status`（`matches_found` + `status`：`completed`/`error`/`in_progress`/`not_started`），用于判断上传视频是否命中版权。
- 在容器就绪前发布，会返回错误 `9007 / 2207027`："The media is not ready for publishing" → 必须先等到 `FINISHED`。

**第 3 步：发布容器**
```http
POST https://graph.facebook.com/<API_VERSION>/<IG_USER_ID>/media_publish
  ?creation_id=<IG_CONTAINER_ID>
  &access_token=<TOKEN>
```

- `creation_id` **必填**，为容器 ID（单媒体或轮播容器）。
- 成功返回**已发布媒体 ID**：`{"id":"<IG_MEDIA_ID>"}`。
- Reels 的判定注意：官方明确 **发布 Reels 后读回 `media_type` 会得到 `VIDEO`**；要判断是否是 Reels，必须读 **`media_product_type`** 字段。该字段表示 Reels 时的**具体字符串值官方未在本批页面写出，标 `未确认`**。
- `media_publish` 的 Deleting / Reading / Updating **官方均为 not supported**。

## 4. 素材与文案限制（官方原文数值）

**Reels 视频规格**（IG User Media · Reel Specifications）
| 项 | 官方值 |
| --- | --- |
| 容器 | **MOV 或 MP4（MPEG-4 Part 14）**，无 edit lists，`moov` atom 在文件前部 |
| 音频 | 编码 **AAC**，采样率最高 **48 kHz**，1 或 2 声道；码率 **128 kbps** |
| 视频 | 编码 **HEVC 或 H264**，progressive scan，closed GOP，4:2:0 色度抽样；帧率 **23–60 FPS**；码率 **VBR，最高 25 Mbps** |
| 分辨率 / 宽高比 | 最大**横向 1920 像素**；宽高比须落在 **0.01:1 – 10:1**，官方**推荐 9:16** 以避免裁切或黑边 |
| 时长 / 体积 | 时长 **最短 3 秒、最长 15 分钟**；文件**最大 300 MB** |

- Reels 封面图：JPEG，**最大 8 MB**，sRGB；建议 9:16（非 9:16 时官方取中间 9:16 矩形裁切；分享到 Feed 时取中间 1:1）。
- Reels 其他限制：**Reels 不能出现在轮播中**；音频标记仅支持原创音频；发布时遵循账号隐私设置（如"允许混剪"）。
- 错误码对应：不支持的视频格式 → `352 / 2207026`（提示使用 MOV 或 MP4）；封面帧越界 → `1 / 2207057`（`thumb_offset` 必须 ≥ 0 且小于视频时长）。

**文案上限**：**2200 字符 / 30 个 hashtag / 20 个 `@` 提及**（IG User Media 的 `caption` 说明与 Error Codes `2207040`、`36004 / 2207010` 一致）。

**速率限制**（⚠️ 官方文档自相矛盾，必须实测）

| 官方出处 | 数值 |
| --- | --- |
| Content Publishing 指南 · Rate Limit | **100** API-published posts / 24 小时滚动窗口；轮播计为 1 条；限制在 `POST /<IG_ID>/media_publish` 上强制执行 |
| `media_publish` 参考 · Limitations；Content Publishing 指南 · Carousel Limitations | 专业账号 **50** posts / 24 小时滚动窗口 |
| `content_publishing_limit` 参考 · `config` | `quota_total` = **50**，`quota_duration` = **86400** 秒 |
| Content Publishing 指南 · 容器限制 | **400** 容器 / 滚动 24 小时 |

**结论：不能凭文档断言上限是 100 还是 50。连接器必须调用 `GET /<IG_USER_ID>/content_publishing_limit?fields=quota_usage,config` 取运行时真值**（`quota_usage` 省略 `since` 时为最近 24 小时已发布容器数；`since` 不得早于 24 小时前），并把结果作为 `validate_package` 的阻断依据。官方还建议应用**自行实施**配额限制，尤其涉及定时发布时。

## 5. 素材托管要求（无直接二进制上传到 `/media`）

- 官方对标准路径的要求是 **`video_url` / `image_url` 指向公开可访问服务器**，因为"**We cURL the video using the passed-in URL, so it must be on a public server**"。Content Publishing 指南亦写明："**Media on a public server** — We cURL media used in publishing attempts, so the media must be hosted on a publicly accessible server **at the time of the attempt**"。官方还强烈推荐 URL 只含 US-ASCII / IETF 标准字符集，否则请求失败；拉取失败的错误码为 `9004 / 2207052`（media could not be fetched from this uri）与 `-2 / 2207003`（下载超时）。
- **唯一可上传本地文件的官方路径是 `upload_type=resumable` + `rupload.facebook.com`**（官方示例明确支持"本机文件"与"公开 CDN 文件"两种来源），且该能力**仅限已实现 Facebook Login for Business 的应用**。对本产品的含义：一键发布前必须有一步"把渲染结果放到公开可访问 URL"的受控资产托管，或走 resumable 上传；两者都要在 `validate_package` 里前置校验。

## 6. 必须保留的用户交互（不得绕过）

以下环节**只能由用户在 Meta 官方界面完成**，连接器不得用浏览器自动化、Cookie 注入、私有接口或任何方式替代：

1. **账号前提**：把 Instagram 账号转为 Professional 账号、创建/关联 Facebook Page，以及通过 Facebook Login for Business 的 `IG_API_ONBOARDING` 引导完成这组配置。
2. **登录与授权**：用户在 Meta 授权窗口中登录 Facebook 并逐项授予权限（见 §1）。凭据不得由本产品收集、代理或缓存。
3. **PPA / 2FA**：官方明确**应用无法判断某主页是否需要 Page Publishing Authorization**，只能在发布失败后得知；主页若要求 2FA，也必须由用户本人完成。两者都必须引导用户提前在 Facebook 侧处理。
4. **App Review + Business Verification**：由开发者/业务方在 Meta 侧提交与等待审批；未通过前不得对非测试用户开放。
5. **平台侧审核与社区限制**：账号被 checkpoint/限制时（错误 `25 / 2207050`："The Instagram account is restricted"）官方要求**用户在 Instagram App 内登录并完成平台要求的操作**才能恢复；疑似垃圾行为的发布会被限制（`4 / 2207051`）。连接器只能提示用户处理，不能代做。
6. **重新授权**：长效 token 失效（过期、密码变更、用户撤销、被作废）后，官方要求**重新走登录流程**；连接器此时应置 `AUTH_REQUIRED`，不得反复盲目刷新。

> **明确禁止**：用浏览器自动化（headless browser、模拟点击、抓取页面）绕过上述授权、PPA、2FA、App Review 或平台审核流程。官方未提供任何替代通道；本产品的"一键发布"只在上述交互全部由用户完成后，才执行 API 请求。

## 7. 发布后的结果读取与状态语义

- 发布成功即返回 **IG Media ID**（`POST /media_publish` 的 `id`），这是后续所有读取的句柄，必须落库为 `operation_handle` 的一部分。
- 回读媒体信息：`GET /<IG_MEDIA_ID>?fields=permalink,shortcode,media_type,media_product_type,timestamp,username,...`；其中 `permalink` 官方定义为 **"Permanent URL to the media."**（公开字段），`shortcode` 为 **"Shortcode to the media."**。
- **permalink 的可用时机**：本批官方页面**没有**任何"发布后 permalink 立即可用/需要等待"的表述，因此 **`未确认`；连接器不得在拿不到 permalink 时编造 URL**（与规划 12.12 一致：允许只保留平台 ID）。唯一的文档化例外是**相册内图片（children）**：官方明确 **`permalink` 等字段不能用于相册内的照片**。
- **"审核中/受限"语义**：
  - 容器侧只有 §3 的 5 个 `status_code`；**不存在**文档化的"内容审核中"容器状态 → **`未确认`**。
  - 账号侧受限有明确错误码：`25 / 2207050`（账号 inactive/checkpointed/restricted）。此时**不得**把发布标记为成功或失败重试，应回到"需用户处理"状态。
  - 版权侧：`copyright_check_status.matches_found=true` 表示命中版权；命中后 Reels 的可见性后果**官方未描述，`未确认`**。
  - 发布成功**不等于**在 Reels 标签可见：`share_to_feed` 官方警告该参数不决定实际曝光（还受资格条件与算法影响）。

## 8. 对本产品接口的映射

| 本产品接口 | 对应官方调用/机制 | 落地要点 |
| --- | --- | --- |
| `begin_authorization()` | Facebook Login for Business 授权 URL（`https://www.facebook.com/dialog/oauth`，`display=page`，`extras={"setup":{"channel":"IG_API_ONBOARDING"}}`，`response_type=token` 或 `code`，`scope=instagram_basic,instagram_content_publish,pages_read_engagement,pages_show_list`） | 生成并保存 `state`（官方说明 `state` 会原样回传，用于防 CSRF）；回调必须在 **Valid OAuth Redirect URIs** 白名单内；`code` vs `token` 取舍 `未确认`，需实测；不得请求未使用的权限 |
| `exchange_callback()` | `code` 流程：`GET /oauth/access_token?client_id&redirect_uri&client_secret&code`（服务端到服务端）；随后 `GET /oauth/access_token?grant_type=fb_exchange_token...` 换长效 User token；再 `GET /me/accounts?fields=id,name,access_token,instagram_business_account` 取 Page token 与 IG User ID | `code` 有效期 **1 小时且只能用一次**；必须校验实际授予的 scope（缺 `instagram_content_publish` 直接判不可发布）；长效 token 加密落库；Page 多于一个时必须让用户选择目标账号 |
| `refresh_authorization()` | 长效 User token **60 天**，官方 Overview 称"可在到期前刷新"；失效后必须重走登录 | **官方未给出"刷新长效 token"的具体 endpoint（`未确认`）**；能确认的只有"用短效换长效"。因此策略应为：到期前用可确认的机制续期，无法续期则置 `AUTH_REQUIRED`；**不得**用已过期 token 尝试换取（官方禁止） |
| `get_account_capabilities(account_ref)` | `GET /<PAGE_ID>?fields=instagram_business_account` 校验关联；`GET /<IG_USER_ID>?fields=...` 读取账号；`GET /<IG_USER_ID>/content_publishing_limit?fields=quota_usage,config` 读取配额 | 能力标志：是否 Professional 账号且已关联 Page（否则直接 BLOCKED）；当前 app 是否已获 Advanced Access（否则仅测试角色可用）；配额剩余量（运行时真值，解决 §4 的 50/100 矛盾）；**PPA 与 2FA 无法通过 API 探测 → 只能作为"已知可能失败"的提示项返回** |
| `validate_package(package, account, publish_options)` | 官方 Reel Specifications + `caption` 上限 + 配额 + 容器上限 | 校验：容器 = MOV/MP4、音视频编码 = AAC / H264 或 HEVC、帧率 23–60、横向 ≤1920、宽高比在 0.01:1–10:1（推荐 9:16）、码率 ≤25 Mbps（视频）/128 kbps（音频）、时长 3 秒–15 分钟、体积 ≤300 MB、封面 JPEG ≤8 MB；caption ≤2200 字符 / ≤30 hashtag / ≤20 `@`；`video_url` 必须为**公开可访问 HTTPS**（本地文件只能走 resumable）；`thumb_offset` 为毫秒且 < 时长；`collaborators` ≤3；`trial_params` 与 `media_type=REELS` 同时出现；配额剩余 > 0。**任何 `未确认` 项（如 `media_product_type` 取值）不得作为硬编码常量** |
| `submit_publish(package, options, execution_key)` | ① `POST /<IG_USER_ID>/media`（`media_type=REELS` + `video_url` + `caption` + `share_to_feed` + `cover_url`/`thumb_offset` + `location_id` + `collaborators` + `is_ai_generated` 等）② ③ `GET /<IG_CONTAINER_ID>?fields=status_code,status` 轮询至 `FINISHED` ④ `POST /<IG_USER_ID>/media_publish?creation_id=<CONTAINER_ID>` | `execution_key` 作为幂等键，与"容器 ID + 媒体 ID"一并落库；**拿到容器 ID ≠ 上传成功**，必须先轮询；轮询节奏按官方建议**每分钟 1 次、最多 5 分钟**，超时按失败处理并保留容器 ID；容器 **24 小时过期**，过期须新建容器（`2207020`/`2207008`）；`media_publish` 成功返回 media ID，**才可进入 PROCESSING → PUBLISHED 判定** |
| `query_publish(operation_handle)` | 容器阶段：`GET /<IG_CONTAINER_ID>?fields=status_code,status`；已发布阶段：`GET /<IG_MEDIA_ID>?fields=permalink,shortcode,media_type,media_product_type,timestamp` | 用 5 个 `status_code` 映射内部状态：`FINISHED`→待发布、`IN_PROGRESS`→PROCESSING、`ERROR`→FAILED（`status` 为错误子码）、`EXPIRED`→FAILED(需重建)、`PUBLISHED`→PUBLISHED；`permalink` 取不到时**保留 media ID，不编造 URL** |
| `cancel_publish(operation_handle)` | **官方无取消/删除端点** | **可取消性结论：已提交的发布不可取消、不可回滚。** 依据：`/<IG_USER_ID>/media`、`/<IG_USER_ID>/media_publish`、IG Container 三个参考页的 **Updating / Deleting 全部为 "This operation is not supported."**。唯一"取消"语义是：本地停止轮询与不再调用 `media_publish`，让容器在 **24 小时**后自然 `EXPIRED`。因此本接口应显式返回 **unsupported**，产品侧必须在提交前做二次确认（与规划 12.11 允许 `cancel_publish` 返回 unsupported 一致） |

额外映射注意：`POST /media` 与 `POST /media_publish` 之间的**时间窗口只有 24 小时**（容器过期），长时审批/排期必须落在"发布前创建容器"的顺序上；发布失败后**不要盲目重试同一个容器**，官方建议的恢复方式多为"生成新容器再试"（`2207006` / `2207008` / `2207053` 等）。

## 9. 来源清单（均为本次实际抓取的官方页面）

| URL | 说明 |
| --- | --- |
| https://developers.facebook.com/docs/instagram-platform/content-publishing | Content Publishing 指南：公开服务器要求、PPA、权限表、容器/发布/状态端点、轮播限制、100 条配额表述、状态码与 5 分钟轮询建议（页面标注 Updated Jun 30, 2026） |
| https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media | IG User Media 参考：Reels 容器参数（`media_type`/`video_url`/`cover_url`/`share_to_feed`/`thumb_offset`/`location_id`/`collaborators`/`audio_name`/`trial_params`/`is_ai_generated`）、Reel 规格、caption 上限、400 容器限制、resumable 上传 |
| https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media_publish | IG User Media Publish：`creation_id`、返回 media ID、**50 posts/24h** 限制、PPA/2FA 前置、Updating/Reading/Deleting 均不支持 |
| https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-container | IG Container：`status_code` 五个取值、`status` 错误子码、`copyright_check_status`、Deleting 不支持 |
| https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/content_publishing_limit | 内容发布配额：`quota_usage`、`config.quota_total=50`、`quota_duration=86400`、`since` 不得早于 24 小时 |
| https://developers.facebook.com/docs/instagram-platform/reference/instagram-media | IG Media 参考：`permalink`（Permanent URL）、`shortcode`、相册子项不支持 `permalink` 等字段的限制 |
| https://developers.facebook.com/docs/instagram-platform/overview | Instagram Platform Overview：两条配置对比、Access Levels、Advanced Access 需 App Review + Business Verification、code 1 小时 / 短效 token 1 小时 / 长效 60 天、host 与权限清单、Facebook Page 为 Required |
| https://developers.facebook.com/docs/instagram-platform/instagram-api-with-facebook-login | Instagram API with Facebook Login：无法访问个人账号、发布能力对专业账号开放（Stories 仅 business） |
| https://developers.facebook.com/docs/instagram-platform/instagram-api-with-facebook-login/get-started | Getting Started：专业账号 + 关联主页 + 可在主页执行任务的开发者账号、请求 `instagram_basic`+`pages_show_list`、`GET /me/accounts`、`GET /{page-id}?fields=instagram_business_account` |
| https://developers.facebook.com/docs/instagram-platform/instagram-api-with-facebook-login/business-login-for-instagram | Facebook Login for Business：登录 URL 全部参数、`IG_API_ONBOARDING`、回调 fragment（`access_token`/`expires_in`/`long_lived_token`）、`GET /me/accounts` 取 Page token 与 IG 账号 |
| https://developers.facebook.com/docs/instagram-platform/app-review | App Review for Instagram API：开发场景与是否需审核、可申请 Advanced Access 的权限清单（此处写作 `instagram_content_publishing`）、提交要求（含"至少成功调用 1 次 API"）、不可同时使用两种登录 |
| https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/error-codes | Instagram 错误码表：`2207050` 账号受限、`2207042` 触达发布上限、`2207027` 媒体未就绪、`2207026` 视频格式、`2207052` 无法拉取 URI、`2207057` 封面越界、`2207040`/`36004` 文案与 `@` 上限 |
| https://developers.facebook.com/docs/facebook-login/guides/access-tokens | Access Tokens：token 类型定义（User / Page / App）、Page token 通过 `GET /{user-id}/accounts` 获取 |
| https://developers.facebook.com/docs/facebook-login/guides/access-tokens/get-long-lived | Long-Lived Access Tokens：`grant_type=fb_exchange_token` 换长效 User token（约 60 天）、长效 Page token 无到期日、过期 token 不可用于换取、必须服务端调用 |
| https://developers.facebook.com/docs/facebook-login/guides/advanced/manual-flow | Manually Build a Login Flow：Login dialog 与 `state`、`response_type` 取值（`code`/`token`/`code token`/`granted_scopes`）、`code` 换 token 端点、取消授权处理、`debug_token` |
| https://developers.facebook.com/docs/instagram-platform/reference/oauth-authorize<br>https://developers.facebook.com/docs/instagram-platform/reference/access_token | 两条 **Instagram Login 配置**的参考页，本文只用于对照确认授权码/长效 token 语义：`code` 有效期 1 小时且只能用一次、取消授权参数；短效 1 小时换长效 60 天（`ig_exchange_token`，`expires_in: 5184000`） |
| https://developers.facebook.com/docs/development/release/business-verification | Business Verification：Advanced Access 必须完成 Business Verification，未完成时其他业务用户无法授权、feature 未激活 |
| https://github.com/fbsamples/reels_publishing_apis | 官方 Content Publishing 页点名的 Reels 发布示例仓库（`insta_reels_publishing_api_sample`），仅作实现参考 |

## 10. 未确认项（不要臆造数值/结论）

1. **发布速率上限到底是 50 还是 100**：官方同一批页面同时存在"100 API-published posts / 24h"（Content Publishing 指南）与"50 posts / 24h"（`media_publish` 参考）、`config.quota_total=50` 三种表述 → 只能以 `content_publishing_limit` 运行时返回值为准。
2. **发布调用用 User token 还是 Page token**：Requirements 表写 User token，Content Publishing 对照表写 Facebook Page access token → 需实测。
3. **`response_type` 该用 `code` 还是 `token`**：Facebook Login for Business 页只演示 `token`（fragment 返回），未说明 `code` 是否同样受支持。
4. **长效 token 的刷新 endpoint 与刷新次数/条件**：官方只文档化了"短效换长效"，未给出独立的"刷新"调用。
5. **`media_product_type` 表示 Reels 时的确切字符串值**：官方只说该字段用于判断是否 Reels，未给出取值列表。
6. **发布后 `permalink` 的可用时机**：官方无任何"可能延迟/立即不可用"的表述；只能在实现时按真实响应处理。
7. **内容"审核中"状态**：容器状态里没有审核态；Reels 在平台侧是否进入审核、审核不通过的可见性后果，官方未描述。
8. **`copyright_check_status.matches_found=true` 之后的处置与可见性后果**：官方只描述检测字段，未描述后续流程。
9. **PPA / 2FA 是否可经 API 查询**：官方明确"无法判断主页是否需要 PPA"，2FA 亦无查询接口。
10. **权限名 `instagram_content_publishing` 与 `instagram_content_publish` 是否同一权限**：App Review 页与各接口页写法不一致。
11. **当前生效的 Graph API 版本**：同批页面里 IG User Media 写"latest API version is v25.0"，而 Content Publishing 示例用 `v26.0` → 施工时须查 Graph API Changelog 与 App Dashboard。
12. **`user_tags` 在 Reels 上的支持范围**：参数表把 `user_tags` 描述为"images, videos, and stories"，Reels 是否等价支持未明确。
13. **单账号可授权的 Page 数量上限、以及 Page token 的失效具体条件**：官方只给出"特定条件下失效"的链接描述，未给可判定清单。
14. **非公开/受限内容的回读能力**：官方未说明 API 发布后若被平台降级/限制，`GET /<IG_MEDIA_ID>` 会返回什么。
