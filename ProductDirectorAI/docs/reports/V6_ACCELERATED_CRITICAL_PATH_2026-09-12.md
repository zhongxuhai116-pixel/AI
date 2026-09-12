# V6 加速关键路径（基于当前工作树真实状态）

日期：2026-09-12。目标：保持主规划 V1–V6 范围与真实验收门槛不变，把后续从零散小补丁
改成可独立验收的端到端功能包。任何包都不替代真机证据，也不提前宣布 V6 完成。

## 当前可信基线

- V1/V2：用户 ACCEPTED；V1/V2 legacy 路径在严格路径改造中持续回归。
- V3-01：产品版本/策略/审核与 Run 冻结绑定已有代码与 API/DB 测试；是切片，不是阶段 PASS。
- V3-02/03：Strict 五通道校验、产品/mask 第二遍渲染、来源注册、Worker 串联、
  compositor isolation、display-linear 色彩合同均已落地代码与离线正负例。
- V3-04：QA 阈值集、问题定位、hash 绑定与发布资格门已有代码和 API 测试；部分视觉指标
  仍 NOT_VERIFIED。
- 本轮后端回归：300/300 PASS；Strict 合成 23/23 PASS；前端 build/Sites 本端沙箱 EPERM，
  控制端标准环境复验。

## V3 最少完整纵向链路

以下是最小可证明 V3 纵向闭环，不允许用 mock/preset 冒充真实通过：

1. 一个真实产品 GLB、一个明确分镜，Blender 5.2.1 CPU 产出 production-spec 五通道 +
   `product` RGBA + 连续 `mask`，逐帧 hash 注册，证明 Beauty/Alpha/Display/Product 同帧合同。
2. 一个经核实的独立 background-only 工作流（真实 graph/节点/模型 hash、无产品参考），
   在批准尺寸/帧数下产出可溯源背景；产品保护区经连续 Alpha/Mask 验证不被覆盖。
3. 该真实 Run 经 Strict display-linear 合成 + 五通道/图层/来源/QA 运行后复核。
4. 一版真实人工 review/decision 绑定 QA report、manifest 与逐文件 hash；旧批准因任意
   输入/产物/阈值变更失效。
5. `release_eligible` 保持 false，直至 V3-07 两类产品×3 镜头×2 背景及回归/性能/存储全过。

预计现场总工时可压到 24–40 小时，但真实 Blender/独立 H3 工作流、GPU/CPU 调度和人工审核
是关键外部依赖；这些时间不能由代码加速消除。

## 后续端到端功能包

### P0 V3 真实纵向闭环

前置：当前 Strict runtime/QA/色彩合同代码与一次真实 Blender 授权。
可并行准备：冻结 plan/source manifest 字段、逐文件 hash 合同、QA 阈值版本、发布门。
外部依赖：现场 Blender CPU/GPU、已核实独立 background workflow、授权素材、人工审核。
预计：每项 8–16 小时，总计约 40–60 小时；未闭环前继续 `VERIFICATION_PASSED`/
`release_eligible=false`。

- P0.1 真实 Blender production probe：目标规格多帧、Pass 不变、product/mask 原子切换、
  颜色合同实测。
- P0.2 独立 background-only producer：真实 graph schema/节点/model hash、无产品参考、
  产品区隔离；不把 V2 全帧 H3 冒充背景。
- P0.3 真实 shadow/reflection/occlusion 来源与逐镜头必需/可选帧合同。
- P0.4 真实 QA + 人工批准 E2E：阈值冻结、最新报告有效性、篡改失效、发布门负例。
- P0.5 V3-07 验收矩阵：两类产品、每类 3 镜头、2 背景，旧版回归、性能/存储证据。

### P1 可并行准备的 V4 合同/API/测试夹具

前置：V3 真实来源闭环后可验收；代码可先做纯合同/负例，不宣称 V4 PASS。
预计：6–10 小时/包。

- P1.1 人物/动作/Proxy 版本与交互 schema：anchors、IK/时序/碰撞阈值、DB 迁移。
- P1.2 时序偏差/穿透/接触距离验证器：离线合成帧序列夹具与失败定位。
- P1.3 V4 修订 UI 数据合同：遮挡、可见产品保真、真人素材来源字段。

### P2 可并行准备的 V5 合同/API/测试夹具

前置：授权参考片上传与代理时间戳；真实参考片需要用户授权。
预计：6–10 小时/包。

- P2.1 授权参考上传/来源/代理时间戳 API 与文件 hash 合同。
- P2.2 硬切检测与 ±0.2s 对齐夹具；F1 阈值冻结。
- P2.3 双栏时间线映射与单镜头/总长帧误差验证器。

### P3 可并行准备的 V6-A 合同/数据/API/测试夹具

预计：8–12 小时/包。

- P3.1 六个 Profile、es-MX locale、TTS/字幕/BGM/ducking/响度 contract 与静态校验。
- P3.2 Batch、封面、Package manifest schema、ZIP 解包校验夹具。
- P3.3 发布包不可继承、文件 role/hash/size/mime 与许可引用测试。

### P4 可并行准备的 V6-B 合同/数据/API/测试夹具

预计：8–12 小时/包。

- P4.1 预算预留/对账 ledger schema 与负例。
- P4.2 Automation scope/幂等/限流、签名 Webhook、Outbox/死信 schema 与 dry-run 夹具。
- P4.3 100 项/2 Worker/10 项故障恢复压测夹具与 p95/UI 事件延迟采集点。

### P5 可并行准备的 V6-C 合同/数据/API/测试夹具

预计：6–10 小时/包；真实发布仍必须四个平台明确授权。

- P5.1 OAuth/审批/发布框架接口与状态机，无凭证 dry-run。
- P5.2 TikTok/YouTube/Instagram/Facebook Page 连接器 adapter contract 与错误映射。
- P5.3 多平台部分失败/未知对账数据模型和负例。

### P6 可并行准备的权限/运维/证据包

预计：4–8 小时/包。

- P6.1 Editor/Reviewer/Publisher/Owner RBAC 与审计日志 schema。
- P6.2 OpenAPI、运行/部署/备份恢复手册、示例包和验收证据清单。

## 执行顺序建议

- 本批收尾后先做 P0.1，因为它是 P0.2–P0.5 和 V3 纵向验收的唯一入口。
- P1–P6 中只有 schema/API/测试夹具可提前；凡依赖真实素材/账号/模型/GPU 的验收项，
  只能准备执行器与 fail-closed 负例，不能标记 PASS。
- 每包交付包含：真实代码路径、正负例、实际命令输出、已知限制和下一包前置条件；
  不合并“仅 mock 证明”的包。

## 不降级声明

主规划 §9.5 只要求线性空间合成与 Manifest 记录显示变换；本批选 display-linear 合同 A，
不把 AgX display PNG 标成 scene-linear。V3-03 真实来源/分层/QA/人工批准未闭环前，
保持 `release_eligible=false`，不提前开启发布。
