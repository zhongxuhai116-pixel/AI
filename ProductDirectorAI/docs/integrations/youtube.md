# YouTube 集成说明（YouTube Data API v3 上传/发布）

- 文档核验日期：**2026-09-13**（本机日期）。以下所有端点、scope、配额、字段与数值均来自当日抓取的官方文档。
- ⚠️ 动手写连接器之前，**必须逐项重新核验**本文所有值：Google 会改动配额、scope 分类、校验规则与政策文本，本文只是核验时点的快照。
- 本文只描述官方文档明确写出的行为；未能确认的一律标注 `未确认`，不做推测。
- 本文不包含任何凭据、频道 ID、项目编号或账号标识；这些值只能由部署环境注入。

## 1. 授权（OAuth 2.0）

### 1.1 应用类型与流程
- 官方提供两条流程：**已安装应用（installed app / 桌面端）** 与 **Web 服务器应用（server-side web app）**；两条流程都需要先在 Google API Console 创建 OAuth 客户端凭据。
- 已安装应用流程使用 **PKCE**：`code_verifier` 为 43–128 个字符的高熵随机串，`code_challenge` 由其派生；换码时回传 verifier。
- Web 应用流程需要注册 redirect URI 并处理 `redirect_uri_mismatch` 等错误。

### 1.2 必需 scope（官方 videos.insert 页面列出的可授权范围）
| 用途 | 官方文档中的 scope |
| --- | --- |
| 上传视频（最小权限） | `https://www.googleapis.com/auth/youtube.upload` |
| 读取视频状态/元数据、写元数据（回读 `status`、`processingDetails`，调 `videos.update` / `videos.delete` / `thumbnails.set`） | `https://www.googleapis.com/auth/youtube`、`https://www.googleapis.com/auth/youtube.force-ssl` |
| 内容合作方（CMS）场景 | `https://www.googleapis.com/auth/youtubepartner` |

要点（均为文档原文含义）：
- `videos.insert`、`thumbnails.set`、`videos.update`、`videos.delete` 的授权列表中都**不含** `youtube.upload` 之外的读权限；其中 `videos.update` / `videos.delete` **不接受** `youtube.upload`。因此：**只申请 `youtube.upload` 只能上传，无法回读处理进度、无法改元数据、无法删除**。要覆盖查询与更正，需同时申请 `youtube`（或 `youtube.force-ssl`）。
- `status.selfDeclaredMadeForKids` 在 `videos.list` 中**只有频道所有者授权时才返回**；`processingDetails` 明确写明**只有视频所有者可以读取**。
- 用户可能只授予部分 scope，官方要求客户端显式校验实际授予的 scope 并优雅降级。
- 官方要求只申请当前功能真正需要的 scope，不允许"提前"申请将来才会用到的写权限。

### 1.3 令牌寿命与刷新
- 访问令牌有有限寿命（示例响应 `expires_in: 3920` 秒），过期后用刷新令牌换新访问令牌。
- **已安装应用总会返回刷新令牌**（官方原文：refresh tokens are always returned for installed applications）。
- 刷新令牌可能失效的原因（官方列举）：用户撤销授权；**连续六个月未使用**；用户改密码且令牌含 Gmail scope；超出活动刷新令牌上限；时间受限访问到期；管理员策略（`admin_policy_enforced`）等。
- 上限：**每个 Google 账号 / 每个 OAuth 客户端 ID 最多 100 个刷新令牌**，超限时最旧的令牌会被无提示作废。
- **Testing 状态的应用**（外部用户类型、发布状态 = "Testing"）签发的刷新令牌 **7 天过期**——除非只申请 `userinfo.email` / `userinfo.profile` / `openid` 这类基础 scope。上传必然超出该集合，所以测试态应用必须预期"每周重新授权"。
- 授权被撤销的情形，接口会返回 `invalid_grant`；官方还给出 `error_subtype`（例如 `invalid_rapt`）用于区分会话策略导致的中断，且要求客户端重启授权流程。

### 1.4 撤销端点
- 端点：`https://oauth2.googleapis.com/revoke`，参数 `token={token}`。
- 该 token 可以是访问令牌或刷新令牌；**若是访问令牌且存在对应刷新令牌，刷新令牌会一并撤销**。
- 官方特别提示：撤销会移除该项目此前授予的全部 OAuth scope，使该项目下所有客户端已签发的访问/刷新令牌失效（对多账号产品是大范围副作用，必须做成显式的"断开账号"操作）。

