# V6-01 Platform Profile 与后期模板（真实云端证据）

- 日期：2026-09-13（云端会话）
- 范围：主规划 12.3（Platform Profile 字段与六个首批导出 Profile）、12.4（后期模板与配音适配策略）、12.15 V6-01「Profile/后期模板 Schema、版本、种子配置、安全区」。
- 状态：V6-01 **完成**（代码 + 版本化 API + 六个种子 Profile + 线上部署与冒烟）；V6 整体仍为 IN_PROGRESS。

## 1. 交付物

### 1.1 合同模块 `apps/api/productdirector_api/platform_profiles.py`

- `PlatformProfileSpec`（不可变版本快照）：`identity`（profile_id/version/name/platform/purpose）、`market`（region/locale/IANA timezone）、`video`（宽高/fps/时长区间/编码/封装/码率策略/**硬限制列表**）、`composition`（aspect_ratio/safe_area/subject_roi/crop_policy）、`copy`（风格/长度上限/**计数算法**/话题标签策略/CTA/禁止宣传语）、`subtitles`（enabled/burn_in/侧车格式/font_ref/**font_license**/字号/位置/最大行数）、`voice`（enabled/locale/voice_ref/rate/发音词典）、`music`（enabled/素材/许可引用/**commercial_use_allowed**/增益/ducking）、`mix`（目标响度/容差/真峰值/声道）、`publish`（output_target/建议可见性/披露字段/auto_publish 默认 false）、`rules`（来源 URL/核验时间/规则修订）。
- `PostproductionPresetSpec`（后期模板）：字幕/配音/BGM/混音 + **配音过长适配策略**（延长非接触镜头 / 调速 / 改文案 / 失败待审）+ 扩展帧上限 + 固定后期顺序（锁定剪辑与文案 → 配音 → 实测音频 → 适配 → 锁定时间线 → 字幕对齐 → 混音 → 编码 → QA → 包）。
- 校验器 `validate_spec` / `validate_preset_spec` / `output_compatibility` / `rules_status`，输出 BLOCKING / WARNING 分级与字段定位。

### 1.2 六个首批导出 Profile（`SEED_PROFILES`，启动时幂等写入）

| profile_key | 平台 | 比例/分辨率 | 语言 | 时长范围 | 构图策略 |
| --- | --- | --- | --- | --- | --- |
| `tiktok-mx-9x16-esmx` | TikTok | 9:16 · 1080×1920 | es-MX | 3–60s | crop_if_safe |
| `youtube-shorts-9x16-en` | YouTube | 9:16 · 1080×1920 | en-US | 3–60s | crop_if_safe |
| `instagram-reels-9x16-en` | Instagram | 9:16 · 1080×1920 | en-US | 3–90s | crop_if_safe |
| `facebook-ads-1x1-esmx` | Facebook Ads 素材 | 1:1 · 1080×1080 | es-MX | 3–60s | letterbox |
| `marketplace-1x1-esmx` | Marketplace | 1:1 · 1080×1080 | es-MX | 3–60s | letterbox |
| `pinterest-2x3-en` | Pinterest | 2:3 · 1000×1500 | en-US | 3–60s | letterbox |

诚实边界（写进每个 Profile 的 notes 与校验结果）：

- **硬限制一律为空**：未核验平台规则就不写平台事实；任何 `hard_limits` 条目必须带 `source_url` + `verified_at`，否则 BLOCKING。
- **规则状态如实 `NOT_VERIFIED`**：`rules_verified_at` 未记录；超过 180 天为 `STALE`；两者都是发布前的阻断/复核条件（V6-C）。
- 文案长度上限标注为**本产品默认策略值**并声明计数算法（`unicode_codepoints`），不把某个平台的 2200 当作所有平台限制。
- 字幕字体使用云端已安装的 **DejaVu Sans** 并记录其 Bitstream Vera 许可（可再分发）；字体缺失或许可缺失为 BLOCKING。
- 配音**默认关闭**（未配置 TTS Provider）：主规划允许「无服务可上传授权配音；没有配音时显式关闭」。
- BGM 默认关闭；开启时必须有素材与许可引用，商业用途 Profile（Ads 素材/Listing）还必须 `commercial_use_allowed`。
- 选择 Profile **不代表账号存在**、不代表平台只支持该比例、也不代表存在官方发布 API（notes 明示）。

### 1.3 API

