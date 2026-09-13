# Facebook Page 视频发布（Graph API）Connector 集成说明

> 本文件只描述 **Facebook Page 视频发布**（Page video posts）这一条链路。所有端点、参数、枚举值与限制均来自 Meta 官方开发者文档；官方未写明的条目一律标注 **未确认**，不做推测。

## 1. 官方资料与核验时间

- **本文核验日期：2026-09-13（today）**，全部内容于该日直接从 `developers.facebook.com` 抓取。
- 抓取到的官方文档页面声明的最近更新时间为 **2024-06-21**（Video API Publishing guide）、**2024-06-04**（Video API Overview）、**2020-06-23**（Video API reference）、**2024-07-19**（Get Videos）、**2026-07-30**（Reels Publishing API）——**均早于本次核验日**，说明这些页面长期未更新，但线上行为可能已变更。
- 官方文档引用的 Graph API 版本存在不一致：HTML 页面渲染为 **v26.0**（页面内以"latest API version"动态注入），而同页的 Markdown 导出（`View as Markdown`）中硬编码为 **v25.0**。
- **结论（强制要求）：开始 connector 开发前、以及每次上线前，必须重新核验本文所有数值与端点；不要直接照抄版本号，应从官方文档动态获取当前版本。**

---

## 2. 账号前提与授权

### 2.1 账号与角色任务前提

- 必须存在一个 **Facebook Page**，视频发布到 Page，而非个人时间线。
- 申请 token 的**自然人必须能在该 Page 上执行 `CREATE_CONTENT` 任务**（官方 Publishing guide 的硬性前提）。
- Page 上的任务（Tasks）由官方定义为：`ADVERTISE` / `ANALYZE` / `CREATE_CONTENT` / `MANAGE` / `MANAGE_LEADS` / `MESSAGING` / `MODERATE` / `VIEW_MONETIZATION_INSIGHTS`；其中 `CREATE_CONTENT` 的含义是"以 Page 身份在 Page 上发布内容"。Page 管理员（Admin）可执行全部任务。

### 2.2 必需权限（Permissions）

Video API Publishing guide 明确列出发布 Page 视频所需的三项权限：

| 权限 | 官方定义（摘要） |
| --- | --- |
| `pages_show_list` | 访问某人管理的 Page 列表；用于向用户展示其可管理的 Page 并校验管理关系 |
| `pages_read_engagement` | 读取 Page 发布的内容（posts/photos/videos/events）、粉丝数据、Page 元数据与 insights |
| `pages_manage_posts` | 创建、编辑、删除 Page 帖子（即以 Page 身份发布内容） |

关于 `publish_video`：该权限**确实存在且已被官方文档记录**，但其官方定义是"向 app 用户的时间线、group、event 或 Page 发布 **live video（直播）**"。Pages API 的 Posts 指南在"发布视频到 Page"的权限清单中列入了 `publish_video`。因此：

- `publish_video` 的定位是**直播**相关权限；
- 普通（非直播）Page 视频上传是否需要 `publish_video` 作为附加权限，官方 Video API Publishing guide 的权限清单中**未包含**它 → 标记为 **未确认**，实现时以实际 API 报错和最新文档为准。

### 2.3 从 user token 获取 Page access token

官方 Access Tokens 文档给出的流程：

1. 通过 Facebook Login（Pages API 推荐 **Facebook Login for Business**）取得 **User access token**，并在授权对话框中申请上述权限。
2. 用该 user token 调用 Graph API 换取 **Page access token**：
   - `GET https://graph.facebook.com/{your-user-id}/accounts?access_token={user-access-token}`
   - 官方 Pages API Overview 表述为查询 `/me/accounts`。
3. 响应中列出该用户拥有角色的 Page，包含 Page 分类、**用户在该 Page 上的 tasks**、以及**每个 Page 的 `access_token`**；同时捕获 Page ID。
4. 后续所有 Page 视频发布调用使用 **Page access token**。
- 官方明确：**Page access token 对每个 Page、每个 admin、每个 app 的组合都是唯一的**；且 Pages API 概述指出 Page token **具有过期时间**。

### 2.4 Token 生命周期与刷新

