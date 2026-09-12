# V3 背景 Worker 串联：控制端复验

日期：2026-09-12。判定：**离线控制流代码切片 PASS；真实 Blender/H3 背景端到端 NOT_TESTED，V3-03 IN_PROGRESS**。

Strict Run 现在可冻结背景工作流和来源模式，同一持租约 Worker 顺序执行受控 Blender 产品通道、背景 Producer 与 Strict 合成。独立工作流缺现场文件直接失败，不会自动回退预置目录；显式受控导入仅使用 Job 专属目录，证据注明不是生成来源、仅作不可发布验证样本。

控制端独立执行：

- `python -X utf8 -m unittest tests.test_v3_background_integration -v`：4/4 PASS。
- `python -X utf8 -m unittest discover -s tests -p 'test_*.py'`：247/247 PASS。
- `git diff --check`：通过；仅有 LF/CRLF 工作树提示。

关键限制：正例模拟 Blender，并由测试夹具补产品层与合成 Mask；真实 Blender 尚未自动落盘这些层。仓库也没有已核实的独立 ComfyUI 背景工作流及节点/模型版本、真实请求和产物证据。测试证明离线拒绝与控制路径，不证明现场真实 Producer。Job 保持 `VERIFICATION_PASSED`、`fidelity_complete=false`、`release_eligible=false`，V3 阶段门未通过。