### 1.5 OAuth 同意屏幕与"未验证应用"
- 必须配置 OAuth 同意屏幕。申请敏感或受限 scope 的应用需要通过 Google 的 OAuth 应用验证；只申请非敏感 scope 的应用可不做完整验证，但若要在同意页显示应用名与图标，仍需轻量的品牌验证（brand verification）。
- 文档明确：测试时若出现 **"unverified app"** 提示，需要提交验证请求才能移除。
- 未验证应用在 Testing 状态下的测试用户数量上限：`未确认`（本次抓取的官方页面未给出该数字）。

## 2. 上传流程（可续传上传协议 / resumable upload）

### 2.1 第 1 步：开启会话
```
POST https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=<PARTS>
Authorization: Bearer <ACCESS_TOKEN>
Content-Length: <body 字节数>
Content-Type: application/json; charset=UTF-8
X-Upload-Content-Length: <将上传的文件字节数>
X-Upload-Content-Type: video/* 或 application/octet-stream

{ video resource ... }
```
- `part` 既决定写入哪些属性，也决定响应里返回哪些 part；URL 中参数必须 URL 编码。
- 请求体是 video resource（JSON），不是媒体本身。

### 2.2 第 2 步：保存会话 URI
- 成功返回 **HTTP 200**，响应头 `Location` 即会话 URI（形如 `https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&upload_id=...&part=...`）。
- 官方明确：**每个会话 URI 都有有限寿命并会过期**，建议拿到后尽快开始上传、中断后尽快续传。

### 2.3 第 3 步：上传文件
```
PUT <UPLOAD_URL>
Authorization: Bearer <ACCESS_TOKEN>
Content-Length: <本次请求体字节数>
Content-Type: <与第 1 步 X-Upload-Content-Type 相同>
<二进制数据>
```

### 2.4 第 4 步：结果判定
| 结果 | 官方含义 |
| --- | --- |
| **201 Created** | 上传成功，响应体是新建的 video resource |
| 308 Resume Incomplete | 未完成但可续传；`Range` 头给出已成功接收的字节范围（0 基，`bytes=0-999999` 表示前 1,000,000 字节） |
| 500 / 502 / 503 / 504 | 可续传；官方要求用**指数退避**后续传 |
| 其他 4xx / 5xx | 永久失败，响应含错误说明 |
| **404**（会话 URI 已过期） | 必须新建会话、拿新 URI、从头发送 |

### 2.5 中断查询与续传
- 查询状态：向同一会话 URI 发**空 PUT**，`Content-Range: bytes */<CONTENT_LENGTH>`；若已完成则返回当初的最终响应，否则返回 308 + `Range`。
- 续传：`Content-Range: bytes <FIRST_BYTE>-<LAST_BYTE>/<TOTAL_CONTENT_LENGTH>`，请求体为剩余字节。
- 硬约束：**不能上传不连续的块**。续传首字节必须是已成功接收字节的下一个字节；重叠或跳字节会导致本次二进制内容全部不被接收。
- 响应含 `Retry-After` 时，按该值决定重试时机。
- 也可在块之间随时查询状态（不必先中断）。

### 2.6 分块规则
- 分块上传官方**不推荐**（请求更多、影响性能），仅在需要进度显示或网络极不稳定时使用。
- **块大小必须是 256 KB 的整数倍**（最后一块例外，因为文件总大小未必是 256 KB 的倍数）。
- **除最后一块外，每块大小必须一致**。
- 非末块成功返回 308；用响应 `Range` 的上界决定下一块起点；最后返回 201 + 所请求的 part。

### 2.7 元数据设置（`part=snippet,status`）
`videos.insert` 官方明确可写的属性：
- `snippet.title`、`snippet.description`、`snippet.tags[]`、`snippet.categoryId`、`snippet.defaultLanguage`、`localizations.*`
- `status.privacyStatus`（`private` / `public` / `unlisted`）、`status.publishAt`、`status.selfDeclaredMadeForKids`、`status.containsSyntheticMedia`、`status.embeddable`、`status.license`、`status.publicStatsViewable`
- `recordingDetails.recordingDate`、`brandPartner` 相关字段