- 官方仅分类说明：token 分为 **short-lived** 与 **long-lived** 两种；**short-lived 通常约 1–2 小时**，**long-lived 通常约 60 天**；并明确要求"**不要依赖这些时长保持不变——可能随时变化或提前失效**"。
- Web 登录取得的 token 是 short-lived，可在**服务端**用 app secret 换为 long-lived。
- **Page access token 自身的确切有效期、以及 user token 失效后 Page token 的行为，官方该页面未给出明确数值 → 未确认。** 设计上必须假定 Page token 会失效：捕获 OAuth 错误（官方 Page Videos 参考中列出错误码 `190 Invalid OAuth 2.0 Access Token`）后引导用户重新授权。

### 2.5 App Review 要求

- 官方明确：**所有 Page 相关 Permission 与 Feature 在 app 上线（live）后都必须通过 App Review 获得批准**，app 用户才能向你的 app 授予这些权限。
- 对 **Business apps**（无 app modes）：权限必须获批 **Advanced access** 后，才能被"对 app 或已认领该 app 的 Business 没有角色的 app 用户"授予。
- 处于 **Development Mode** 的 app 可以向任何在 app 上拥有 Role 的用户申请任意权限（即自测阶段无需 App Review）。
- 官方同时提示：只申请功能真正需要的权限，**申请多余权限是 App Review 被拒的常见原因**。

---

## 3. 发布流程

### 3.1 当前官方推荐流程（Publish with the Video API）

官方当前文档把"上传"与"发布"分成两步，并使用 **Resumable Upload API** 产生的 file handle：

**Step 1 — 创建上传会话**

- `POST https://graph.facebook.com/{API_VERSION}/{APP_ID}/uploads`
- 必填参数：`file_name`（文件名）、`file_length`（**字节**为单位的文件大小）、`file_type`（MIME 类型，官方列出的合法值：`application/pdf`、`image/jpeg`、`image/jpg`、`image/png`、`video/mp4`）、`access_token`（此处为 **User access token**）。
- 返回：`{"id": "upload:<UPLOAD_SESSION_ID>"}`。

**Step 2 — 上传字节流**

- `POST https://graph.facebook.com/{API_VERSION}/upload:<UPLOAD_SESSION_ID>`
- 头部：`Authorization: OAuth <USER_ACCESS_TOKEN>`、`file_offset: 0`，body 为二进制文件数据（`--data-binary`）。
- 官方强调：**token 必须放在 header 中，否则调用会失败**。
- 返回 file handle：`{"h": "<UPLOADED_FILE_HANDLE>"}`（示例 `"2:c2FtcGxl..."`）。

**中断续传**

- `GET https://graph.facebook.com/{API_VERSION}/upload:<UPLOAD_SESSION_ID>`（同样用 `Authorization: OAuth` 头）返回 `file_offset`。
- 用该 `file_offset` 重新 `POST` 到同一 `/upload:<UPLOAD_SESSION_ID>`，即从断点续传。

**Step 3 — 发布到 Page**

- `POST https://graph-video.facebook.com/{API_VERSION}/{PAGE_ID}/videos`（官方 host 为 **graph-video.facebook.com**，注意与 `graph.facebook.com` 区分）
- 表单参数：`access_token`（**Page access token**）、`title`、`description`、`fbuploader_video_file_chunk=<UPLOADED_FILE_HANDLE>`。
- 成功返回：`{"id":"<VIDEO_ID>"}`。

### 3.2 分片（chunked）上传变体 —— Page Videos 参考中的 `upload_phase`

`POST /{page_id}/videos` 的官方参考文档中**仍然列出**以下参数，构成"老版/分片"流程（**注意：官方当前没有任何一篇 guide 逐步描述这个分片流程，只有在 reference 中列出的参数与返回结构 → 完整逐步流程标记为 未确认**）：

| 参数 | 官方描述 |
| --- | --- |
| `upload_phase` | 分片上传请求类型，枚举：`start`、`transfer`、`finish`、`cancel` |
| `upload_session_id` | 分片上传会话 ID |
| `start_offset` | **文件分片的起始字节位置** |
| `end_offset` | `end_offset`（官方仅给出参数名，未在该页给出文字描述） |
| `video_file_chunk` | 分片文件内容（form data）；**官方明确要求该字段在 `transfer` 阶段必填** |
| `file_size` | 整个视频文件的字节大小 |
| `file_url` | 可访问的视频文件 URL；**官方明确：不能与 `upload_phase` 同时使用** |

