# ProductDirectorAI 执行约束

## 当前授权

- 用户已于 2026-09-10 明确解锁继续执行到 V6，但必须按 V1 → V2 → … → V6 顺序施工；每一阶段先完成证据化验收，再进入下一阶段。
- 产品类别必须保持通用。拳击靶只允许作为可替换演示素材，禁止把其名称、外形、文案或镜头规则写成业务默认假设。
- V1 接受 PNG、JPEG、WebP 和 GLB。图片走 2D 推近/平移；只有 GLB 可声明真实 3D 环绕。
- 当前 V1 总状态为 PARTIAL；新云节点的安装、GPU 渲染、通用 GLB 端到端链路和 DirectorPlan 相机语义已 PASS，但不等于全部产品合同、可靠任务、鉴权或用户真实素材验收。
- 当前优先事项是补齐 V1 产品差距并形成阶段门；不得因为 V2–V6 已解锁而跳过阶段验收。

## 安全与真实性

- 不把 API key、token、cookie 或账号信息提交到 Git、前端、日志、Manifest 或发布包。
- 不用静态截图、占位视频或假进度冒充 Blender/FFmpeg 的真实结果。
- 不自动购买服务、不调用付费 Provider、不发布到外部平台。
- 保留 `.openai/hosting.json`、`apps/web/worker/index.js`、`apps/web/scripts/prepare-sites-build.mjs` 和 `apps/web/tests/sites-worker.test.mjs`。

## 施工顺序

1. 先读 `docs/HANDOFF_NEW_COMPUTER.md`、`docs/README.md`、主规划、`docs/CURRENT_PHASE.md`、`docs/EXECUTION_LOG.md`、`docs/V1_IMPLEMENTATION_GAPS.md`、`docs/CLOUD_SERVER_HANDOFF.md` 和历史验收报告。
2. 改代码前检查工作树，保留用户改动。
3. 每次变更至少执行前端 build、Sites worker 测试和相关 API/渲染冒烟测试。
4. 变更运行环境、Provider 或阶段范围时同步更新 `docs/`。
5. 每一版本先更新阶段报告与退出证据；未达到门槛不得把后续版本标成完成。

MiniMax H3 作为 V2 首选开放权重视频模型路线。官方完整 BF16 权重与四卡示例不能当作当前单卡基线；当前节点已有一套单卡 RTX 4090 的 ComfyUI 量化/卸载验证环境，但它尚未接入 ProductDirectorAI，且当前根盘只剩约 19GB 可用。不得重复下载模型、占用正在运行的 H3 任务，或把已有独立 ComfyUI 成果标成 ProductDirectorAI V2 通过。优先实现 Provider/任务合同，再用现有受控环境完成真实集成验收。

不要把 Windows DPAPI 加密的旧数据库当成可跨电脑凭证备份。不要提交 `var/`、`.venv/`、模型、真实成果或密钥。ComfyUI 验证属于 V2 或单独授权，不是 V1 云 Blender 的退出条件。