补充事实：
- `categoryId` 必须是 `videoCategories.list` 返回的可用分类；非法值报 `invalidCategoryId`。
- `tags` 传空字符串会得到 **400 Bad Request**（官方 Go 示例注释），应省略该字段而不是传空串。
- 标题长度上限 100 字符、描述上限 5,000 字符，且不得包含非法字符（YouTube 帮助中心）。
- 查询参数 `notifySubscribers`：**已文档化**，boolean，默认 `true`，控制是否向订阅者发送新视频通知。

### 2.8 定时发布（`status.publishAt`）
- 只有在视频隐私状态为 `private`、且**该视频从未被发布过**时才能设置。
- ISO 8601 格式；若设置为过去的时间，视频会**立即发布**（等价于把 `privacyStatus` 改为 `public`）。
- 用 `videos.update` 设置该属性时，必须同时把 `status.privacyStatus` 指定为 `private`（即使本来就是 private）。
- `videos.update` 的 `part` 会**覆盖**该 part 下所有可变属性：若 `part=status` 而请求体没写某属性，该属性会被重置为默认值。

## 3. 处理与结果（上传成功 ≠ 已发布）

- 上传返回 201 只代表**文件已接收**。官方实现指南写明：上传后的视频会立即出现在授权用户的"已上传视频"列表里，但**在 YouTube 上可见之前必须先完成处理**。
- 官方检查方式：`videos.list`，`id=<videoId>`，`part=processingDetails`（可加 `snippet`）。
- `status.uploadStatus` 取值：`deleted`、`failed`、`processed`、`rejected`、`uploaded`。
- 失败原因 `status.failureReason`（仅当 `uploadStatus=failed`）：`codec`、`conversion`、`emptyFile`、`invalidFile`、`tooSmall`、`uploadAborted`。
- 被拒原因 `status.rejectionReason`（仅当 `uploadStatus=rejected`）：`claim`、`copyright`、`duplicate`、`inappropriate`、`legal`、`length`、`termsOfUse`、`trademark`、`uploaderAccountClosed`、`uploaderAccountSuspended`。
- `processingDetails.processingStatus`：`processing` / `succeeded` / `failed` / `terminated`（`terminated` = 处理信息不再可用）。
- `processingDetails.processingFailureReason`（仅当 `failed`）：`other`、`streamingFailed`、`transcodeFailed`、`uploadFailed`。
- `processingDetails.processingProgress`：`partsTotal`、`partsProcessed`、`timeLeftMs`；官方给出的百分比算法为 `100 * partsProcessed / partsTotal`，并说明**进度可能阶段性下降**（总块数估计会上调）。
- 官方明确说 `processingProgress` **就是设计来被轮询的**，且这些数据只有视频所有者能取。
- 另有可用性标志：`fileDetailsAvailability`、`processingIssuesAvailability`、`tagSuggestionsAvailability`、`editorSuggestionsAvailability`、`thumbnailsAvailability`。
- 轮询频率、超时上限、重试次数：官方未给数值，`未确认`。
- **重要平台约束**：2020-07-28 之后创建的**未验证 API 项目**，通过 `videos.insert` 上传的视频一律被限制为 `private`；解除该限制需要项目通过合规审核（audit）。

## 4. Shorts 相关

官方文档中**不存在任何 "Shorts" 专用 API 参数**（`videos.insert` 的可写属性与查询参数列表中均无 Shorts 字段）。Shorts 是 YouTube 依据上传视频自身属性做出的**平台侧归类**，不是 API 上传时声明的类型。官方口径：
- 时长**最长 3 分钟**，画面为**方形或竖屏（square or vertical）**的视频会被归类为 Shorts；标准频道以 **2024-10-15** 为界，Official Artist Channel 以 **2025-12-08** 为界（此日期之前的存量视频仍按长视频处理）。
- 上传入口说明：从电脑上传 Short 时"不超过 3 分钟、方形或竖屏宽高比"；Shorts 上传的最大分辨率为 **1080p**。
- 竖屏短视频的常用宽高比为 **9:16**（Shorts 缩略图推荐宽高比，官方自定义缩略图页明确写出）。
- 时长/宽高比的判定阈值在 API 响应中如何暴露（例如能否从 API 判断某视频已被归类为 Shorts）：`未确认`。
- 官方"3 分钟 Shorts"页面还说明：超过 1 分钟且存在有效版权主张的 Short 会被全球屏蔽（与渠道无关），这不属于 API 行为，但会影响发布后的可用性预期。

