# V3 镜头级 Strict 分层合同：控制端复验

日期：2026-09-12。判定：**代码合同切片 PASS；V3-03 与 V3 全阶段仍 IN_PROGRESS**。

验收范围：冻结分镜逐镜头声明必需阴影、反射、遮挡层；策略 `allowed_operations` 仅代表许可；必需层按镜头帧范围覆盖；非必需层可不存在或只提供全片范围内的部分帧；提供的帧仍逐帧校验；未获许可的层、必需段缺帧和越界帧被拒绝。注册与 Strict 合成均执行对应约束。

控制端独立执行：

- `python -X utf8 -m unittest tests.test_strict_composite tests.test_v3_strict_runtime tests.test_contract_parity -q`：61/61 PASS。
- `python -X utf8 -m unittest discover -s tests -p 'test_*.py'`：227/227 PASS。
- `git diff --check`：通过；仅有 LF/CRLF 工作树提示。

控制端先后退回了两处误拒：必需层在非必需镜头出现合法可选帧，以及全程仅允许的层只提供部分帧。最终测试分别覆盖混合镜头正例、纯可选部分帧正例、必需段缺帧、越出冻结全片范围与缺 Alpha 负例。

本批没有实际受控 H3/ComfyUI 背景进程证据、真实 Blender 五通道新产物或完整发布 QA/人工批准。成功结果仍为 `VERIFICATION_PASSED` 且 `release_eligible=false`。前端本批未修改，未重复执行构建。
