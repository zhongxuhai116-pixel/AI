# V2 · AI 导演：描述 → 合法可编辑计划

日期：2026-09-12。状态：**IN_PROGRESS**（本地规则生成器与校验/修复链路已通过 V2 样本验收；LLM 路径已接线但未调用真实付费 API）。

## 1. 目标与验收标准

主规划 V2 要求"描述生成合法可编辑计划"，验收样本为 20 条描述，其中 10 条可执行样本**至少 9 条在两次修复内**得到可执行计划；**所有真正入队计划必须通过服务端校验**。

## 2. 实现

`apps/api/productdirector_api/director.py`：

| 组件 | 作用 |
| --- | --- |
| `rule_based_plan()` | 本地生成器：按描述关键词选择机位与时长组合（三组机位偏好、五组时长组合，均满足每段 24–144 帧且合计 144 帧） |
| `validate_candidate()` | 用运行时的 `PlanUpdate` 做真实校验，返回可读原因 |
| `_repair()` | 字段级修复：保留可用镜头，非法字段退回模板；时长不合规时按模板重分配 |
| `build_plan()` | 生成 → 校验 → 修复（`MAX_REPAIRS = 2`）→ 仍失败则退模板，并记录 `attempts` 与 `repairs` |
| `parse_llm_content()` | 从模型回复中抽取 JSON（抽取失败即报错，进入修复流程） |

接口 `POST /api/v1/director/plan` 返回 `plan`（可直接 PATCH 进计划接口）+ 溯源信息：`provider`、`attempts`、`repairs`、`rejected_candidates`、`asset_kind`、`validated`。

**Provider 策略**：默认 `rules`（本地规则，不调用收费 API）。仅当设置 `PRODUCTDIRECTOR_DIRECTOR_PROVIDER=minimax` 且已保存凭证时，才通过 `minimax_call` 走 LLM；LLM 输出非法会被同一条修复链路接管，Provider 抛错则退回模板并在 `repairs` 里留痕。

**图片不冒领 3D**：描述里出现"环绕/360"时，图片素材的对应镜头会被改写为定格，避免对静态图声明物理环绕。

## 3. 真实云端验收

在云节点通过真实 API 跑 10 条可执行样本：

```
 1 OK provider=rules attempts=1 total=144 cameras=static/dolly_in/side_track
 ...
10 OK provider=rules attempts=1 total=144 cameras=dolly_in/side_track/static
acceptance: 10/10 可执行样本在两次修复内得到合法计划（门槛 >=9）
```

10/10 全部一次通过（无修复），每份计划 3 个镜头、时长合计 144 帧、通过服务端校验。

## 4. 过程中的一个真实缺陷

新加的接口函数名 `director_plan` **遮蔽了同名模块** `director_plan`，导致生成 Manifest 时把模块当成函数调用，**所有渲染作业直接失败**。这个问题由既有的 worker 用例当场抓到（`'function' object has no attribute 'TargetContractError'`），改名 `generate_director_plan` 后恢复 115/115。

## 5. 测试

| 范围 | 结果 |
| --- | --- |
| `tests/test_director.py` | 10 项：10 条样本全部合法、图片不出现 orbit、模型可按描述使用 orbit、坏 LLM 输出在修复上限内修好、部分错误字段逐项修复、Provider 异常退模板、JSON 抽取、接口返回校验结果、素材归属校验、V2 样本门槛 |
| 全量后端回归 | 本地 **115/115**、云端 **115/115** |

## 6. 仍未完成

1. **LLM 路径未真实调用**：MiniMax 文本接口需要用户授权预算；当前只有接线与失败退回逻辑，没有真实模型样本证据。
2. **主规格的其余 V2 主线**：官方视频接口、`ProductBlenderRender` 多视图保真链路、取消语义与并发产能。
3. **前端未接**：工作台还没有"用描述生成分镜"的入口，目前通过 API 调用。
4. **修复上限是常量**：`MAX_REPAIRS = 2` 固定，未做按 Provider 区分的策略。

## 7. 后续更新（2026-09-12）

- **前端已接**（上面第 3 条已处理）：工作台「导演描述」卡片新增 **「用描述生成分镜」** 按钮，点击后调用 `/director/plan`，把校验通过的草稿**直接落成可编辑计划**（先建模板计划、再 PATCH 分镜），提示里显示 provider 与尝试次数（含修复次数）。
- **Provider 任务进入工作台**：新增「H3 生成任务」卡片，列出重建/视频任务的状态、阶段、产物状态，并对进行中的任务提供取消按钮；取消运行中任务时会原样展示 API 的能力限制说明。
- **第 1 条按用户指示重新定级**：见 [ADR-002 本地优先](../decisions/ADR-002-LOCAL-FIRST-PROVIDERS.md)——生成能力优先使用自托管 MiniMax H3，官方付费 API 降为可选；本地 LLM 的调查结论（`TextGenerate` 动态参数无法经 API 组装）也已记录。
- **真实浏览器验证**（Chrome，本机）：输入描述 → 点击按钮 → 分镜区出现三个可编辑镜头（正面推近/dolly_in、侧向观察/side_track、细节定格/static），任务卡片正常渲染，无控制台错误。

### 仍未完成

- LLM 路径仍无真实模型样本（按 ADR-002 降为可选）。
- 修复上限仍为常量 `MAX_REPAIRS = 2`。