## 5. 配额（仅记录官方页面实际写出的内容）

- 默认分配（Quota Calculator 与 Quota and Compliance Audits 两页一致）：**`videos.insert` 每天 100 次调用，`search.list` 每天 100 次调用，其余端点合计 10,000 units/天**；Google API Console 的 Quotas 页可查看用量。
- 读操作成本：`videos.list` = **1 unit**，`channels.list` = **1 unit**（方法页顶部"Quota impact"写明）。
- `videos.update` = **50**、`videos.delete` = **50**、`thumbnails.set` = **50**（Quota Calculator 表格）。
- 官方另有一句表述：**"每个请求（即使无效）至少消耗 1 个配额点"**；`search.list` 与 `videos.insert` 有独立配额桶，各 100 次/天、**每次调用计 1 配额**。
- ⚠️ **文档内部不一致，必须标为未确认**：同一 Quota Calculator 页面的摘要段落写 `videos.insert` 的 cost 为 **1600 points**，而其表格与上述独立配额桶段落写的是"每天 100 次、每次 1 配额"。两种数值无法同时成立，落地前必须以控制台实际用量实测确认。见 §10 第 1 条。
- 配额的每日重置时间：`未确认`（本次抓取的页面未写出）。
- 超额申请：需要先完成 API 合规审核（audit）并提交配额扩展表；审核过的开发者（12 个月内）可申请追加扩展。

## 6. 素材限制（容器 / 编解码 / 体积 / 缩略图）

官方"推荐的 upload encoding settings"：
- **容器：MP4**；不要有 Edit Lists（否则可能无法正确处理）；`moov atom` 置于文件前部（Fast Start）。
- **视频编解码：H.264**（Progressive scan、High Profile、2 consecutive B frames、Closed GOP（GOP 为帧率的一半）、CABAC、可变码率、色度采样 4:2:0）。
- **音频编解码：AAC-LC / Opus / Eclipsa Audio**；声道 Stereo 或 Stereo+5.1；采样率 48 kHz。
- **帧率**：与录制帧率一致；常见 24/25/30/48/50/60 fps；隔行内容须先反隔行。
- **推荐码率（SDR，标准帧率）**：360p 1 Mbps、480p 2.5 Mbps、720p 5 Mbps、1080p 8 Mbps、1440p 16 Mbps、2160p(4K) 35–45 Mbps、8K 80–160 Mbps（高帧率档位另有更高值；HDR 另有独立表格）。
- 官方同时说明：**码率无强制上限**，上表是推荐值。
- **单文件最大体积：`未确认`**（本次抓取的官方页面未写出该数值）。可续传协议只规定 X-Upload-Content-Length 与 256 KB 分块规则，未给上限。
- 支持的完整容器/编解码清单（例如 MOV/AVI/WMV/FLV 是否受支持）：`未确认`；官方只给出上述"推荐"配置。

**自定义缩略图（`thumbnails.set`）**：
- 方法：`POST https://www.googleapis.com/youtube/v3/thumbnails/set?videoId=<ID>`，请求体是图片二进制；scope 可用 `youtube.upload` / `youtube` / `youtube.force-ssl` / `youtubepartner`。
- 文档化错误：`invalidImage`、`mediaBodyRequired`、403 `forbidden`（无权限上传自定义缩略图）、`videoNotFound`、429 `uploadRateLimitExceeded`（"该频道最近上传了太多缩略图"）。
- 官方帮助中心要求：**账号必须已验证（verified）才能使用自定义缩略图**；桌面端上限 **50 MB**，移动端视频缩略图 **2 MB**；格式 JPG 或 PNG。
- 推荐分辨率：视频 **3840×2160**（最小宽度 640），Shorts **2160×3840**（最小高度 640）；视频建议 16:9，Shorts 建议 9:16。
- 频道每天可上传的自定义缩略图数量有上限（官方未给出具体数字，达到上限报"Daily custom thumbnail limit reached"），且会受国家/地区与频道历史影响 → 具体阈值 `未确认`。

## 7. 必须保留的用户交互与政策要求