| 方法与路由 | 行为 |
| --- | --- |
| `GET /api/v1/platform-profiles` | 列表 + 最新版本校验摘要（valid/blocking/warning/rules_status） |
| `POST /api/v1/platform-profiles` | 新建草稿（可给完整 spec 或按平台取模板骨架）；非法 spec → 422；重复 profile_key → 409 |
| `GET /api/v1/platform-profiles/{id}` | 详情（profile + 最新版本 + 完整 spec + 校验 + 规则状态） |
| `GET /api/v1/platform-profiles/{id}/versions` | 版本历史（旧版本保留） |
| `POST /api/v1/platform-profiles/{id}/versions` | 生成新不可变版本；**版本号由服务端强制递增**，客户端指定被覆盖 |
| `POST /api/v1/platform-profiles/{id}/validate` | 规格校验 + 可选实际成片兼容性（`output`）+ 可选账号能力（未配置 → `NOT_CONFIGURED`，不伪造能力） |
| `GET /api/v1/postproduction-presets`、`GET /api/v1/postproduction-presets/{id}`、`POST /api/v1/postproduction-presets`、`POST /api/v1/postproduction-presets/{id}/versions` | 后期模板的版本化读写 |

### 1.4 数据库

新增 `platform_profiles` / `platform_profile_versions` / `postproduction_presets` / `postproduction_preset_versions`（SQLite 内联 schema 与 `db/schema.postgres.sql` 同步；PostgreSQL 路径经方言适配写入）。启动时幂等写入 6 个 Profile 与 2 个后期模板种子（`clean-9x16-nosub`、`es-mx-subtitled`）。

## 2. 验证证据

### 2.1 单元/接口测试（云端）

- 新增 `tests/test_v6_profiles.py` **20 例**：六个种子 Profile 有效且覆盖预期平台/比例/语言/分辨率、产品默认时长完整落区间、主体 ROI 在安全区内；负例覆盖比例不一致、主体越出安全区、硬限制未核验、BGM 缺许可、非商用 BGM 用于广告素材、字幕缺字体许可、字幕缺 SRT、规则过期、配音语言不匹配；模型层拒绝时长区间倒置；成片兼容性（比例/时长）；后期模板校验；API 层覆盖种子列表、版本不可变与自增（含客户端指定版本被覆盖）、validate（含成片兼容与账号 NOT_CONFIGURED）、草稿创建/重复 409/非法 422、模板版本化。
- 全量后端回归：见下文（本轮结束时 409 例）。

### 2.2 线上冒烟（真实 HTTP + 真实 PostgreSQL，16/16 PASS）

```
[PASS] login status=200
[PASS] GET /platform-profiles status=200 n=6
[PASS] 六个种子 Profile 全部通过规格校验 invalid=[]
[PASS] 规则状态如实 NOT_VERIFIED
[PASS] 比例覆盖 9:16 / 1:1 / 2:3 ['1:1', '2:3', '9:16']
[PASS] validate（规格层） blocking=[]
[PASS] validate（含真实成片兼容） blocking=[]
[PASS] 1:1 Profile 拒绝 9:16 成片（不得盲目裁切）
[PASS] 账号能力如实 NOT_CONFIGURED
[PASS] GET /platform-profiles/{id} status=200
[PASS] POST /platform-profiles/{id}/versions status=201 version=2
[PASS] 版本历史保留旧版本 n=2
[PASS] GET /postproduction-presets keys=['clean-9x16-nosub', 'es-mx-subtitled']
[PASS] es-MX 模板：字幕开启、无 Provider 时配音显式关闭
[PASS] POST /platform-profiles（草稿） status=201
[PASS] 非法 spec 返回 422 status=422
```

冒烟过程发现并修复一个真实缺陷：创建草稿时非法 spec 的 `ValidationError` 未被捕获（会变成 500），现改为在写库前校验并返回 422。

## 3. 如实未完成 / 边界

- **平台硬限制与规则来源未核验**：六个 Profile 的 `hard_limits` 为空、`rules` 未记录核验时间（`NOT_VERIFIED`）。这不是遗漏而是诚实状态；V6-C 连接器施工前必须按官方资料核验并写入 `docs/integrations/<platform>.md`。
- **账号能力**：`POST /platform-profiles/{id}/validate` 的 `account_id` 分支如实返回 `NOT_CONFIGURED`（无 OAuth 连接器），不产生任何账号能力结论。
- **1:1 / 2:3 目标尚无对应渲染**：当前渲染基线为 9:16（540×960 / 1080×1920）；`output_compatibility` 会阻断不匹配的成片并提示按 `crop_policy` 处理，真正的 1:1/2:3 生产属于 V6-03/V6-05。
- **文案/字幕/配音/BGM 只完成合同与模板**：实际 es-MX 文案、SRT 生成与对齐、TTS 试听、ducking 与响度测量属于 V6-02/V6-03。
- **前端**：本轮只有后端合同与 API，总控台界面属于 V6-09。
