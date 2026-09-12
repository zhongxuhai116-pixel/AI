# A06 用户流程与质量（进行中）

日期：2026-09-12。状态：**IN_PROGRESS**。本文件随 A06 各任务推进追加证据，不代表 A06 完成。

## 任务清单与状态

| 任务 | 状态 | 证据 |
| --- | --- | --- |
| 黑帧 / 可见性 / 媒体检查 | ✅ 已实现并验证 | 本节第 1 节 |
| 错误 / 取消 / 重试状态 | ✅ 已实现（取消、错误、失败/取消后重试）；重试的浏览器交互验证仍待做 | 本节第 2 节 |
| 尺寸 / 朝向 / 可拍摄视角确认 | ⏳ 未实现 | — |
| 桌面与窄屏 UI 端到端流程 | ⏳ 未验证 | — |
| 损坏素材 / 缺纹理 / 磁盘不足处理 | ⏳ 部分（损坏素材有 422/409，缺纹理与磁盘不足未覆盖） | — |

## 1. 媒体质量门（已实现）

### 为什么需要

此前 QA 阶段只做「文件存在 + 体积 + ffprobe 分辨率/帧率/帧数/时长」检查。渲染失败时仍可能产出**技术参数正确但画面全黑或近乎单色**的视频，并作为成功产物发布。A06 要求补上黑帧与可见性检查。

### 实现

作业进入 `QA` 阶段后，`media_quality_report()` 会：

1. 按冻结 DirectorPlan 的镜头边界取样检查帧（每段首帧 + 整片末帧，默认 4 帧）；
2. 逐帧用 Pillow 计算灰度均值、标准差与近黑像素占比；
3. 用 `ffmpeg blackdetect` 统计黑屏时长占比；
4. 任一检查不通过则**阻断作业**（状态 `FAILED`），不产出「成功」结果；
5. 完整报告写入 `metadata.json` 的 `qa` 字段，便于事后复核。

阈值保守且可通过环境变量覆盖，避免把低对比度的正常产品画面误判为失败：

| 参数 | 默认 | 环境变量 |
| --- | --- | --- |
| 最小标准差（低于视为近乎单色） | 2.0 | `PRODUCTDIRECTOR_QA_MIN_STDDEV` |
| 最小灰度均值（低于视为接近全黑） | 6.0 | `PRODUCTDIRECTOR_QA_MIN_MEAN` |
| 最大黑屏时长占比 | 35% | `PRODUCTDIRECTOR_QA_MAX_BLACK_RATIO` |

### 验证证据

**单元/集成测试（真实 FFmpeg 产物，非 mock）**：`tests/test_media_quality.py`

| 用例 | 输入 | 期望 |
| --- | --- | --- |
| `test_black_video_is_rejected` | 真实全黑视频 | 拒绝，黑屏占比 > 90% |
| `test_uniform_white_video_is_rejected_as_invisible` | 真实纯白视频 | 拒绝（近乎单色） |
| `test_moving_pattern_passes_and_records_samples` | 真实运动测试图 | 通过，取样帧 = 镜头边界 |
| `test_unreadable_video_reports_failure_instead_of_crashing` | 损坏文件 | 报告失败而不崩溃 |
| `test_sample_frames_follow_shot_boundaries` | 计划 24/72/48 | 取样帧 = 1/24/96/144 |

后端回归：本地 **33/33** 通过；云端 **33/33** 通过。

**本机真实链路**：图片作业 `f75886f9-4b8e-4a34-9fa6-c374e8a0a013` 通过质量门，Manifest 记录
`qa.passed=true`、`black_seconds_ratio=0`，取样帧均值 85.48–100.77、标准差 83.62–90.04。

**云端真实链路**（`scripts/v1_cloud_acceptance.py`，目录 `var/acceptance/20260912T014719Z`）：

| 链路 | 终态 | 耗时 | 产物 | 质量门 |
| --- | --- | --- | --- | --- |
| 图片 | SUCCEEDED | 6.0 秒 | H.264 1080×1920 144 帧 6 秒 | passed，黑屏比 0，4 个取样帧 |
| GLB（真实 Blender） | SUCCEEDED | 60.2 秒 | H.264 1080×1920 144 帧 6 秒 | passed，黑屏比 0，4 个取样帧 |

两次云端验收（加质量门前 `…014015Z` 与加质量门后 `…014719Z`）的视频 SHA-256 完全一致
（图片 `79cf26e8…`、GLB `6e6f46a2…`），说明相同输入与代码产生确定性产物，质量门没有改变成片内容。

### 已知限制

- 质量门只拦截明显故障，不替代人工审美与产品保真审核；V3 的 Strict 保真门是另一套要求。
- 目前按镜头边界取 4 帧，不做全帧扫描；阈值对极暗风格化产品素材可能需要按项目调整。

## 2. 失败 / 取消后的重试（已实现）

### 行为

`POST /api/v1/jobs/{job_id}/retry`（需 Owner 鉴权与项目归属校验）：

- 只接受 `FAILED`、`CANCELLED`、`CANCEL_REQUESTED`；其他状态返回 409。
- 在**单一写事务**内新增一条 `job_attempts` 记录（attempt + 1），把 `run_jobs`/`jobs`/`runs` 退回 `QUEUED`，清除错误、取消标记与旧产物引用。
- 写入 `job.retry_requested` 事件（含原状态与新的 attempt 号），便于事后追溯。
- 重新排入后台执行；重试的任务会被重新领取并推进租约 epoch，旧 worker 的迟到回报仍被 409 拒绝。
- 保留原 run 与历史 attempt，不新建重复 Run，也不把旧成片当作新结果。

前端在 `FAILED` / `CANCELLED` / `CANCEL_REQUESTED` 状态下显示「重试任务」按钮，调用同一接口后刷新任务状态。

### 验证证据

| 用例 | 覆盖 |
| --- | --- |
| `test_a06_retry_failed_job_requeues_as_new_attempt` | 失败任务重试后 attempt=2、错误/取消标记/产物引用清空、事件写入，且能被 worker 重新领取并完成到 `SUCCEEDED` |
| `test_a06_retry_after_cancel_clears_cancel_flag` | 取消中的任务可重试，`cancel_requested` 归零 |
| `test_a06_retry_rejects_jobs_that_are_not_failed_or_cancelled` | 正常排队中的任务重试返回 409，状态不变 |

本地与云端均为后端 **36/36** 通过；云端 `7b5d988` 部署后 `/openapi.json` 已包含 retry 路由，API 与 Web 服务均 active。

### 已知限制

- 重试不清理上一次失败留下的中间产物（帧目录等），产出会被新一次尝试覆盖；磁盘清理策略属于后续任务。
- 前端按钮与状态流转尚未做真实浏览器交互验收（见任务表最后两行）。