同一参考文档给出的**返回结构**包含：`id`、`upload_session_id`、`video_id`、`start_offset`、`end_offset`、`success`、`skip_upload`、`upload_domain`、`region_hint`、`xpv_asset_id`、`is_xpv_single_prod`、`transcode_bit_rate_bps`、`transcode_dimension`、`should_expand_to_transcode_dimension`、`action_id`、`gop_size_seconds`、`target_video_codec`、`target_hdr`、`maximum_frame_rate`。

**更简单的非分片变体**：官方参考将 `source` 描述为"以 form data 编码的视频，**该字段为必需**"，即直接把整文件作为 `source` 上传到 `POST /{page_id}/videos`。但官方**未说明** `source` 与 `upload_phase`/file handle 三条路径的取舍条件与各自大小上限 → 选择策略 **未确认**。

### 3.3 官方文档记载的重要发布参数

`POST /{page_id}/videos` 参考中与发布行为直接相关的参数（均为官方原文摘要）：

| 参数 | 官方说明 |
| --- | --- |
| `title` | 视频标题（UTF-8 字符串） |
| `description` | 描述性文本，可能出现于相关 story 中，支持 emoji 与富文本信息 |
| `published` | **默认值 `true`**；是否发布该视频的帖子；**未发布的视频不能 backdate** |
| `scheduled_publish_time` | 定时发布时间，官方原文：**"应在发布视频时间之后的 10 分钟到 6 个月之间"** |
| `thumb` | 要上传并关联到视频的缩略图原始数据（image） |
| `content_category` | 内容分类枚举：`BEAUTY_FASHION, BUSINESS, CARS_TRUCKS, COMEDY, CUTE_ANIMALS, ENTERTAINMENT, FAMILY, FOOD_HEALTH, HOME, LIFESTYLE, MUSIC, NEWS, POLITICS, SCIENCE, SPORTS, TECHNOLOGY, VIDEO_GAMING, OTHER` |
| `targeting` | **限制**受众范围的对象；不在这些人群内的人**无法查看**该内容；不会覆盖 Page 级别的年龄/地区限制 |
| `feed_targeting` | **影响**信息流投放倾向的对象；不在其中的人"更不容易看到，但仍可能看到" |
| `file_size` / `file_url` | 整文件字节大小 / 可访问的视频 URL（与 `upload_phase` 互斥） |
| `backdated_time` + `backdated_post` | 回填发布时间；参考中将 `backdated_time` 标为 Required |
| `no_story` | 设为 `true` 时不再产生信息流/时间线 story |
| `unpublished_content_type` | 未发布内容类型枚举：`SCHEDULED, SCHEDULED_RECURRING, DRAFT, PUBLISH_PENDING, ADS_POST, INLINE_CREATED, PUBLISHED, REVIEWABLE_BRANDED_CONTENT` |
| `crossposted_video_id` | 新视频帖将复用的 video id（跨 Page 复用） |

> 说明：`targeting` 与 `feed_targeting` 的效果强度不同（限制 vs 倾向），实现 `publish_options` 时应区分对待，不要混用。

---

## 4. 处理与结果

- **发布成功返回 video id**：`{"id":"<VIDEO_ID>"}`（发布步骤的官方返回）。
- **读取处理状态**：官方在 Reels Publishing guide 中记录了 Video 节点的 `status` 字段读取方式，**该字段即 Video node 上的 `status`**：
  - `GET https://graph.facebook.com/{API_VERSION}/{video-id}?fields=status&access_token=<PAGE_ACCESS_TOKEN>`
- `status` 对象包含四个部分（官方逐项说明）：
  - `video_status`：整体状态。官方枚举：`error`（处理或发布阶段出错）、`expired`（视频过期，需重新上传）、`processing`（Meta 正在处理）、`ready`（可发布）、`uploading`（正在上传）、`upload_failed`（上传失败，需重试）、`upload_complete`（上传完成，已传字节应等于文件大小）。
  - `uploading_phase`：含 `bytes_transfered`（**已上传字节数，可作为 `offset` 用于续传**）、`errors`、`status`（`completed`/`error`/`not_started`/`in_progress`）、`source_file_size`。
  - `processing_phase`：含 `error`（含失败原因，如"分辨率过低"）与 `status`（`completed`/`error`/`not_started`/`in_progress`）。
  - `publishing_phase`：含 `error`、`status`、`publish_status`（`draft`/`error`/`published`/`scheduled`）、`publish_time`（实际或计划发布的 UNIX 时间戳）。
