# V3-04 QA 控制端验收

日期：2026-09-12。判定：**QA 控制流与拒绝规则代码切片 PASS；完整 V3-04 现场保真 QA NOT_VERIFIED，V3 阶段 IN_PROGRESS。**

## 控制端独立复验

- QA 引擎与 API 专项：16/16 PASS，覆盖缺 Beauty、伪造 Alpha、错误通道、资产 hash、阈值放宽、产品/Mask/背景/合成帧/MP4/Manifest 篡改、重新 QA 撤销旧批准及发布资格。
- 标准环境全量后端：`python -X utf8 -m unittest discover -s tests -p 'test_*.py' -q`，269/269 PASS，198.208 秒。
- 前端 `npm run build` PASS；Sites 测试 4/4 PASS。
- `git diff --check` 无格式错误，仅 Git 换行提示。

## 可签收范围

阈值集按版本冻结，当前拒绝比基线更宽松或越界的值。QA 读取文件计算帧级指标，缺可信 Beauty/Alpha、异常遮罩和资产 hash 不符会拒绝；无法证实的产品身份、已核实尺寸和成片质量保持 `NOT_VERIFIED`，不可批准。人工批准绑定 manifest、QA 报告与冻结输入；读取和发布门动态判断旧批准是否仍有效。Strict 输入来源不可信时发布资格为 false。

## 不可据此宣称的事项

API 批准正例用模拟 `PASS_QA` 测试绑定流程，不能证明真实 Blender/H3 全链 QA 达标。产品 ID、尺寸依据、Logo 真实标注与素材、编码后可读性等尚缺现场可信证据，故真实 Strict QA 仍不能 PASS。两类必需产品各 3 镜头/2 背景、透明反射边界和本地/云一致性未验。阈值放宽的 ADR/质量报告审批流程尚未实现，当前只允许不宽于基线。不得将本批代码切片签收升级为 V3 整体或可发布资格。