- **AI 内容披露**：官方要求创作者披露"用 AI 实质性改变或生成的、看起来真实（realistic/photorealistic）的内容"。Studio 的 "AI use / Attributes" 开关对应 API 的 `status.containsSyntheticMedia`；官方说明选择披露后会为视频加标签，未披露可能被自动加标签或被处罚（含移除内容、取消 YPP 资格）。
- **Made for kids 申报**：为遵守 COPPA 及与 FTC 的协议，上传时必须声明是否面向儿童；对应 API 的 `status.selfDeclaredMadeForKids`，读回值（`status.madeForKids` / `selfDeclaredMadeForKids`）仅在频道所有者授权时可见。面向儿童的 API 客户端另有严格限制（见下）。
- **Community Guidelines**：所有内容与自定义缩略图都必须遵守；缩略图违规可能导致警告、30 天禁用自定义缩略图权限甚至账号终止。
- **最小功能要求（RMF）与用户最终控制权**（开发者政策原文含义）：
  - 支持上传的客户端**必须允许用户为每个视频设置标题**，且**不得**把标题上限设得比 YouTube 的 100 字符更短。
  - 写操作可以**建议**或预填属性值，但**用户必须对最终发布的数据有最终控制权**；未经用户明确同意，**不得**截断、追加或改写用户提供的值（例如不得擅自追加录制日期或客户端名称）。
  - 客户端**必须清楚展示**可见性选项（`public` / `private` / `unlisted`），且**未经用户明确指示不得修改**既有可见性设置。
- **禁止绕过与自动化滥用**：
  - 开发者政策**禁止抓取（scrape）YouTube/Google 应用**或获取抓取来的数据；**禁止使用未公开（undocumented）API**，也禁止逆向未公开接口。
  - **不得在未经用户事先明确同意的情况下自动化或触发上传、观看、评论、点赞等行为。**
  - 不得收集/代理/缓存用户在认证过程中提供的凭据（用户名、密码等），必须走 OAuth。
  - 因此：**本连接器不得使用浏览器自动化、Cookie 注入、私有接口调用或任何方式绕过上述授权、申报与配额机制**；所有写入必须由用户逐次授权并确认。
- **面向儿童（Child-Directed）客户端的额外限制**：这类客户端**不得**执行任何 YouTube API 写操作（含上传）；如运营方要上传自己的视频，必须另建一个专用于上传的 API 项目并使用 `mfk110` 作为项目 ID 前缀，且 Made for Kids 视频必须把该参数设为 `true`。

## 8. 对本产品接口的映射

| 本产品接口 | 对应的官方调用/机制 | 落地要点 |
| --- | --- | --- |
| `begin_authorization()` | OAuth 2.0 授权请求（installed app + PKCE 或 web app） | 生成 `code_verifier`/`code_challenge` 与 `state`；请求 `youtube.upload`（+ 需要读回/改元数据时加 `youtube`）；保存 state 与 PKCE verifier，短 TTL 失效 |
| `exchange_callback()` | 授权码换令牌（`expires_in`，installed app 总会返回 refresh token） | 落库刷新令牌并加密存储；校验实际授予的 scope，缺 `youtube` 时降级为"仅上传+不轮询"模式并提示用户 |
| `refresh_authorization()` | 用刷新令牌换访问令牌 | 处理 `invalid_grant` 与 `error_subtype`；Testing 状态应用 **7 天**必失效，需重新走授权；**不得**把撤销当作普通刷新失败——撤销会连带项目内全部令牌失效 |
| `get_account_capabilities(account_ref)` | `channels.list`（`part=contentDetails,status`，1 unit）+ 项目侧状态 | 判断是否具备上传能力、是否已验证（自定义缩略图前置条件）、项目是否处于"未验证 → 上传只能 private"状态；把"是否需要 audit"作为能力标志返回。具体字段到能力的判定规则 `未确认`，需按 `channels.list` 实际响应实现 |
| `validate_package(package, account, publish_options)` | 官方限制的本地前置校验 + `videoCategories.list` | 校验标题 ≤100 字符、描述 ≤5000 字符、`categoryId` 合法、容器/编解码/分辨率符合推荐配置、缩略图规格与账号验证状态、`privacyStatus` 与 `publishAt` 组合（`publishAt` 仅允许 private 且未发布过）、made-for-kids 与 `containsSyntheticMedia` 已由用户明确选择。**不臆造**文件体积上限（`未确认`），超过未知阈值时按 API 错误处理 |
| `submit_publish(package, options, execution_key)` | `videos.insert`：`POST /upload/youtube/v3/videos?uploadType=resumable&part=snippet,status` → `Location` 会话 URI → `PUT` 上传 | `execution_key` 作为幂等键；会话 URI 必须持久化以便续传（会话会过期，过期表现为 404，必须新建会话从头重传）；分块按 256 KB 倍数且除末块外等长；`notifySubscribers` 默认 `true`，若产品不希望打扰订阅者需显式置 `false` 并让用户可感知；上传成功只返回 videoId，**不等于已发布** |
| `query_publish(operation_handle)` | `videos.list?id=<videoId>&part=status,processingDetails`（1 unit/次） | 轮询 `processingDetails.processingStatus` 与 `processingProgress`（`100*partsProcessed/partsTotal`，可能回退）；以 `status.uploadStatus` + `failureReason` / `rejectionReason` / `processingFailureReason` 判定终态；这些字段要求所有者授权。轮询间隔与超时 `未确认`，需产品侧自定并在文档中标注为产品策略 |
| `cancel_publish(operation_handle)` | 上传前：本地放弃（不发起 `PUT`，让会话自然过期）。上传后：只能 `DELETE /youtube/v3/videos?id=<videoId>`（50 units），且**不接受** `youtube.upload` scope | **可取消性说明**：官方**没有**"取消正在进行的可续传上传"的文档化端点——只能停止续传并让会话 URI 过期；一旦 201 已返回（视频资源已存在），唯一官方回退是 `videos.delete`。删除是**破坏性且不可逆**的（视频、其观看数据与公开 URL 一并消失），必须要求用户显式二次确认；若只是不想公开，正确做法是用 `videos.update` 保持 `private`/改回 `private`（注意 `part=status` 的覆盖语义），而不是删除 |

