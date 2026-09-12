# A02 / A03 验收记录

验收日期：2026-09-11  
范围：`A02 产品与计划合同（不可变合同 + 版本化）`、`A03 数据与任务基础（幂等 + 任务上下文）`

## 执行内容

- 执行 `python -m unittest discover -s tests -p 'test_*.py'`（本地测试套件）。
- 复核并回归 `tests/test_job_control.py`：
  - `test_a02_contract_version_is_immutable_on_plan_update`
  - `test_a03_run_idempotency_conflict`
  - `test_a03_run_access_requires_project_contract_context`

## 命令输出

```text
............ 
Ran 12 tests in 0.322s

OK
```

## 验收结论

- A02：计划变更会生成新合同版本（`version=2`），旧版本仍保留。
- A03：重复 `idempotency_key` 且请求体一致会复用同一 `run_id/job_id`；
      同键不同内容会返回 `409`；
      不同上下文（同 owner 下不同 project）被拒绝为 `403`，体现了计划/Run 上下文隔离。
- 过程问题修复：`update_job` 里原先向 `run_jobs` 写 `progress` 导致列不存在报错，已移除，确保 `jobs` / `runs` / `run_jobs` 字段同步路径一致。

## 下一步

- 继续推进 `A04 独立执行与恢复`。

---

## 2026-09-12 追加：图片合同与正负例一致性（A02 增量）

### 背景

`contracts/README.md` 一直声明"仅有图片时使用主规划定义的 ImagePreviewSpec"，但仓库里**没有这份文件**：图片路径的字段只存在于 Pydantic 代码里，且新加的 `crop_anchor` 没有任何合同记录。同时根 `director-plan.v1.schema.json` 是目标 3D 合同（相机轨迹/位姿/场景），与运行时简化实现并非同一份东西——这类"文档说一套、代码做一套"正是 A02 要消灭的。

### 本次交付

1. 新增 `contracts/image-preview.v1.schema.json`：描述**当前真实实现**的图片预演冻结快照（`schema_version` / `product_asset_id` / `intent` / `output` / `fidelity_mode` / `crop_anchor` / `shots`）。
2. 新增 `contracts/image-preview.v1.example.json`：一份合法示例（1080×1920、24/72/48 帧、`crop_anchor=left`）。
3. `contracts/README.md` 增加对照表，明确区分"已实现的图片合同"与"尚未实现的目标 3D 合同"。
4. 新增 `tests/test_contract_parity.py`（含 jsonschema 4.26.0）：Schema、示例、运行时模型三方同时校验。

### 正负例证据

| 用例 | 期望 |
| --- | --- |
| `test_example_matches_the_schema` | 示例通过 Draft 2020-12 校验 |
| `test_runtime_models_accept_the_example` | 同一份示例被 `PlanUpdate`/`PlanRequest`/`OutputSpec` 接受，帧数合计 144 |
| `test_schema_rejects_unknown_crop_anchor` | 非法锚点 |
| `test_schema_rejects_unknown_property` | 未知字段（`additionalProperties: false`） |
| `test_schema_rejects_wrong_shot_count` | 镜头数不足 3 |
| `test_schema_rejects_out_of_range_duration` | 单镜 200 帧超上限 |
| `test_schema_rejects_landscape_output` | 非 9:16 输出 |
| `test_runtime_rejects_the_same_bad_cases` | 运行时对同一批负例同样报错（Schema 与运行时判定一致） |
| `test_schema_and_runtime_fields_stay_in_sync` | `Shot`/`OutputSpec`/快照顶层字段集合逐项相等（漂移守卫） |
| `test_target_3d_plan_contract_is_still_structurally_valid` | 目标 3D 合同与示例保持结构有效 |
| `test_contracts_readme_points_to_the_image_preview_spec` | README 必须引用新合同（本次就是靠它发现 README 未更新） |

后端回归：本地 **62/62** 通过。

### A02 仍未完成

- 目标 3D 合同（`director-plan.v1.schema.json` 的相机轨迹/位姿/场景）**尚未实现**为运行时模型；当前只有简化字段。
- 缺少产品版本的审核/批准工作流与多 Owner/Workspace 管理接口（现为单 Owner 部署）。
- 主规格的时长边界（除固定 6 秒外）未实现。

