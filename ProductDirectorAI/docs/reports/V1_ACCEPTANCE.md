# V1 验收报告

验收日期：2026-09-10
结论：PASS（需用户用真实产品素材做业务复验）

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

## 工程验收

- 前端生产构建：PASS，4,574 modules transformed。
- Sites worker 回归：PASS，4/4。
- 图片端到端：PASS。
- GLB 端到端：PASS。
- 视觉 QA：PASS，详见 `../../design-qa.md`。
- 控制台交互自动化：未使用；浏览器页面已实际渲染，核心接口与真实产物通过端到端测试。

## 已知限制

- V1 是单机单用户原型，不提供账号、权限、云同步和多人协作。
- 任务执行器为本机后台任务，不是跨进程可靠队列；服务重启时正在运行的任务需要重新提交。
- 图片预演为二维推近/平移；真实 3D 运动要求 GLB。
- Blender V1 使用统一工作室灯光和固定三段镜头，尚未接入 AI、ComfyUI 或 MiniMax。
- 大型复杂 GLB 的材质兼容性和渲染耗时需用用户真实模型继续验证。

## 阶段退出条件

- V1 范围已完成并停止扩展。
- 用户可提供第一批真实产品图片/GLB 做业务复验。
- 用户提供 API 后，仍需明确授权 V2，才开始 Provider 接入；密钥只进入后端凭证存储。