- **上传后视频可能仍在处理**：官方示例中即使上传完成，`video_status` 仍可能为 `processing`，且 `processing_phase.status` 可能为 `not_started`；因此**不能把"上传成功/发布返回 id"当作"可播放/已上线"**，必须轮询 `status` 直到 `video_status` 为 `ready`（或 `publishing_phase.publish_status` 为 `published`/`scheduled`）。
- **如何获取 permalink**：
  - 官方 Pages API Posts 指南记载：Page 帖子（post）的 permalink 形式为 **`https://www.facebook.com/{page_post_id}`**。
  - `permalink_url` 字段本身：**在当前可访问到的官方 Video / Page Videos 参考页面中未出现 → 未确认**。实现时应通过 `GET /{video-id}?fields=...` 探测可读字段，而不是硬编码字段名。
  - permalink **何时不可用**：官方参考中提到 `reference_only=true` 的视频"不会出现在 Facebook 任何位置，且不能通过 permalink 查看或分享"；`secret=true` 的视频不公开、不可搜索，但**可以**通过 permalink 查看与嵌入；`published=false` 表示不生成帖子。因此当 `published=false`、`reference_only=true`、或视频仍在处理（`video_status` 非 `ready`）时，不应期望 permalink 可用，具体行为 **未确认**。
- **删除/更新**：官方 Video 参考列出 `DELETE /{video-id}`（删除已发布视频）与 `POST /{video-id}`（更新已发布视频的字段）。

---

## 5. 素材限制

以下为官方 **Video API reference** 明文列出的规格（适用于 Page/Group/User 视频端点）：

- **Aspect Ratio（宽高比）**：`9x16`、`16x9`。
- **Formats（容器/扩展名）**：`3g2, 3gp, 3gpp, asf, avi, dat, divx, dv, f4v, flv, gif, m2ts, m4v, mkv, mod, mov, mp4, mpe, mpeg, mpeg4, mpg, mts, nsv, ogm, ogv, qt, tod, ts, vob, wmv`。
- **Audio Settings**：Sample Rate **48 kHz**；Channel Layout **Stereo 或 Mono**；Codec **AAC**；Bit Rate **最高 256 kbps**。
- **Resumable Upload API 支持的文件类型**：`pdf`、`jpeg`、`jpg`、`png`、`mp4`（即 file handle 路径官方仅列出 `mp4` 作为视频格式）。

**未确认（官方文档中未找到，禁止编造）**：Page 普通视频的**最大文件大小**、**时长限制**、**推荐/最低分辨率**、**视频编码（H.264/H.265 等）与帧率要求**、**caption/description 字符长度上限**。唯一可确认的分辨率/时长数值来自 **Reels** 章节（9x16、1080x1920 推荐/最低 540x960、24–60 fps、3–90 秒），**不适用于普通 Page 视频**。

**素材托管要求（与 Instagram 不同）**：

- 本文所述 Page 视频链路**不要求视频先存在于公网 URL**：既可用 **Resumable Upload API 上传本地文件取得 file handle**，也可用 `source` 以 form data 直接上传，或用 `file_url` 提供可访问 URL。
- 唯一与 URL 相关的官方约束：`file_url` **不能与 `upload_phase` 同时使用**；官方 Page Videos 参考的 `6001/6000/382/389/368` 等错误码中，**`389` 为"Unable to fetch video file from URL"**。
- 因此本 connector **不需要** Instagram 式的"必须先有公网可拉取的视频 URL"前提。

---

## 6. 范围边界

- 本产品范围**严格限定为 Facebook Page 视频发布（Page video posts）**：授权、上传、发布、状态查询、取消/删除。
- **明确排除**：Ads 广告活动创建、广告投放、广告预算/花费（ad spend）、广告账户与 Campaign/AdSet/Ad 管理。计划已明确将 Ads 排除在外。
- 由此推论（实现约束）：
  - 不申请广告类权限（例如官方权限参考中用于"创建 campaign、管理广告、拉取指标"的 `ads_management`）。
  - 不使用 `/{page_id}/videos` 参考中"为广告创建未发布视频"的路径（该路径官方要求 `ADVERTISE` 任务与 `pages_manage_ads`、`pages_show_list` 权限）——**本产品不涉及**。
  - `ad_breaks`、`sponsor_id`、`direct_share_status`、`content_tags`（广告兴趣定向）等参数**不纳入**本 connector 能力范围。

