# V6-02 本地化文案、字幕对齐与 es-MX 配音（真实云端证据）

- 日期：2026-09-13（云端会话）
- 范围：主规划 12.4（文案本地化 / 字幕 / 对齐 / TTS / 混音顺序）与 12.6（文案与标签）、12.9 中的本地化与试听接口；V6-02 完成证据「实际西语音频 / SRT / 人工修订」。
- 状态：V6-02 **完成**（合同 + API + 真实 es-MX 音频 + 语音对齐字幕 + 人工修订）；V6 整体仍为 IN_PROGRESS。

## 1. 交付物

### 1.1 模块 `apps/api/productdirector_api/localization.py`

| 能力 | 实现与诚实边界 |
| --- | --- |
| 文案生成 | 本地确定性模板（`productdirector.local-copy.v1`，**不调用付费 API**），es-MX / en-US 两套；**事实字段（来自批准数据）与生成式宣传语分离输出**，`generated` 显式标注 |
| 违规宣传拦截 | `find_prohibited_claims`：健康/医疗功效、绝对化性能承诺、伪造认证/奖项、虚假折扣紧迫感四类关键词扫描（中英西）→ 建文案与人工修订后都会扫描并回传命中片段 |
| 话题标签 | 格式/数量/重复校验（`validate_hashtags`） |
| 字幕构建 | `split_copy_into_chunks`（按句/逗号保序切分）、`wrap_text`（按词换行、行数上限）、`render_srt` / `render_vtt` / `parse_srt`；SRT 时间戳 `HH:MM:SS,mmm`，VTT `WEBVTT` + `.` 分隔 |
| 字幕校验 | `validate_cues`：时间倒置、与上一条重叠、越过视频结尾、短于最小显示时长、行数/行长超限 |
| 语音对齐 | `detect_speech_segments`（ffmpeg `silencedetect`）+ `cues_from_speech`：按**实际语音段**逐条对齐；语音段不足时回退并说明原因；**不裁剪到视频长度**（配音过长由调用方显式报告） |
| 短句可读性 | 真实语音短于最小显示时长时**延长显示**至最小可读时长，但不越过下一条开始或视频结尾；延长记录在 `padded_short_cues` |
| 无配音路线 | `cues_from_timeline`（按时间线比例分配），如实标注 `timeline_proportional` 且明确写「不是语音精确同步」 |
| TTS 能力 | `tts_capability`：本机可用 espeak-ng（离线开源、`es-419` 拉美西语音色）时标 `AVAILABLE` 且注明 `offline_preview`；不可用时 `NOT_CONFIGURED` 并给出「上传授权配音 / 显式关闭」两条合法路线 |
| 发音词典 | `apply_pronunciation`（term → say_as，记录命中），合成前应用 |

### 1.2 API

| 方法与路由 | 行为 |
| --- | --- |
| `GET /api/v1/audio/engines` | TTS 能力如实上报 + 回退路线清单 |
| `POST /api/v1/runs/{run_id}/localizations` | 建立本地化草稿（locale/Profile）：文案 + 标签 + 文案上限核对 + 违规扫描 |
| `GET /api/v1/runs/{run_id}/localizations`、`GET /api/v1/localizations/{id}` | 列表 / 详情（全部修订、试听、字幕轨） |
| `PATCH /api/v1/localizations/{id}` | 人工修改 → **新 revision**（`edited_from_revision` 记录来源，不覆盖旧文案），并标记 `downstream_invalidated`（下游包审批失效） |
| `POST /api/v1/localizations/{id}/subtitles` | 生成 SRT/VTT：`source=voice` 按真实配音对齐 / `timeline` 按时间线；不合规问题逐条返回；配音过长 → `audio_longer_than_video` + 三种允许适配策略 |
| `POST /api/v1/audio/previews` | 真实离线合成试听（语速/发音词典），超过 `max_seconds` 直接 422（**不截断音频**）；返回引擎/音色/时长/字符数 |
| `GET /api/v1/audio/previews/{id}/content` | 试听音频下载（鉴权） |
| `POST /api/v1/runs/{run_id}/voiceovers` | 上传**已授权配音**（记录 `license_ref`；原文件默认不入包，仅保留许可引用与混音记录） |

新增表：`localizations`、`localization_revisions`、`audio_previews`、`voiceovers`、`subtitle_tracks`（SQLite 内联 schema 与 `db/schema.postgres.sql` 同步）。

## 2. 真实验证证据

### 2.1 测试（云端）

- 新增 `tests/test_v6_localization.py` **21 例**：文案事实/生成分离、违规宣传扫描（西/英）、标签校验、时间线字幕合法性与重音字符 SRT/VTT 往返、非法字幕四类问题、换行预算、**真实音频语音对齐 + 语音段不足回退**、短句补齐到最小可读时长、长语音不被裁剪、TTS 能力 `NOT_CONFIGURED` 分支、**真实 espeak-ng 西语合成**、发音词典、API 层（本地化创建/修订与旧版本保留、违规扫描、时间线对齐的诚实标注、引擎能力与试听下载、超长试听 422、配音上传后语音对齐、越权/不存在 404）。
- 全量后端回归见第 3 节。

