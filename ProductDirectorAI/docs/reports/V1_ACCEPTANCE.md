# V1 验收报告

验收日期：2026-09-10
当前总状态：**PARTIAL（历史核心技术证据，不是完整 V1 负责人验收）**

下表的 PASS 是旧电脑、当时版本和所列夹具范围内的历史结果。它不证明新电脑、用户真实素材、云端节点、完整主规划退出门或当前未复跑链路已经通过。当前差距见 `../V1_IMPLEMENTATION_GAPS.md`。

## 功能验收

| 能力 | 结果 | 说明 |
|---|---|---|
| 任意产品图片上传 | PASS | PNG/JPEG/WebP，25 MB 上限 |
| 任意产品 GLB 上传 | PASS | 500 MB 上限，浏览器 Three.js 预览 |
| 模板导演 | PASS | 三镜头、6 秒、9:16、24 fps |
| 分镜编辑与确认 | PASS | 镜头名称与机位写回后端后才可执行 |
| 图片预演 | PASS | FFmpeg 真实编码，不声称真实 3D |
| GLB 预演 | PASS | Blender Headless 真实导入、归一化、相机动画和帧渲染 |
| 任务可追踪 | PASS | QUEUED/RUNNING/CANCEL_REQUESTED/CANCELLED/FAILED/SUCCEEDED |
| 产物下载 | PASS | MP4 与 JSON Manifest |
| 本机恢复 | PASS | 素材、计划、任务保存在 SQLite 与 `var/` |
| MiniMax 凭证 | PASS | 中国区端点认证通过；Windows DPAPI 加密，本机数据库中无明文标记 |
| MiniMax 文本生成 | LIMITED | 官方返回 429 / 2056：Token Plan 用量已达上限 |

## 工程验收

以下也是验收日的历史记录；不得无复跑证据改写为当前环境 PASS。

- 前端生产构建：PASS，4,574 modules transformed。
- Sites worker 回归：PASS，4/4。
- 图片端到端：PASS。
- GLB 端到端：PASS。
- 视觉 QA：PASS，详见 `../../design-qa.md`。
- 设置界面：PASS，实际输入、保存、认证、生成测试；浏览器 console error/warning 为 0。
- 控制台交互自动化：未使用；浏览器页面已实际渲染，核心接口与真实产物通过端到端测试。

## 已知限制

- V1 是单机单用户原型，不提供账号、权限、云同步和多人协作。
- 任务执行器为本机后台任务，不是跨进程可靠队列；服务重启时正在运行的任务需要重新提交。
- 图片预演为二维推近/平移；真实 3D 运动要求 GLB。
- 历史本机验收时 Blender V1 使用统一工作室灯光和固定三段镜头；2026-09-11 云端已补充相机/焦距/时长语义验收，见 `V1_DIRECTORPLAN_ACCEPTANCE.md`。MiniMax 尚未参与 DirectorPlan，ComfyUI 尚未接入 ProductDirectorAI。
- MiniMax 当前 Token Plan 需要补充额度后才能通过文本生成测试。
- 大型复杂 GLB 的材质兼容性和渲染耗时需用用户真实模型继续验证。

## 阶段退出条件

- V1 本机核心原型已形成，但主规划完整范围尚未达到退出条件。
- 仍需新电脑环境复验、用户真实产品业务复验，并关闭差距文档列出的必需 V1 项。
- 用户提供 API 后，仍需明确授权 V2，才开始 Provider 接入；密钥只进入后端凭证存储。