---

## 7. 必须保留的用户交互（不得用自动化绕过）

1. **Page 角色/权限授权必须由真人完成**：用户需在 Facebook Login 对话框中亲自授权权限，且该用户必须**本人在目标 Page 上拥有 `CREATE_CONTENT` 任务**。产品不得代持、不得共享凭据、不得代替用户跳过授权。
2. **平台对已发布视频的审核/处理必须保留**：视频上传后由 Meta 侧转码、处理与合规审核，`video_status` 可能为 `processing`/`error`/`expired`。产品**不得**通过重试风暴、伪造元数据或任何手段规避平台处理与审核结论。
3. **Sharing Disclosure 与受众选择**（官方要求，Reels 章节明文）：上传时"应向 app 用户展示其内容将如何被 Facebook 使用的说明与选择项"；app 用户应能选择受众。官方同时指出"**发布到 Page 隐含公开范围，只应提供 `Public` 选项**"。
4. **定时发布需用户确认**：使用 `published=false` + `scheduled_publish_time` 时，把官方区间（10 分钟 ~ 6 个月）如实呈现给用户，不静默改写用户设定的时间。
5. **显式声明：禁止使用浏览器自动化（browser automation / RPA / 无头浏览器）绕过平台要求**——包括但不限于：模拟点击以跳过登录或授权、绕过 App Review 或权限检查、绕过发布频率限制、绕过平台审核与目标受众限制。所有发布必须经由官方 Graph API 使用用户合法授权的 token 完成。
   - 关于自动化交互的合规性，请以 Meta 官方 **Platform Terms** 与 Developer Policies 原文为准并逐条核对（本次未逐条核验其条款文本）。

---

## 8. 对本产品的接口映射

| 产品接口 | 映射到官方能力 | 说明 |
| --- | --- | --- |
| `begin_authorization()` | Facebook Login / **Facebook Login for Business** 授权对话框 | 申请 `pages_show_list`、`pages_read_engagement`、`pages_manage_posts`；由真人登录并授权；返回授权 URL / state，不接触用户密码 |
| `exchange_callback()` | 回调 code → **User access token**；再 `GET /{user-id}/accounts`（或 `/me/accounts`）→ **Page access token** | 落库：Page ID、Page token、用户在该 Page 的 tasks（校验含 `CREATE_CONTENT`）、权限授予结果；**不记录**任何凭据到文档或日志 |
| `refresh_authorization()` | 官方 long-lived token 换取（服务端 + app secret）；token 失效后重新走 Facebook Login | 官方明示 token 时长可能变化/提前失效；捕获错误码 `190` 触发重授权。**Page token 精确有效期未确认**，需以运行时错误为准检测失效 |
| `get_account_capabilities(account_ref)` | `GET /{user-id}/accounts` / `/me/accounts` 的 `tasks` 字段；`GET /{page-id}/videos` 验证读权限（`pages_read_engagement`） | 返回：是否可发布（`CREATE_CONTENT`）、可发布到哪些 Page、权限是否齐备、token 是否有效。**不涉及**广告账户能力（见 §6） |
| `validate_package(package, account, publish_options)` | 本地校验 + 官方规格：容器格式（Video API reference 列表）、Audio（48 kHz / AAC / Stereo-Mono / ≤256 kbps）、`video/mp4` 供 file handle 路径、`title`/`description` 类型、`published`、`scheduled_publish_time` 的 **10 分钟 ~ 6 个月**区间、`content_category` 枚举、`targeting`/`feed_targeting` 结构 | **最大文件大小、时长、分辨率、caption 长度官方未确认** → 这些校验项必须标记为"按运行时/实测结果确定"，不得写死数值 |
| `submit_publish(package, options, execution_key)` | ① Resumable Upload：`POST /{app-id}/uploads` → `POST /upload:{session}` → 得 `h`；② 发布：`POST https://graph-video.facebook.com/{ver}/{page-id}/videos`，带 `fbuploader_video_file_chunk=<handle>`（或分片路径 `upload_phase=start/transfer/finish` + `upload_session_id` + `start_offset`/`end_offset`/`video_file_chunk`；或 `source`；或 `file_url`） | `execution_key` 用于幂等：**必须持久化 `upload_session_id` 与最终 `video_id`**，避免重复上传/重复发帖。返回 `operation_handle` 封装：`upload_session_id`、`video_id`、stage、以及最后一次已知的 `start_offset`/`end_offset`/`file_offset` |
| `query_publish(operation_handle)` | `GET /{video-id}?fields=status`；跨层读取 `video_status`、`uploading_phase`（含 `bytes_transfered`）、`processing_phase`、`publishing_phase`（含 `publish_status`、`publish_time`）；permalink 见 §4 | 状态机建议：`uploading` → `upload_complete` → `processing` → `ready`；异常分支 `upload_failed` / `error` / `expired`。中断续传用 `GET /upload:{session}` 的 `file_offset` 或 `uploading_phase.bytes_transfered` |
| `cancel_publish(operation_handle)` | **两种情况，语义完全不同，必须在 UI 与 API 中区分：** | ① **中止在途上传**：官方 Page Videos 参考中 `upload_phase` 的枚举**包含 `cancel`** → 中断分片上传会话，**不会**产生任何 Page 帖子。② **删除已发布的帖子**：`DELETE /{video-id}`（官方 Video 参考列出）→ **这是删除已上线内容，属于不可逆的对外可见操作，必须二次确认**。若视频已发布而仅想撤销上传，官方**未提供**"撤回已发布视频但保留草稿"的语义 → **未确认**；实现须明确告知用户"取消 = 删除帖子"或"取消 = 停止上传" |

