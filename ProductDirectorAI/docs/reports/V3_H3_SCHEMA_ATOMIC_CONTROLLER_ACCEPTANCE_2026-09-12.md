# V3 H3 候选图与原子产物：控制端验收

日期：2026-09-12。判定：**原子回滚及 H3 schema 代码切片 PASS；真实 Blender/H3 运行 NOT_VERIFIED；V3 仍 IN_PROGRESS。**

## 已验范围

- 控制端审查 `render_product.py` 的暂存、备份与回滚逻辑；针对当前 pair 双重 rename 失败、前序 pair 回滚失败、原目标不存在三种边界，旧备份保留、异常报告恢复路径或清除部分新目标。
- 控制端复跑 `tests.test_render_product_compositor_isolation tests.test_strict_background`：34/34 PASS。此前标准环境完整后端回归 283/283 PASS；DeepSeek 对最终补充测试后的完整回归 289/289 PASS，186.296s。
- 云端 `/object_info` 原始节点快照与模型/节点源码哈希已保存于 `var/acceptance/2026-09-12-v3-background-h3-schema-probe/`。控制端独立计算快照原始文件 SHA256 为 `93178341812c01ef1ea5033291319969aa0587473858a5dcdf23ab0fad90fa89`，与 `summary.json.raw_file_sha256` 一致。规范化 JSON SHA256 是另一种计算口径，已单列。
- 候选图中 `MiniMaxH3ImageToVideo` 的无关键帧输入与链路类型符合现场 schema；代码将其标为 `SCHEMA_VERIFIED_ONLY`、运行状态 `NOT_VERIFIED`，实际 Producer 保持 fail-closed。

## 未通过阶段门

- 未见 Blender 5.2.1 当前脚本真实 Strict 一帧产物及 Pass 未被第二遍覆盖的现场证据。
- 未见候选 H3 无参考背景图的真实生成、质量检查及 provenance。模型文件哈希只证明所指文件身份，不证明 prompt-only 生成能力。
- 因此不得将候选 graph 作为可发布 Strict 背景来源，`release_eligible=false` 不变。
