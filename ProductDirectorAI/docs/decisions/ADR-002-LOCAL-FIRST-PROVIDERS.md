# ADR-002：生成能力本地优先（自托管 MiniMax H3）

日期：2026-09-12。状态：**已采纳**（用户指示）。

## 背景

V2 原计划要求验证 MiniMax **官方付费 API**（视频与文本）。用户明确指示：**优先使用本地的 MiniMax**——即云节点上已部署的自托管 MiniMax H3（ComfyUI）环境，而不是官方付费接口。

此前项目已按"不调用收费 API、不自动购买资源"的约束运行；本次是把这条约束固化为决策。

## 决策

1. **视频生成**：只使用自托管 H3 环境（本 Provider 已实现：提交、轮询、产物回收、取消、队列视图）。不调用官方视频接口。
2. **文本/导演**：默认使用**本地规则生成器**（`director.py` 的 `rule_based_plan`），它不依赖任何外部服务；服务端校验与最多两次修复是必经步骤。
3. **官方 API**：从"必须验证项"降为**可选/延后**。若将来要启用，只需配置 `PRODUCTDIRECTOR_DIRECTOR_PROVIDER=minimax` 并保存凭证，代码路径已就位（`build_director_llm()`）。
4. **不为此下载新模型**：云节点已约 46GB 模型，磁盘与单卡产能有限；除非出现明确的、无法用现有环境满足的需求，不新增模型下载。

## 关于"用本地模型做导演"的调查结论

现场确实存在文本生成的潜在条件：

- ComfyUI 有 `TextGenerate` 节点（`comfy_extras/nodes_textgen.py`），接收 `CLIP` 并调用 `clip.generate()`；
- H3 的文本编码器 `qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors`（Qwen3-VL 32B）已在本地，`CLIPLoader` 支持 `type="minimax"`；
- `comfy/sd.py` 中确实存在 `generate()` 实现。

但**通过 API 组装该节点不成立**：`TextGenerate` 的 `sampling_mode` 是 V3 DynamicCombo 输入，实测四种编码都失败——

| 编码方式 | 结果 |
| --- | --- |
| `"sampling_mode": "on"` + 点号子键（`sampling_mode.temperature`） | 校验拒绝 |
| `"sampling_mode": {"on": {...}}` | 校验通过，运行时报 `execute() missing 1 required positional argument: 'sampling_mode'` |
| `"sampling_mode": {"sampling_mode": "on", "temperature": ...}` | 同样运行时报缺参 |
| `"sampling_mode": {"key": "on", ...}` | 同样运行时报缺参 |
| 顶层平铺（`"sampling_mode": "on", "temperature": ...`） | 校验拒绝 |

结论：**当前 H3 栈没有可经 API 直接使用的本地文本 LLM**。要继续这条路需要逆向该节点的序列化格式（并确认 minimax CLIP 包装器支持自回归生成），成本高于收益；本决策选择先不投入。

## 影响

- 不产生 API 费用；导演功能在没有外部服务时依然可用（规则生成 + 服务端校验）。
- 导演的"创意质量"目前依赖规则模板；若用户后续希望接入本地 LLM（例如另装 ollama），只需实现一个返回 JSON 计划草稿的函数并传入 `build_plan(llm=...)`。
- 评测口径：V2 的"描述生成合法可编辑计划"用本地规则生成器验收（已达成 10/10），**不**记为"LLM 路径通过"。