---

## 9. 来源清单（本次实际抓取的官方页面）

1. https://developers.facebook.com/docs/video-api/guides/publishing/ — Publish with the Video API：发布 Page 视频的权限前提、`graph-video.facebook.com/{PAGE_ID}/videos` 调用、Resumable Upload 三步与续传（更新于 2024-06-21）。
2. https://developers.facebook.com/docs/video-api/ — Video API 总览：可发布 Videos 与 Reels 到 Page，并指向交叉发布与视频广告（更新于 2024-06-04）。
3. https://developers.facebook.com/docs/video-api/reference — Video API reference：宽高比、支持的格式列表、音频规格（48 kHz / AAC / ≤256 kbps）、Page/Group/User/video 端点清单（更新于 2020-06-23）。
4. https://developers.facebook.com/docs/graph-api/reference/page/videos — Page Videos 参考：创建要求（`CREATE_CONTENT` + 三项权限；广告路径为 `ADVERTISE` + `pages_manage_ads`）、全部参数（`upload_phase`、`upload_session_id`、`start_offset`、`end_offset`、`video_file_chunk`、`source`、`file_url`、`published`、`scheduled_publish_time`、`thumb`、`content_category`、`targeting`、`feed_targeting` 等）、返回结构与错误码。
5. https://developers.facebook.com/documentation/graph-api/reference/page/videos.md — 上述页面的官方 Markdown 导出（用于交叉核对参数与返回结构原文）。
6. https://developers.facebook.com/docs/graph-api/reference/video — Video 节点参考：读字段、`POST /{video-id}` 更新、`DELETE /{video-id}` 删除；并注明该页部分内容对应已被移除的 v3.2 之前特性。
7. https://developers.facebook.com/docs/graph-api/guides/upload — Resumable Upload API：`POST /{APP_ID}/uploads`、`POST /upload:{session}`、`file_offset` 续传、`{"h": ...}` file handle、支持 `mp4` 等类型。
8. https://developers.facebook.com/docs/video-api/guides/reels-publishing/ — Reels Publishing API：`status` 字段的完整枚举（`video_status`、`uploading_phase`、`processing_phase`、`publishing_phase`）、续传方式、Reels 的规格与 30 条/24 小时限流、Sharing Disclosure 与 Page 受众仅 `Public`（更新于 2026-07-30）。
9. https://developers.facebook.com/docs/video-api/guides/get-videos — Get Videos：`GET /{PAGE_ID}/videos` 与所需 token/权限（`MANAGE` 任务 + `pages_read_engagement`；公开 Page 用 Page Public Content Access）（更新于 2024-07-19）。
10. https://developers.facebook.com/docs/pages/access-tokens — Access Tokens：Page token 定义与获取方式（先 user token 再换取）、`/{your-user-id}/accounts` 返回结构与 `tasks`、short-lived/long-lived（约 1–2 小时 / 约 60 天）与"时长可能变化"的官方警示、token 用可变长度类型存储。
11. https://developers.facebook.com/docs/pages/overview — Pages API Overview：Page token 唯一性与过期、获取 token 的 `/me/accounts` 流程、Tasks 定义（`CREATE_CONTENT` 等）、权限与 Feature 均需 App Review、Business app 需 Advanced access。
12. https://developers.facebook.com/docs/pages-api/posts — Pages API Posts：发布 Page 帖子的权限清单（含"若发布视频则需 `publish_video`"）、Page 帖子 permalink 形式 `https://www.facebook.com/{page_post_id}`、定时帖需 `published=false` + `scheduled_publish_time`。
13. https://developers.facebook.com/docs/permissions/reference/pages_manage_posts — 权限参考：`pages_manage_posts` / `pages_read_engagement` / `pages_show_list` / `publish_video` 的官方定义与 App Review 说明。
14. https://developers.facebook.com/docs/permissions/reference/pages_read_engagement — `pages_read_engagement` 官方定义。
15. https://developers.facebook.com/docs/permissions/reference/pages_show_list — `pages_show_list` 官方定义。
16. https://developers.facebook.com/docs/permissions/reference/publish_video — `publish_video` 官方定义（明确为 **live video** 发布权限）。