额外映射注意：
- `videos.insert` 返回的 videoId 是后续所有操作的唯一句柄；把它作为 `operation_handle` 的一部分并落库。
- 若产品需要"回读上传后的状态"，授权时必须同时拿到 `youtube` scope，否则 `processingDetails` 根本读不到（只有所有者可读）。

## 9. 来源清单（均为本次实际抓取的官方页面）

| URL | 说明 |
| --- | --- |
| https://developers.google.com/youtube/v3/docs/videos/insert | `videos.insert` 参考：scope 列表、`part`、`notifySubscribers`、可写属性（含 `status.containsSyntheticMedia`、`status.publishAt`、`status.selfDeclaredMadeForKids`）、错误码 |
| https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol | 可续传上传四步流程、308/Range/404 语义、256 KB 分块规则、不可跳字节 |
| https://developers.google.com/youtube/v3/docs/videos | video resource：`status.uploadStatus/failureReason/rejectionReason`、`processingDetails.*`、`fileDetails.*`、未验证项目上传限制为 private |
| https://developers.google.com/youtube/v3/guides/implementation/videos | 上传后检查状态的标准做法（`videos.list` + `processingDetails`），"已上传但未处理完不可见" |
| https://developers.google.com/youtube/v3/docs/videos/list | `videos.list`：part 列表、1 unit 成本 |
| https://developers.google.com/youtube/v3/docs/videos/update | `videos.update`：可写属性、`part` 覆盖语义、scope（不含 `youtube.upload`） |
| https://developers.google.com/youtube/v3/docs/videos/delete | `videos.delete`：`id` 参数、204 响应、scope（不含 `youtube.upload`） |
| https://developers.google.com/youtube/v3/docs/thumbnails/set | 自定义缩略图接口、错误码（`invalidImage`、429 `uploadRateLimitExceeded`） |
| https://developers.google.com/youtube/v3/docs/channels/list | `channels.list`：part 列表、1 unit 成本、`auditDetails` 的额外授权要求 |
| https://developers.google.com/youtube/v3/docs/videoCategories/list | `categoryId` 合法值来源 |
| https://developers.google.com/youtube/v3/determine_quota_cost | 配额表（默认分配、各方法成本、"每个请求至少 1 点"、独立配额桶） |
| https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits | 默认配额分配、超过默认需通过合规审核与配额扩展表 |
| https://developers.google.com/youtube/v3/docs/errors | 错误类型（`quotaExceeded`、`uploadLimitExceeded`、`rateLimitExceeded`、`youtubeSignupRequired` 等） |
| https://developers.google.com/youtube/v3/guides/auth/installed-apps | 已安装应用 OAuth + PKCE、scope 列表与验证要求、撤销端点、刷新令牌上限、YouTube Data API 的 scope 说明 |
| https://developers.google.com/youtube/v3/guides/auth/server-side-web-apps | Web 应用 OAuth 流程与回调处理 |
| https://developers.google.com/youtube/v3/guides/uploading_a_video | 官方 Python 上传示例：`youtube.upload` scope、`privacyStatus` 三种取值与默认 public |
| https://developers.google.com/youtube/v3/getting-started | API 项目与凭据的入门要求 |
| https://developers.google.com/identity/protocols/oauth2 | 刷新令牌过期原因、Testing 状态 7 天过期、每个账号/客户端 100 个刷新令牌上限、`invalid_grant`/`error_subtype` |
| https://support.google.com/cloud/answer/9110914 | OAuth 应用验证总览：敏感/受限 scope 需验证、品牌验证 |
| https://support.google.com/cloud/answer/10311615 | OAuth 同意屏幕品牌配置与验证流程 |
| https://developers.google.com/youtube/terms/developer-policies | 开发者政策：最小功能要求、用户最终控制权、禁止抓取/未公开 API、禁止未经同意自动化上传、可视化设置展示、Child-Directed 客户端限制与 `mfk110` 上传项目 |
| https://developers.google.com/youtube/terms/api-services-terms-of-service | YouTube API Services 条款（开发者政策的协议基础） |
| https://support.google.com/youtube/answer/71673 | 上传视频帮助页：标题 100 字符 / 描述 5,000 字符上限、Audience（made for kids）、Altered content 披露入口、每日上传量受频道限制 |
| https://support.google.com/youtube/answer/9528076 | "Made for kids" 判定与 COPPA/FTC 申报要求 |
| https://support.google.com/youtube/answer/14328491 | AI 生成/修改内容的披露要求、标签与未披露风险 |
| https://support.google.com/youtube/answer/72431 | 自定义缩略图：需账号已验证、分辨率/格式/体积（桌面 50 MB、移动 2 MB）、Shorts 9:16、频道每日上限 |
| https://support.google.com/youtube/answer/1722171 | 推荐上传编码设置：MP4 容器、H.264、AAC-LC/Opus、48 kHz、各分辨率推荐码率、16:9 说明 |
| https://support.google.com/youtubecreatorstudio/answer/12779649 | 上传 Shorts：≤3 分钟、方形或竖屏、Shorts 最大 1080p |
| https://support.google.com/youtube/answer/15424877 | 3 分钟 Shorts 的归类规则与生效日期（2024-10-15 / 2025-12-08） |
| https://support.google.com/youtube/answer/10059070 | Shorts 入门（竖屏上传、Made for kids 选择、1080p 上限） |

