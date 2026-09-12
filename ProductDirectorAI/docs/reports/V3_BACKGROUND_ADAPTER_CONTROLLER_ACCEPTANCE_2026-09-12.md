# V3 背景适配模块：控制端复验

日期：2026-09-12。判定：**独立适配模块代码切片 PASS；实际受控背景 Producer 未通过**。

模块提供冻结 Run 工作流与批准项的独立比较、受控目录导入与隔离检查、ComfyUI 提交/轮询/帧收集适配、逐帧哈希证据，并保持全幅 H3 视频提取模式失败关闭。控制端退回并复核了工作流自比较/缺失回退、重复帧覆盖、失败半成品写盘、低透明度边缘覆盖和不安全保护映射。

控制端独立执行：

- `python -X utf8 -m unittest tests.test_strict_background -v`：16/16 PASS。
- `python -X utf8 -m unittest discover -s tests -p 'test_*.py'`：243/243 PASS。
- `git diff --check`：通过；仅有 LF/CRLF 工作树提示。

**范围限制**：`strict_background.py` 尚未由 `main.execute_claimed_job` 调用；测试使用模拟 ComfyUI 与本地受控目录。仓库没有已核实的独立背景工作流文件和真实生成过程证据。受控目录导入只证明本地隔离与文件合同，不能替代实际 H3 生产来源。当前 `VERIFICATION_PASSED`、`fidelity_complete=false`、`release_eligible=false` 不变。V3-03 和 V3 整阶段仍 `IN_PROGRESS`。