### 2.2 线上真实链路（真实 HTTP + 真实 PostgreSQL + 真实音频，21/21 PASS）

证据目录：`/home/ubuntu/pd-v602-localization-20260913/`（`v602_report.json`、`es-MX.srt`、`es-MX.vtt`、`es-MX-voiceover-3phrases.wav`、`re1-esMX-softsub.mp4`、`re1-esMX-burnin-frame.png`）。

| 步骤 | 真实结果 |
| --- | --- |
| 本地化草稿（es-MX + TikTok MX Profile） | headline `Camera_01_textured.glb · acabado mate`；body `Camera_01_textured.glb con acabado mate. Diseño compacto para el uso diario.`；标签 `#Producto #Diseno #UsoDiario`；违规扫描 0 命中；标签 0 问题 |
| 真实离线配音 | 引擎 `espeak-ng`、音色 `es-419`、`offline_preview`；短句 1.393s；整段文案 10.016s（> 5s 目标视频 → 触发过长判定） |
| 授权配音上传 | 三段真实西语短语拼接（含 0.5s 真实停顿）= **5.547s**，`license_ref=owner-synthesized-preview-2026` |
| **语音对齐字幕** | ffmpeg `silencedetect(noise=-35dB, d=0.25s)` 检出 **3 个语音段 → 3 条字幕**，`status=ALIGNED`；其中第 2 条真实语音仅 0.754s，按可读性补齐到 0.80s 并记录 `padded_short_cues`；时间轴校验 0 问题 |
| 字幕文件 | `es-MX.srt` 166 bytes、`es-MX.vtt` 168 bytes（UTF-8，含 `Diseño` 等重音字符） |
| 配音过长 | 以 2.0s 视频对齐 5.5s 配音 → 显式 `audio_longer_than_video`（含溢出秒数与三种适配策略），**未截断音频或视频** |
| 人工修订 | 3 个 revision 全部保留（r1 生成、r2 与配音对齐、r3 人工收紧标题与标签），旧 revision 内容未被覆盖，`downstream_invalidated=true` |
| 集成（真实成片） | 软字幕封装 `mov_text` + `language=spa`（ffprobe 可读回字幕轨）；libass 用 **DejaVu Sans** 成功烧录含重音字幕帧 |

## 3. 回归

| 项 | 结果 |
| --- | --- |
| 后端全量 | **430/430 OK**（云端，含 V6-01 的 20 例与 V6-02 的 21 例） |
| 前端 | 未改前端；build + Sites 4/4 在 V6-01 后仍通过 |
| 现场服务 | api / web / postgresql / comfy-h3 均 active |

## 4. 如实未完成 / 边界

1. **TTS 为离线预览引擎**：espeak-ng 是开源离线合成，音色机械，**不是商业发行音色**；`engine_kind=offline_preview` 明确标注。正式发行需要接入已配置的 TTS Provider（付费或自托管），或上传授权配音——两条路线都已实现或已留接口。
2. **生成式文案是本地模板**：不调用 LLM，文案质量有限；主规划要求「AI 建议 + 人工修改」，本轮实现的是确定性建议 + 完整人工修订链（revision 不可覆盖）。
3. **ASR 未接入**：主规划把 ASR 列为可替换能力；当前对齐基于 TTS/上传音频的静音切分（`voice_audio`），未做语音识别校验。
4. **时长适配（延长非接触镜头 / 调速 / 改文案）只做了检测与策略声明**，实际执行属于 V6-03（时长适配与混音编码）。
5. **BGM 与混音/响度未实现**（V6-03）：`music`/`mix` 字段已在 Profile 与后期模板合同中，但未接入实际音轨处理。
6. **字幕烧录未进入生产链**：本轮验证了 libass 可烧录（真实成片帧），但发布包中的字幕产物与 burn-in 选项属于 V6-05（发布包）。
7. **前端界面**：本地化/字幕编辑 UI 属于 V6-09 总控台。

## 5. 本轮修好的真实缺陷

- `runs` 表不存 `owner_id`：本地化接口原按 `runs.owner_id` 取归属会 `IndexError`；改为经 `plan_contracts → product_versions` 关联解析（与既有 `resolve_run_owner_scope` 一致）。
- **PostgreSQL 建表口径**：运行在 PostgreSQL 时 schema 来自 `db/schema.postgres.sql`（不是内联 SQLite DDL）；本轮新表最初只加到内联 schema，线上重启后 `relation "localizations" does not exist` —— 已补 `schema.postgres.sql` 并重新部署（教训：两条 schema 必须同时更新且同时部署）。
- 字段名 `copy` 与 `BaseModel.copy` 冲突（pydantic 警告）：改名 `copy_spec` 并保留 `copy` 作为输入别名。
- 短句字幕短于最小可读时长被判为非法：改为按字幕惯例延长显示并如实记录。
- 配音过长此前会被静默裁剪到视频长度（产生倒置/过短字幕且无提示）：改为**先判过长、显式报告适配策略**，字幕保持真实音频时间。