---

## 10. 未确认事项（官方文档中未能确认，禁止臆造）

1. **`processing_progress`**：在本次可访问的官方文档中**未找到**该字段；官方文档化的进度表达是 `status.processing_phase.status`。字段是否存在 → **未确认**。
2. **`permalink_url`**：作为 Video 节点字段**未在官方文档中确认**；官方只记载了 Page 帖子 permalink 形式 `https://www.facebook.com/{page_post_id}`。可用性与取值时机 → **未确认**。
3. **普通（非 Reels）Page 视频的最大文件大小 / 时长限制 / 推荐或最低分辨率 / 帧率 / 视频编码要求**：官方仅给出宽高比与音频参数 → **均未确认**（Reels 的 1080x1920、3–90 秒等数值不可外推）。
4. **`title` / `description` / caption 的字符长度上限**：官方仅说明类型为 UTF-8 字符串，无长度限制说明 → **未确认**。
5. **`publish_video` 是否是非直播 Page 视频上传的必需权限**：Publishing guide 的三项权限清单中未含它，而 Pages API Posts 指南把它列为"发布视频到 Page"的权限 → 存在文档冲突，**未确认**；需以实际 API 报错与最新文档复核。
6. **Page access token 的确切有效期**，以及 **user token 失效后 Page token 是否随之失效**：官方只给出 short-lived/long-lived 的一般说明与"时长可能变化"的警告 → **未确认**。
7. **分片流程（`upload_phase=start/transfer/finish`）的逐步官方说明**：官方仅在 reference 列出参数与返回结构，**没有**对应的 guide → 分片大小、推荐 chunk 尺寸、`finish` 阶段的必需参数、`cancel` 的具体语义与副作用 → **未确认**。
8. **`source`（整文件 form-data 上传）路径的文件大小上限**，以及与 file handle / `file_url` 三条路径的官方取舍建议 → **未确认**。
9. **普通 Page 视频发布的频率限制**：官方只记录了 **Reels** 的 30 条/24 小时限流（且明确针对 `POST /{page_id}/video_reels`），普通 `/videos` 是否有同等级限流**未见记载** → **未确认**。
10. **API 版本号**：官方 HTML 文档渲染为 v26.0，而同一页的官方 Markdown 导出硬编码 v25.0 → 当前应使用的版本号 **未确认**，必须在开发时动态核验。
11. **`end_offset` 的官方文字说明**：该页仅给出参数名，未给出描述文本 → **未确认**（可合理推断为分片结束字节位置，但不作为已确认值）。
12. **"取消已发布视频但保留草稿"的官方语义**：官方只有 `DELETE /{video-id}` 与 `upload_phase=cancel` 两个原语，未定义中间语义 → **未确认**。
13. **平台审核的具体触发条件与时长**（视频发布后是否/何时进入人工审核、审核窗口多长）→ **未确认**。
14. **Meta Platform Terms 中关于自动化交互的具体条款文本**：本次未逐条抓取核验，仅作合规原则要求 → 具体条款 **未确认**。