## 10. 未确认项（不要臆造数值）

1. `videos.insert` 的配额成本：官方同一页面存在"1600 points"与"每天 100 次、每次 1 配额"两种互相矛盾的表述 → 需以控制台实测确认。
2. 单个上传文件的**最大体积**（GB 级阈值）官方页面未写出。
3. 完整的受支持容器/编解码清单（仅给出"推荐"配置：MP4 + H.264 + AAC-LC/Opus）。
4. 配额重置的具体时刻（"午夜太平洋时间"未在本次抓取页面中出现）。
5. 查询上传/处理状态时的官方轮询最小间隔、最大轮询时长与重试上限。
6. Testing 状态下未验证应用的**测试用户数量上限**数值。
7. 项目通过 audit 之后可申请的配额上限数值与审批时长。
8. 自定义缩略图的**频道每日数量上限**具体值（仅知存在上限与 429 错误）。
9. `youtube.upload` 与 `youtube` 两个 scope 在 Google 侧的敏感/受限分类标签（页面只给出 scope 与描述，未标注分类）。
10. 何谓"频道已验证（verified）"的确切资格条件与判定方式，以及能否通过 API 直接查询该状态。
11. 能否通过 API 响应判定一个已上传视频被归类为 Shorts（官方只描述平台侧归类规则）。
12. `videos.insert` 请求中 `part` 是否需要包含 `processingDetails` 才能在响应里拿到处理信息（文档允许该 part，但未明确说明用法）。
13. 官方"取消/中止正在进行的可续传上传"是否存在未文档化的方式（结论：按文档只能让会话过期或事后 `videos.delete`）。
14. `status.containsSyntheticMedia` 与 Studio「AI use」选项的字段级一一对应关系（文档用词为 "Altered or Synthetic (A/S) content"，未逐字对应到该开关）。
