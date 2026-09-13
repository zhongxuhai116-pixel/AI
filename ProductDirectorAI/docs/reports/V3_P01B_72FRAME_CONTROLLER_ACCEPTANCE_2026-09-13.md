# V3 P0.1b 真实 72 帧：控制端验收

日期：2026-09-13。结论：**72 帧、540×960 的 Strict 数据产出切片 PASS；Depth/Normal/Product ID/颜色空间语义 NOT_VERIFIED，完整 P0.1 和 V3 仍 IN_PROGRESS。** 实际产物保存在云端隔离目录 `/home/ubuntu/pd-v3-p01b-72f-probe-20260912-01/`，不提交中间帧到 Git。

## 真实执行与独立复验

- 首次运行误用了云端旧渲染脚本，只有 `DIRECTOR_MASK frames=72`，缺少 `product/` 和独立 `mask/`；控制端发现后要求保留失败目录，不能计入通过。第二次在新目录 `render-540x960-shot01-72f-v2/` 使用本地 HEAD 的 `render_product.py` 副本。两份脚本逐行对照仅差一个结尾空行。
- 第二次 Blender 5.2.1 以 CPU 软件 OpenGL 渲染 72 帧，日志含 `DIRECTOR_PASS_UNCHANGED files=288` 与 `DIRECTOR_STRICT_LAYERS ... frames=72 color=RGBA`。未把 GPU 渲染或目标 1080×1920 声称为通过。
- 控制端在云端逐文件重算 `p01b_ledger.json` 的 **576 项 SHA256，0 项不匹配**。独立用 OpenEXR 解码全部 **288 个 EXR**，Beauty RGBA、Alpha 标量、Depth 标量、Normal XYZ 的通道、960×540 数组形状与有限值 **288/288 通过**。
- 控制端独立读取 72 对 display/product PNG：Alpha 逐像素相同，RGB 共 129 个像素有差异；最大单通道差 4/255 只作额外诊断。产品 Alpha 内腐蚀 2px 的核心区最差归一化 MAE 为 `8.5205185e-06`，低于主规划 §9.11 的 `1/255`；Alpha EXR 对 product PNG 的最大差为 `0.001966529`，低于 `1/255`。与开发端验证器结论一致，数值因核心区域取法略异。
- 开发端云端 8/8 测试与缺帧、篡改核心区、损坏 EXR、mask 越界负例已有记录；控制端独立复验了真实正例、完整帧集和哈希，负例代码仍需在仓库可移植版本中复跑。

## 阶段门与代码审查

- `configure_passes()` 按导入顺序给 `pass_index` 赋值，不能把数字索引直接视作稳定产品身份。Depth 米单位/距离定义、Normal 坐标空间，以及 EXR scene-linear 与 AgX display PNG 的跨空间语义均无现场校准，保持 `NOT_VERIFIED`。
- 云端初版验证器仅因 metadata 字段非空就把语义标 `VERIFIED`；修订版又仅凭任意校准文件的 hash 绑定升格。控制端均退回：文件完整性不等于语义正确。最终版只将绑定证据标为 `EVIDENCE_BOUND`，`semantic_verified=false`，`--require-semantics` 在缺真正内容校准器/审批时 fail-closed。测试已去除云端固定路径，脚本并入仓库；控制端在 Windows 项目 `.venv` 跑新旧探针测试 **16/16 PASS**，前端 build 与 Sites **4/4 PASS**。
- 仍未覆盖 1080×1920/Cycles、独立 H3 背景、阴影/反射/遮挡层、两类产品×三镜头×两背景、完整 QA 与人工审批。`release_eligible=false` 保持。

下一步：建立与实际渲染绑定的产品身份、Depth/Normal 和颜色空间校准证据，之后进入真实背景与分层合成。此报告只接受 72 帧数据切片，不宣布完整 P0.1 或 V3 PASS。
