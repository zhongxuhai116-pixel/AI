# ProductDirectorAI 执行约束

## 当前授权

- 当前只实现并维护 V1。除非用户明确解锁，不进入 V2–V6。
- 产品类别必须保持通用。拳击靶只允许作为可替换演示素材，禁止把其名称、外形、文案或镜头规则写成业务默认假设。
- V1 接受 PNG、JPEG、WebP 和 GLB。图片走 2D 推近/平移；只有 GLB 可声明真实 3D 环绕。
- 当前 V1 总状态为 PARTIAL；历史局部 PASS 不等于负责人验收、用户真实素材复验或新电脑复验。
- 当前优先事项是文档/GitHub 交接与新电脑恢复。云服务器排查和部署暂停；V2–V6 未授权。

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
5. V1 验收后停止，等待用户提供 API 与明确授权 V2。

不要把 Windows DPAPI 加密的旧数据库当成可跨电脑凭证备份。不要提交 `var/`、`.venv/`、模型、真实成果或密钥。ComfyUI 验证属于 V2 或单独授权，不是 V1 云 Blender 的退出条件。
