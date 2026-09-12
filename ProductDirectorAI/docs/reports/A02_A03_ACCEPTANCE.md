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
