# TikTok Content Posting API 连接器集成文档

> 状态：调研稿（未实现、未联调）。本文件只记录官方文档已确认的事实与明确未知项，供后续连接器施工参考。

## 1. 官方资料与核验时间

- 核验日期：**2026-09-13**（本文件所有数值均在该日抓取自 `developers.tiktok.com` 官方文档，各页面标注的 “Last updated” 见第 9 节）。
- 时效声明：TikTok 开放平台变更频繁（本次抓取时部分页面已更新到 2026-08-04 / 2026-08-19 / 2026-08-24）。**任何连接器开工前，必须重新逐项核验本文件中的端点、scope、限额与枚举值**；本文件不是合同的替代品。
- 记录口径：官方文档给出具体数值的一律照抄；官方未给出或本次未在官方页面确认的，写 `未确认`，并注明应在何处核对。**不得**用第三方博客/SDK 填补官方空缺。
- 本文件不含任何凭证、token 或账号标识；示例中的 `act.xxx`/`CLIENT_KEY` 等均取自官方文档示例，仅为占位符。

## 2. 授权流程（OAuth 2.0 Authorization Code）

### 2.1 前置条件

- 必须在 TikTok for Developers 注册应用（`Manage apps`），并完成应用审核；内容发布类能力需在应用页添加 **Content Posting API** 产品，Direct Post 还需单独启用 **Direct Post** 配置（见 [Get Started - Direct Post](https://developers.tiktok.com/doc/content-posting-api-get-started/)）。
- 官方明确要求：应用必须被批准 `video.publish` scope，且目标 TikTok 用户也必须授权该 scope（同上页面）。scope 需在应用配置中勾选并在审核中演示（[App Review Guidelines](https://developers.tiktok.com/doc/app-review-guidelines/)）。
- Web 应用的 `redirect_uri` 必须已注册：绝对 URL、必须 `https`、必须静态（不允许带 query 参数）、不允许 fragment、每个应用最多 10 个、单个长度 < 512 字符（[Login Kit Web](https://developers.tiktok.com/doc/login-kit-web/)）。
- 发布路径若用 `PULL_FROM_URL`，其域名或 URL 前缀必须先在应用页完成归属验证（[Media Transfer Guide](https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide/)）。

### 2.2 端点（官方文档原文）

| 用途 | 方法 | URL |
| --- | --- | --- |
| 授权页（跳转用户） | GET（浏览器跳转） | `https://www.tiktok.com/v2/auth/authorize/` |
| 换取 / 刷新 token | POST | `https://open.tiktokapis.com/v2/oauth/token/` |
| 撤销授权 | POST | `https://open.tiktokapis.com/v2/oauth/revoke/` |

- 授权页必须通过 HTTPS 访问；查询参数以 `application/x-www-form-urlencoded` 编码（UTF-8）：`client_key`、`response_type=code`、`scope`（逗号分隔）、`redirect_uri`、`state`；可选 `disable_auto_auth`（0/1）。
- 换取 token 的请求体（`Content-Type: application/x-www-form-urlencoded`）：`client_key`、`client_secret`、`code`、`grant_type=authorization_code`、`redirect_uri`（必须与请求 code 时一致）；`code_verifier` 官方标注为 **Required for mobile and desktop app only**。
- 刷新：同一 token 端点，`grant_type=refresh_token` + `refresh_token`。
- 撤销：同一域下 `/v2/oauth/revoke/`，请求体 `client_key`、`client_secret`、`token`（用户 access_token），成功时响应体为空。

### 2.3 state 与 PKCE

- `state`：官方要求生成不可猜测的随机串，在回调时比对以防 CSRF；服务端需负责防请求伪造（[Login Kit Web](https://developers.tiktok.com/doc/login-kit-web/)）。
- 回调会带回 `code`、`scopes`、`state`，以及可能的 `error` / `error_description`，必须优雅处理（同上）。
- PKCE：官方文档明确 **desktop 应用必须使用 PKCE**，`code_challenge = SHA256(code_verifier)` 的 **hex** 编码，`code_challenge_method` 官方只支持 `S256`；`code_verifier` 为 43–128 位、字符集 `[A-Za-z0-9-._~]`，每次授权请求都要重新生成（[Login Kit Desktop](https://developers.tiktok.com/doc/login-kit-desktop/)）。
- **未确认**：Web（服务端）授权流程当前是否也强制 PKCE。Web 文档本次只要求 `state`；应在 Login Kit Web / Desktop 页面复核，或直接在授权请求中始终带上 PKCE（不会更不安全）。

### 2.4 Token 生命周期与刷新

- `access_token` 有效期 24 小时（`expires_in` 示例 86400 秒），`token_type` 为 `Bearer`。
- `refresh_token` 自首次下发起有效期 365 天（`refresh_expires_in` 示例 31536000 秒）。
- 官方明确：access_token 过期后**无需用户再次同意**即可用 refresh_token 刷新，服务端可后台定时刷新以维持长期授权。
- 官方明确：刷新响应返回的 `refresh_token` **可能与传入的不同**；若不同，必须改用新返回的值，否则会失效。
- 响应同时返回 `open_id` 与 `scope`（逗号分隔的已授权 scope 列表），应持久化在服务端。
- `client_secret` 必须保密，不得下发给前端、不得进入开源仓库（[Content Sharing Guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines/)）。

## 3. 发布前置条件

### 3.1 查询创作者信息

- 端点：`POST /v2/post/publish/creator_info/query/`（scope `video.publish`）；每用户 access_token 限 **20 次/分钟**（[Query Creator Info](https://developers.tiktok.com/doc/content-posting-api-reference-query-creator-info/)）。
- 官方要求：渲染 “Export to TikTok” 页面时必须调用该接口，并用其最新返回值展示账号可选的隐私级别与互动设置。
- 返回字段：`creator_avatar_url`（TTL 2 小时）、`creator_username`、`creator_nickname`、`privacy_level_options`、`comment_disabled`、`duet_disabled`、`stitch_disabled`、`max_video_post_duration_sec`。
- `privacy_level_options` 取值与账号类型相关：公开账号返回 `PUBLIC_TO_EVERYONE` / `MUTUAL_FOLLOW_FRIENDS` / `SELF_ONLY`；私密账号返回 `FOLLOWER_OF_CREATOR` / `MUTUAL_FOLLOW_FRIENDS` / `SELF_ONLY`。
- `max_video_post_duration_sec`：该创作者可发布的最长视频秒数，各用户权限不同；官方要求开发者用它拦截超长视频。

### 3.2 隐私与互动必须由用户选择

- `privacy_level` 提交值**必须**属于该次 `creator_info` 返回的 `privacy_level_options`，否则返回 `privacy_level_option_mismatch`（官方注明该错误在商用应用中出现即被视为违反产品使用指引）。
- 官方 UX 要求：隐私状态必须是用户在下拉框中**手动选择**，**不得有默认值**；Duet/Stitch/Comment 也必须是用户手动打开，**默认全部不勾选**（[Content Sharing Guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines/)）。
- 若 `creator_info` 返回某互动被禁用，UX 必须置灰且不可勾选；`creator_info` 返回创作者当前无法再发帖时，必须中止本次发布并提示稍后再试。
- 未审核（unaudited）客户端只能发布 `SELF_ONLY` 可见性；用户要公开需先在 TikTok 内把账号改为公开，再逐条把该内容改为 “Everyone”（同上）。

## 4. 直接发布 vs 上传草稿（inbox）

### 4.1 两条路径对照

| 维度 | Direct Post（直接发布） | Upload（上传到 inbox 草稿） |
| --- | --- | --- |
| 端点（视频） | `POST /v2/post/publish/video/init/` | `POST /v2/post/publish/inbox/video/init/` |
| Scope | `video.publish` | `video.upload` |
| 用户后续动作 | 无需再操作，处理完成后即发布 | 必须在 TikTok App 内点开 inbox 通知，走编辑流程并自行完成发布 |
| 请求体 | `post_info`（标题、隐私、互动开关、封面时间点等）+ `source_info` | 只有 `source_info`；隐私/标题等元数据由用户在 TikTok 内决定 |
| 照片 | 同一端点 `POST /v2/post/publish/content/init/`，`post_mode=DIRECT_POST`、`media_type=PHOTO` | 同一端点，`post_mode=MEDIA_UPLOAD` |
| 状态终态 | `PUBLISH_COMPLETE` 表示已发布 | 先 `SEND_TO_USER_INBOX`，用户发完后才是 `PUBLISH_COMPLETE` |

- 两条路径 `upload_url` 官方均注明**签发后 1 小时内有效**，必须在该窗口内完成上传。
- 视频上传限制：每用户 access_token 限 **6 次/分钟**（两条 init 端点均是）。
- 上传路径额外风控：官方注明同一 24 小时内**最多 5 个待处理（pending）分享**，超出返回 `spam_risk_too_many_pending_share`。
- 官方明确提示：使用上传路径时必须告知用户去点 inbox 通知继续编辑并完成发布。
- 关键请求字段（Direct Post video）：`post_info.privacy_level`（必填）、`post_info.title`（可选）、`post_info.disable_duet` / `disable_stitch` / `disable_comment`、`post_info.video_cover_timestamp_ms`、`post_info.brand_content_toggle`（必填）、`post_info.brand_organic_toggle`、`post_info.is_aigc`；`source_info.source` = `FILE_UPLOAD` 或 `PULL_FROM_URL`，`FILE_UPLOAD` 需 `video_size` / `chunk_size` / `total_chunk_count`，`PULL_FROM_URL` 需 `video_url`。
- 关键响应字段：`data.publish_id`（最长 64 字符，用于查状态）、`data.upload_url`（最长 256 字符，仅 `FILE_UPLOAD` 返回）；错误体含 `error.code` / `message` / `log_id`，除 `ok` 外均视为失败。
- 视频上传请求：`PUT {upload_url}`（必须使用返回的完整 URL，含 query 参数），头部 `Content-Type` 取 `video/mp4` / `video/quicktime` / `video/webm`，`Content-Length` 为本块字节数，`Content-Range: bytes {FIRST_BYTE}-{LAST_BYTE}/{TOTAL_BYTE_LENGTH}`；响应 206 = 还有后续块，201 = 全部上传完成并进入发布流程。

### 4.2 分块规则（官方原文数值）

- `total_chunk_count` = `video_size` / `chunk_size` **向下取整**。
- 每块 **至少 5 MB、至多 64 MB**；**最后一块**可超过 `chunk_size`（最多 128 MB）以容纳尾部字节。
- 总体积 **< 5 MB** 的视频必须整文件上传，此时 `chunk_size` = 整个文件字节数。
- 总体积 **> 64 MB** 必须多块上传；块数最少 1、最多 **1000**；**块必须顺序上传**。
- 官方示例：50,000,123 字节 / 每块 10,000,000 字节 → `total_chunk_count=5`，最后一块 10,000,123 字节（把尾部 123 字节并入以保持 > 5 MB）。
- `upload_url` 过期返回 403，任务不存在返回 404，`Content-Range` 与实际进度不符返回 416，5xx 应重试该块。

### 4.3 状态查询

- 端点：`POST /v2/post/publish/status/fetch/`（scope `video.upload` 或 `video.publish`）；每用户 access_token 限 **30 次/分钟**；请求体 `{"publish_id": ...}`。
- `status` 枚举：`PROCESSING_UPLOAD`（仅 FILE_UPLOAD）、`PROCESSING_DOWNLOAD`（仅 PULL_FROM_URL）、`SEND_TO_USER_INBOX`（上传路径已投递 inbox）、`PUBLISH_COMPLETE`、`FAILED`。
- 其他字段：`fail_reason`、`publicaly_available_post_id`（官方字段名拼写如此；**仅在内容公开且通过审核后**才返回 post_id）、`uploaded_bytes`（FILE_UPLOAD，1-indexed）、`downloaded_bytes`（PULL_FROM_URL，1-indexed）。
- 也可改用 webhook：`post.publish.failed` / `post.publish.complete` / `post.publish.inbox_delivered` / `post.publish.publicly_available` / `post.publish.no_longer_publicaly_available`；回调必须 HTTPS 且立即返回 200，未收到 200 时 TikTok 会以指数退避重试至多 72 小时，且投递为 “at least once”，接收端必须幂等。
- 处理耗时官方给出参考：512 MB < 半分钟、1 GB 约 1 分钟、4 GB > 2 分钟；公开内容需过审核（通常 1 分钟内，少数需数小时），审核完成前不返回 `post_id`。
- `fail_reason` 取值包含 `file_format_check_failed`、`duration_check_failed`、`frame_rate_check_failed`、`picture_size_check_failed`、`internal`（可重试）、`video_pull_failed`、`photo_pull_failed`、`publish_cancelled`、`auth_removed`（不可重试）、`spam_risk_too_many_posts`、`spam_risk_user_banned_from_posting`（不可重试）、`spam_risk_text`、`spam_risk`。

### 4.4 取消

- 官方只在 [Media Transfer Guide](https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide/) 的 “Cancel ongoing pull from URL tasks” 一节记载取消：`POST /v2/post/publish/cancel/`，请求体 `{"publish_id": ...}`，即**对 Direct Post 与 Content Upload 两条路径的下载任务按 best-effort 取消**。
- 官方明确：临近完成或已进入文件处理状态的任务**无法取消**；失败码含 `invalid_publish_id`、`token_not_authorized_for_specified_publish_id`、`publish_not_cancellable`。
- **未确认**：`FILE_UPLOAD`（已传给 TikTok 的二进制的上传任务）是否可取消；取消成功后会收到 `publish_cancelled` 的 fail_reason。

## 5. 素材限制（[Media Transfer Guide](https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide/)）

### 5.1 视频

- 容器格式：MP4（推荐）、WebM、MOV；上传 `Content-Type` 支持 `video/mp4`、`video/quicktime`、`video/webm`。
- 编码：H.264（推荐）、H.265、VP8、VP9。
- 帧率：最低 23 FPS，最高 60 FPS。
- 分辨率：宽高各最小 360 像素、最大 4096 像素。
- 时长：所有创作者可发 3 分钟，部分创作者可发 5 分钟或 10 分钟；开发者经 init 端点最长可送 **10 分钟**，用户可在 TikTok App 内裁剪到本账号真实上限。应按 `creator_info.max_video_post_duration_sec` 做前置拦截。
- 文件大小：最大 **4 GB**。
- 封面：视频用 `post_info.video_cover_timestamp_ms` 指定封面帧（毫秒）；未设置或非法时取视频首帧。不带 cover 图片上传。**未确认**：是否存在独立的视频封面图上传接口或封面图尺寸/格式限制。
- 传输入口：`FILE_UPLOAD` 用返回的 `upload_url` 走 HTTP PUT；`PULL_FROM_URL` 由 TikTok 服务端拉取，要求 URL 为 `https`、不得重定向、必须在归属验证过的域名/URL 前缀下、下载任务 1 小时超时，官方称服务端下载入口带宽可达 100 Mbps。官方建议内容已在自家服务器时用 `PULL_FROM_URL`，文件在用户设备上时才用 `FILE_UPLOAD`。

### 5.2 照片

- 仅当 `media_type=PHOTO`、端点 `POST /v2/post/publish/content/init/`。
- 格式：WebP、JPEG；分辨率最大 1080p；**单张最大 20 MB**。
- 必须用 `PULL_FROM_URL`（`photo_images` 为图片 URL 数组）；`photo_cover_index` 指定封面，索引**从 0 开始**。
- 文案：`title` 最长 90（UTF-16 runes），`description` 最长 4000（UTF-16 runes）。
- 互动：照片帖只支持 “Allow Comment”，Duet/Stitch 不适用；`auto_add_music`（仅 DIRECT_POST）可自动加推荐音乐。

### 5.3 文案与话题

- 视频 `title` 最长 **2200 UTF-16 runes**；`#` 话题与 `@` 提及会被识别，以空格或换行分隔；不传则无文案。
- **未确认**：话题数量上限、提及数量上限、敏感词/外链规则均未见官方在 Content Posting API 文档中给出数值；需在官方文档或审核反馈中核对。
- 官方明确禁止：在内容上叠加品牌名、logo、水印、推广文案或链接；预设文案（含标题字段与话题）必须允许用户发布前编辑（[Content Sharing Guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines/)）。

## 6. 必须保留的用户交互（连接器不得绕过）

1. 用户必须亲自在 TikTok 授权页完成登录与授权同意，连接器只能做跳转与回调处理，不能代填凭证、不能跳过同意页（[Login Kit Web](https://developers.tiktok.com/doc/login-kit-web/)）。
2. 发布页必须展示 `creator_info` 返回的昵称，让用户知道内容将发到哪个账号；必须展示 `privacy_level_options` 并让用户**手动选择隐私级别（无默认值）**。
3. Allow Comment / Duet / Stitch 必须由用户手动打开，默认不勾选；被创作者关闭的项必须置灰。
4. 商业内容披露（Content Disclosure）：默认关闭；开启后 “Your brand” / “Branded content” 至少选一项，未选时发布按钮必须禁用；选择 Branded Content 时可见性不能为 “only me”，需禁用 “only me” 或自动切公开。
5. 发布按钮前必须有声明文案，官方原文要求形如 “By posting, you agree to TikTok's Music Usage Confirmation”；涉及 Branded Content 时须同时引用 Branded Content Policy 与 Music Usage Confirmation。
6. 必须展示待发布内容预览；**只有用户明确同意后**才能开始把素材发送给 TikTok。
7. 必须告知用户发布后可能需要数分钟处理才在自己的主页可见；必须轮询 `publish/status/fetch/` 或接入 webhook 让用户看到状态。
8. 使用上传（草稿）路径时，必须告知用户去点击 TikTok inbox 通知，在其编辑流程中完成发布。
9. 连接器不得替用户做以上任一项；也不得用浏览器自动化、模拟点击等方式在 TikTok 站点/App 上代用户操作。

## 7. 不能声称的能力（诚实边界）

- 不能声称“填一个 API key 就能公开发布”：Direct Post 需应用审核通过并获 `video.publish` scope 批准、用户逐次授权、内容与账号受 TikTok 风控与审核约束。
- 未审核客户端的内容**只能 `SELF_ONLY`**、只能投给私密账号（`unaudited_client_can_only_post_to_private_accounts`），且 24 小时内最多 5 个用户、每创作者每日发帖上限约 15 条（官方注明 “typically around 15 posts per day/creator account”，且所有 Direct Post 客户端共享）。
- 不能声称“全自动无人值守发布”：隐私、互动、商业披露、声明与发布确认都必须由用户完成；上传路径还要求用户回到 TikTok 内完成编辑与发布。
- 不能声称支持浏览器自动化/爬虫式代发：官方 UX 指引要求用户完全知情与可控，且这类做法违反平台规则，本项目同样禁止。
- 不能声称“加字幕/贴片/加水印后发布”：官方禁止在内容上叠加品牌 logo、水印或推广文案。
- 不能声称“发完立刻拿到公开链接”：官方明确公开内容需过审核，审核完成前不返回 `post_id`，仅通过 webhook/状态接口得知最终结果。
- 不能声称“批量刷量/多账号矩阵发布”：官方明示 API 客户端不得为内部/私有工具用途，且存在活跃创作者配额与风控封禁（`spam_risk_*`）。
- 不能声称“素材一定通过”：格式、帧率、分辨率、时长、大小任一不符都会被拒（对应 `*_check_failed` fail_reason）。

## 8. 对本产品的接口映射

连接器对外保持平台无关的接口；TikTok 侧只实现本文件已确认的能力。

| 接口 | 语义与映射 |
| --- | --- |
| `begin_authorization()` | 生成 `state`（可选 PKCE `code_verifier`/`code_challenge`），按 scope 需求拼装 `https://www.tiktok.com/v2/auth/authorize/` 跳转 URL 并返回给前端；不接触用户凭证。 |
| `exchange_callback()` | 校验回调 `state` 与 `error`，把 `code` 交到 `POST /v2/oauth/token/` 换取 `access_token` / `refresh_token` / `open_id` / `scope`，服务端加密持久化；失败时按官方错误语义回报。 |
| `refresh_authorization()` | 用 `grant_type=refresh_token` 调用同一 token 端点续期；**必须接收并保存新返回的 `refresh_token`**；refresh 过期（365 天）后要求用户重新走授权。 |
| `get_account_capabilities(account_ref)` | 调 `POST /v2/post/publish/creator_info/query/`，返回该账号可选的 `privacy_level_options`、`max_video_post_duration_sec`、`comment_disabled` / `duet_disabled` / `stitch_disabled`，并把“未审核客户端只能 SELF_ONLY”作为能力上限一并输出，供 UI 渲染。 |
| `validate_package(package, account, publish_options)` | 本地预检：容器/编码/帧率/分辨率/时长（对照 `max_video_post_duration_sec`）/文件大小与分块可行性、封面时间点、文案长度（视频 2200；照片 title 90 / description 4000）、隐私值是否属于该账号的 `privacy_level_options`；不通过则拒绝提交，不发任何 TikTok 请求。 |
| `submit_publish(package, options, execution_key)` | 按选项选路径：Direct Post 用 `/v2/post/publish/video/init/`（照片用 `/v2/post/publish/content/init/`，`post_mode=DIRECT_POST`），草稿用 `/v2/post/publish/inbox/video/init/`（照片 `post_mode=MEDIA_UPLOAD`）；`FILE_UPLOAD` 需在 1 小时内按 4.2 的分块规则顺序 PUT 到 `upload_url`；`execution_key` 用于本地幂等去重，官方侧无幂等键，重复提交会产生新 `publish_id`。返回操作句柄（内部 id ↔ `publish_id`）。 |
| `query_publish(operation_handle)` | 调 `POST /v2/post/publish/status/fetch/` 读 `status` / `fail_reason` / `uploaded_bytes` / `downloaded_bytes` / `publicaly_available_post_id`，并与 webhook 事件合并（webhook 至少一次投递，需本地幂等）；官方字段名拼写以文档为准。 |
| `cancel_publish(operation_handle)` | **能力受限**：仅 `PULL_FROM_URL` 下载任务可按 best-effort 调 `POST /v2/post/publish/cancel/`；已接近完成或已进入处理阶段不可取消；`FILE_UPLOAD` 场景是否可取消**未确认**。因此对外必须声明“取消是尽力而为、可能失败”，并对最终态返回不可取消错误。 |

## 9. 来源清单（本次实际抓取的官方页面）

| 来源 | 确认内容 |
| --- | --- |
| [Get Started - Direct Post](https://developers.tiktok.com/doc/content-posting-api-get-started/) | Direct Post 全流程、需批准 `video.publish`、未审核客户端仅私密可见、`creator_info` 与 `video/init` / `content/init` / `status/fetch` 示例、需审核以解除限制。 |
| [Direct Post API Reference](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post/) | `/v2/post/publish/video/init/` 的 scope、6 次/分钟、`post_info`/`source_info` 全字段与必填性、`publish_id`(≤64)/`upload_url`(≤256)、`upload_url` 1 小时有效、错误码（含 `unaudited_client_can_only_post_to_private_accounts`、`privacy_level_option_mismatch`）、PUT 上传头。 |
| [Upload API Reference](https://developers.tiktok.com/doc/content-posting-api-reference-upload-video/) | `/v2/post/publish/inbox/video/init/` 的 `video.upload` scope、只传 `source_info`、inbox 通知提示、24 小时内最多 5 个 pending 分享。 |
| [Query Creator Info](https://developers.tiktok.com/doc/content-posting-api-reference-query-creator-info/) | 端点、20 次/分钟、返回字段与公开/私密账号的 `privacy_level_options` 差异、头像 TTL 2 小时、错误码。 |
| [Get Post Status](https://developers.tiktok.com/doc/content-posting-api-reference-get-video-status/) | `/v2/post/publish/status/fetch/`、30 次/分钟、`status`/`fail_reason` 全枚举、`publicaly_available_post_id` 语义、webhook 事件名、处理耗时参考。 |
| [Photo API Reference](https://developers.tiktok.com/doc/content-posting-api-reference-photo/) | `/v2/post/publish/content/init/`、`media_type=PHOTO`、`post_mode` 两值、照片 title 90 / description 4000、`photo_cover_index` 从 0 开始、`auto_add_music`。 |
| [Media Transfer Guide](https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide/) | 分块规则（5–64 MB、尾块 ≤128 MB、≤1000 块、顺序上传、`total_chunk_count` 向下取整）、HTTP schema 与 2xx 语义、PULL_FROM_URL 归属与 1 小时超时、`/v2/post/publish/cancel/` 与 best-effort 限制、视频/图片限制（格式、编码、23–60 FPS、360–4096 px、10 分钟、4 GB、WebP/JPEG、1080p、20 MB）。 |
| [User Access Token Management](https://developers.tiktok.com/doc/oauth-user-access-token-management/) | token/revoke 端点与请求体、access_token 24 小时、refresh_token 365 天、刷新无需用户同意、新 refresh_token 必须替换、`code_verifier` 仅移动/桌面必填。 |
| [Login Kit Web](https://developers.tiktok.com/doc/login-kit-web/) | 授权页 URL 与查询参数、`state` 防 CSRF、回调参数、redirect URI 注册限制、client secret 服务端保管。 |
| [Login Kit Desktop](https://developers.tiktok.com/doc/login-kit-desktop/) | desktop 强制 PKCE：hex SHA256、仅 `S256`、verifier 43–128 字符、每次授权重新生成。 |
| [Login Kit Overview](https://developers.tiktok.com/doc/login-kit-overview/) | Web/Desktop 授权流程对照（web 要求 `state`；desktop 额外要求 PKCE 与 loopback 重定向）。 |
| [Content Sharing Guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines/) | Direct Post 开发者与 UX 强制要求：5 用户/24h 未审核上限、SELF_ONLY、每创作者约 15 帖/日、隐私无默认值、互动默认关闭、商业披露与声明文案、预览/同意后才可上传、水印禁令、client_secret 保密。 |
| [Scopes Reference](https://developers.tiktok.com/doc/tiktok-api-scopes/) | `video.publish`（Direct Post）与 `video.upload`（草稿上传）定义及其对应端点。 |
| [App Review Guidelines](https://developers.tiktok.com/doc/app-review-guidelines/) | scope 需审核并演示、不得申请不需要的 scope、Web 应用需提供有效 redirect URI、沙盒演示与 demo 视频要求。 |
| [Register Your App](https://developers.tiktok.com/doc/getting-started-create-an-app/) | 注册应用、Production/Sandbox 模式、Content Posting API 的 URL properties 归属验证入口。 |
| [Webhooks Overview](https://developers.tiktok.com/doc/webhooks-overview/) | webhook 回调必须 HTTPS 并立即返回 200、重试 72 小时、at-least-once 需幂等。 |
| [Developer Guidelines](https://developers.tiktok.com/doc/our-guidelines-developer-guidelines/) | 不得移除创作者版权标识等平台规则；违规可被拒审。 |

说明：`ux-guidelines.txt` / `video-restrictions.txt` 两个中间文件与 Content Sharing Guidelines 是同一官方页面（路径重定向），故不重复列出。

## 10. 未知项（官方文档中本次未能确认，不得臆造）

1. **Web 端是否需要 PKCE**：官方只写明 desktop 强制；Web 是否强制或支持 `code_challenge` 未确认 → 核验 [Login Kit Web](https://developers.tiktok.com/doc/login-kit-web/)。
2. **`/v2/oauth/revoke/` 是否同时吊销 refresh_token**：官方只说撤销后用户不再在 Manage app permissions 看到应用 → 核验 token 管理页。
3. **`FILE_UPLOAD` 已上传任务能否取消**：官方取消章节只覆盖 pull-from-URL → 核验 Media Transfer Guide / cancel 端点。
4. **视频封面图可否单独上传**及封面图格式/尺寸限制：官方只提供 `video_cover_timestamp_ms` → 核验 Direct Post 参考页。
5. **分块上传的重试/续传规则**：官方只给出 5xx 可重试与 416 语义，未说明已上传字节如何续传（`uploaded_bytes` 可读）→ 核验 Media Transfer Guide。
6. **话题/提及数量上限、文案敏感词与链接规则**：仅确认 2200 / 90 / 4000 字符与 `#`、`@` 解析规则 → 核验官方文档与审核反馈。
7. **`access_token` 可用 scope 组合**：未确认同一用户能否同时授予 `video.publish` 与 `video.upload`、以及多产品共用一套 token 的限制 → 核验 Scopes / OAuth 文档。
8. **定时发布（scheduling）能力**：本次未在任何官方页面看到 Content Posting API 的定时发布参数 → 视为不支持，除非官方文档更新。
9. **`reached_active_user_cap` 的具体配额数值**：官方只说依据审核申请表中的用量估算设定，未给数值 → 核验应用审核后台/官方文档。
10. **各端点 rate limit 是否有全局/应用级上限**：本次只确认按用户 access_token 的 6 / 20 / 30 次每分钟 → 核验官方 Rate Limits 页面（本次未抓取到有效 URL）。
11. **`share` 类音频/音乐授权**：本次只确认声明文案要求引用 Music Usage Confirmation，未确认音频版权接口 → 核验官方政策页。
12. **应用所属地区/账号类型是否影响 Direct Post 资格**：未确认 → 核验应用审核指引与后台提示。
