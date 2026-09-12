# V3 Blender 5 合成器隔离：控制端验收

日期：2026-09-12。结论：**合成器隔离代码切片 PASS；真实 Blender/H3 来源 NOT_VERIFIED；V3 仍 IN_PROGRESS。**

## 独立证据

- 检查 `blender/scripts/render_product.py`：第二遍前必须实际解绑 Blender 5 `compositing_node_group`；解绑失败在旧帧清理和渲染前拒绝，并尝试恢复 scene 状态。第二遍后比较 Pass 文件的 mtime、size、SHA256，发现改写即失败。
- 控制端复跑 `tests.test_render_product_compositor_isolation tests.test_render_product_strict_contract tests.test_strict_background`：31/31 PASS，包含 setter 被忽略、直接报错、旧产物保持和渲染不启动的故障注入。
- DeepSeek 完整后端回归：280/280 PASS，182.830s；控制端 `git diff --check` 无错误。
- 本地归档的整帧 H3/产品替换工作流被拒绝作为 Strict 独立背景来源；历史云端 H3 graph 含参考视频和参考图，只能归类历史样本。

## 未通过的阶段门

- 未在真实 Blender 5.2.1 上产生新的同源 Beauty/Alpha/Depth/Normal/product/mask 并验证首遍 Pass 不被第二遍覆盖。
- 未核实独立 background-only H3 工作流、节点/模型版本 hash、真实请求和产物。
- 若第二遍已渲染而复制 mask 时失败，可能留下部分新文件；没有成功标记或发布资格，但原子产物交付仍需加固。

因此只接受代码修复，不提升 `VERIFICATION_PASSED`、`fidelity_complete=false`、`release_eligible=false`，也不宣布 V3 PASS。
