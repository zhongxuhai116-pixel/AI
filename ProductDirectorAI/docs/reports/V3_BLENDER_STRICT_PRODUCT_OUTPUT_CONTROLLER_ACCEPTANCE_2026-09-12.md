# V3 Blender Strict 产品层控制端验收

日期：2026-09-12。判定：**离线代码合同切片 PASS；真实 Blender 五通道与产品层一致性 NOT_TESTED；V3 仍 IN_PROGRESS。**

## 独立复验

- `tests.test_render_product_strict_contract`：5/5 PASS。测试只检查源码结构，不调用 `bpy`。
- 缺产品层/Mask 的受控渲染故障测试：1/1 PASS，Job 为 `QA_REJECTED`。
- 标准环境全量命令 `python -X utf8 -m unittest discover -s tests -p 'test_*.py' -q`：253/253 PASS，171.574 秒。首次并发修改期间的 44 项回归有 1 项断言不符，开发端修正后专项 44/44 PASS，控制端标准环境全量亦 PASS。
- `git diff --check` 无格式错误；仅有 Git 换行提示。

## 本批代码合同

`--passes` 首遍隐藏非产品 Mesh、开启透明 RGBA；第二遍按同一产品隔离语义写 `strict/product`，同帧复制到 `strict/mask` 和 `strict/passes/mask`。帧数不足时失败。非 `--passes` 路径保留原有不透明 RGB 与 Studio Floor。受控 Worker 缺产品/Mask 的模拟输入拒绝。

## 尚未签收的现场证据

本机未发现 Blender 命令。上述测试不能证明实际 Blender 生成的 EXR Beauty/Alpha 与 PNG product/mask 逐像素一致，也未覆盖透明边缘、黑色材质、Logo、反射及云端材质兼容。真实 H3/ComfyUI 独立背景来源、分层产物与发布 QA 亦未通过现场验收。Job 仍须保持 `VERIFICATION_PASSED`、`fidelity_complete=false`、`release_eligible=false`，不能把本批 PASS 解释成 V3 整阶段或发布 PASS。