---

## 2026-09-12 追加：目标 3D 合同落地（A02 增量）

上一节列的第 1 条已处理：根 Schema 的相机轨迹、产品位姿与场景现在是真实的运行时能力，并且**真的驱动 Blender 出片**。

### 运行时新增字段

| 位置 | 字段 | 说明 |
| --- | --- | --- |
| `Shot` | `camera_target_m` | 相机注视点；缺省沿用历史默认 `(0, 0, 0.7)` |
| `Shot` | `camera_path` | 判别式联合：`hero_orbit`（半径/高度/起止角度）、`dolly_in` / `side_track`（起止位置）、`static`（位置） |
| `Shot` | `sensor_width_mm` | 传感器宽度，默认 36 |
| 计划 | `product_pose` | 产品位置/旋转/缩放，在归一化底座上叠加 |
| 计划 | `scene` | 模板、背景色（`#RRGGBB`）、布光预设（`softbox` / `three_point`） |

旧计划不填这些字段时，Blender 脚本使用与历史完全相同的默认取景，因此历史作业可复现。

### 合同桥接（`apps/api/productdirector_api/director_plan.py`）

- `to_target_document(snapshot)`：运行时快照 → 符合根 Schema 的 3D 文档；Manifest 里以 `director_plan_3d` 字段随产物保存。
- `from_target_document(document)`：3D 文档 → 运行时字段（下转换）。
- 目标合同把 `product_pose.scale` 固定为 1、`sensor_width_mm` 固定为 36，而运行时已支持更大范围：此时**显式抛 `TargetContractError`**，Manifest 记录 `director_plan_3d_error`，不会输出一份"看起来合规"的文档。

### 验证证据

单元/契约测试（本地 87/87）：

| 用例 | 覆盖 |
| --- | --- |
| `test_default_snapshot_converts_to_a_valid_3d_document` | 默认快照映射出的文档通过根 Schema 校验 |
| `test_unspecified_3d_fields_fall_back_to_renderer_defaults` | 缺省值与 Blender 脚本历史默认一致 |
| `test_explicit_3d_fields_survive_the_bridge` | 显式轨迹/注视点/场景不丢失 |
| `test_contract_limits_are_reported_instead_of_silently_clamped` | 超出目标合同范围时报错而非静默裁剪 |
| `test_round_trip_preserves_shot_semantics` | 往返后镜头语义不变且仍被运行时接受 |
| `test_target_schema_example_still_round_trips` | 官方示例可往返 |
| `test_runtime_rejects_unknown_camera_path_type` / `..._out_of_range_orbit` | 非法轨迹类型与半径 0 被拒绝 |

真实渲染对比（`scripts/director_plan_3d_check.py`，同一 GLB、云端 RTX 4090、1080×1920、144 帧）：

| 计划 | 耗时 | 视频 SHA-256 | 质量门 | director_plan_3d |
| --- | ---: | --- | --- | --- |
| 默认（不提供 3D 字段） | 55.2 秒 | `6e6f46a241645f81…` | passed | 已写入 |
| 显式轨迹（环绕半径 6.5m、-80°→80°、注视点抬高、三点布光、背景 `#101828`） | 55.4 秒 | `3115137984802851…` | passed | 已写入 |

默认计划的哈希与此前所有验收批次**完全一致**，说明新增字段没有改变默认行为；显式轨迹产出不同成片，说明这些字段真的进入了渲染。

### 仍未完成

- 产品版本的审核/批准工作流与多 Owner/Workspace 管理接口（现为单 Owner 部署）。
- 主规格的时长边界（除固定 6 秒外）未实现。
- 目标合同把 `scale` 固定为 1、`sensor_width_mm` 固定为 36；放开需要用带版本号的 Schema 迁移，不能直接改现有合同。
- 前端尚未暴露相机轨迹/位姿/场景编辑控件（当前通过 API 与合同驱动）。

---

## 2026-09-12 追加：产品版本审核工作流（A02 增量）

