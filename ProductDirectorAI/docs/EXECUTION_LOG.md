# V1 执行记录

## 2026-09-10

- V1-00：安装并验证 Blender 5.2.1 LTS、FFmpeg 9.0.1、Python/FastAPI、Node/Vite。
- V1-01：建立 React 工作台及参考 V6 图的信息架构；V1 只显示当前阶段功能。
- V1-02：实现通用 PNG/JPEG/WebP/GLB 上传、哈希、大小限制、本地素材存储。
- V1-03：实现固定 6 秒、24 fps、9:16 的三镜头模板 DirectorPlan。
- V1-04：实现分镜编辑、PATCH 持久化与显式批准。
- V1-05：实现 GLB 浏览器预览、Blender 模型归一化、推近/侧移/环绕三段相机动画。
- V1-06：实现图片 FFmpeg 预演，明确不把图片描述为真实三维环绕。
- V1-07：实现 SQLite 作业队列、真实状态、阶段、进度、取消与错误记录。
- V1-08：实现 MP4 与 Manifest 下载、产物存在性和大小检查。
- V1-09：完成图片与 GLB 端到端验收、前端构建、Worker 测试和视觉对照。
- 用户增量：在设置页加入 MiniMax 中国区 Provider 配置；密钥通过 Windows DPAPI 加密保存。认证与模型列表测试通过，最小文本生成返回 429 / 2056（Token Plan 用量上限）。
- 用户增量：核对优云智算创建页与官方计费，确认截图中的单卡 RTX 5090 32GB、14C64GB、按量计费适合 V1；要求系统盘至少 100GB，ComfyUI/视频建议 200GB 或独立云盘。
- 用户增量：增加 `docs/GPU_CLOUD_SELECTION.md` 与设置页 GPU 选型卡。主平台建议优云智算，AutoDL 作为低成本开发/备用；V1 不调用云平台 API、不创建实例、不产生费用。

## 关键决定

- 拳击产品仅为演示文件；默认文案、API、数据模型与渲染逻辑不绑定产品类别。
- MiniMax V1 仅做安全配置和连通测试；生成式 DirectorPlan Provider 编排留到 V2。
- 本机优先：素材、SQLite、帧、视频和清单全部保存在 `var/`，该目录不提交 Git。