上一节第 1 条的产品版本审核部分已处理。

### 实现

| 能力 | 行为 |
| --- | --- |
| `POST /api/v1/plans/{id}/approve` | 批准计划时**同时批准其产品版本**，响应返回 `product_version_id` / `product_version_status` / `product_version_approved_at` |
| `POST /api/v1/product-versions/{id}/approve` | 显式批准某一版本；**幂等**（重复批准不改变 `approved_at`），越权返回 403，不存在返回 404 |
| `GET /api/v1/product-versions` | 按素材或项目列出历史版本（版本号、状态、快照哈希、批准时间） |
| `product_versions.approved_at` | 新增列（SQLite `ensure_column` 与 PostgreSQL schema 同步） |

### 验证证据（`tests/test_product_versions.py`，5 项）

| 用例 | 覆盖 |
| --- | --- |
| `test_approving_a_plan_approves_its_product_version` | 批准计划后版本状态为 `APPROVED` 且有批准时间 |
| `test_editing_after_approval_creates_a_new_active_version` | 编辑后 **版本 1 保持 APPROVED、版本 2 为 ACTIVE**，两个版本快照哈希不同 |
| `test_explicit_version_approval_is_idempotent` | 重复批准不改变 `approved_at` |
| `test_unknown_version_is_rejected` | 未知版本 404 |
| `test_versions_are_scoped_to_the_project` | 未知项目 404（不泄露存在性），已存在的其他项目返回空列表 |

后端回归：本地 **133/133**。

### A02 仍未完成

- 多 Owner/Workspace 管理接口（现为单 Owner 部署）。
- 主规格的非 6 秒时长边界。
- 目标合同的 `scale` / `sensor_width_mm` 放开（需要带版本号的 Schema 迁移）。
- 前端尚未暴露相机轨迹/位姿/场景编辑控件。

---

## 2026-09-12 追加：时长边界 5–8 秒（A02 增量）

上一节第 2 条已处理。主规划第 312 行明确 V1 约束：**3 个 Shot，总时长 5–8 秒，固定帧率 24**（即 120–192 帧），因此按文档实现，未自行发明数值。

### 改动

原先运行时把 6 秒写死在多处：请求模型 `Literal[6]`、镜头总帧数校验 144、模板计划按 48/48/48 切分、Blender 参数写死 `--frames 144`、渲染进度按 144 计算、成片时长校验写死 `5.8–6.2`。现在全部改为跟随计划的 `output.duration_seconds`：

- `OutputSpec.duration_seconds` 与 `PlanRequest` / `PlanUpdate` 接受 `5|6|7|8`；
- 镜头总帧数必须等于 `24 × duration_seconds`（`PlanUpdate` 校验并按计划时长给出可读错误）；
- 模板计划按目标总帧数三段均分（余数给最后一段），每段 ≥24 帧；
- Blender 帧数、进度分母、成片时长校验（容差 ±0.2 秒）都取自计划；
- AI 导演按请求时长缩放镜头组合；
- 图片预演合同 Schema 的 `duration_seconds` 从 `const 6` 改为 `enum [5,6,7,8]` 并写明依据。

### 验证

| 范围 | 结果 |
| --- | --- |
| `tests/test_duration_boundary.py` | 6 项：5–8 接受、4/9/10 拒绝、帧数必须匹配时长、`frame_count` 跟随时长、导演按请求时长缩放、模板计划按请求时长切分、PATCH 拒绝不匹配的帧数 |
| 后端回归 | 本地与云端 **139/139** |
| **云端真实渲染（5 秒）** | 计划 40/40/40 = **120 帧**；作业 `SUCCEEDED`（51.9 秒）；ffprobe 实测 **1080×1920 / 120 帧 / 5.000000 秒** |

### A02 仍未完成

- 多 Owner/Workspace 管理接口（现为单 Owner 部署）。
- 目标合同的 `scale` / `sensor_width_mm` 放开（需要带版本号的 Schema 迁移）。
- 前端尚未暴露相机轨迹/位姿/场景编辑控件。
- 前端时长选择器仍是固定文案"6 秒"（API 已支持 5–8 秒）。
