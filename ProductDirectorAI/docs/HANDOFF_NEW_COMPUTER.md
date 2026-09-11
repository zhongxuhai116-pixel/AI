# ProductDirectorAI 换电脑接手手册（Windows）

> 本次换电脑先读 [最新继续入口](NEXT_COMPUTER_START.md) 与 [A05 交接验收](reports/A05_SECURITY_HANDOFF.md)。下文是历史手册，其中仅授权 V1、固定 localhost、npm 阻塞等内容已由最新入口覆盖。当前代码新增登录鉴权，需按新入口配置。

更新日期：2026-09-11。源码与文档已在新 Windows 电脑恢复；V1 云基础设施与通用 GLB 全链路已通过，但完整 V1 仍为 **PARTIAL**。用户已授权逐阶段继续到 V6，必须每期验收后再前进。

## 1. Git 会带走什么

Git 只负责已跟踪的源码、合同、脚本、测试夹具和脱敏文档。它不会自动带走旧电脑的运行环境或私人数据，尤其不应提交：

- `.venv/`、`node_modules/`、构建缓存和本机 Codex runtime；
- `var/` 中的 SQLite、上传素材、帧、视频、Manifest、任务状态和 Provider 凭证；
- Blender/FFmpeg、模型权重、ComfyUI 节点/工作流及其他大型运行依赖；
- 用户真实成果、未经确认许可的素材、云登录截图；
- API key、token、cookie、密码、SSH 私钥、真实公网 IP、实例 ID 或账号。

旧电脑当前项目目录为：

```text
C:\Users\dell\Documents\Codex\2026-09-10\referenced-chatgpt-conversation-this-is-an\work\github-document-sync\ProductDirectorAI
```

旧 V1 SQLite 位于该目录下的 `var/productdirector.db`（绝对路径即在上述目录后追加 `\var\productdirector.db`）。`var/` 不进入 Git。

## 2. 新电脑克隆

先安装 Git、Python、Node.js、Blender 和 FFmpeg，并确认它们可从新 PowerShell 的 PATH 找到。推荐使用当前受支持的稳定版本；不要仅因旧报告中的版本号不同就重写项目。

在用户选择的新父目录中执行：

```powershell
git clone https://github.com/zhongxuhai116-pixel/AI.git
cd AI\ProductDirectorAI
git status
```

仓库的 Git 根目录是 `AI`，项目位于其 `ProductDirectorAI` 子目录。若实际 GitHub 默认分支或目录结构与此不同，先查看仓库再定位，不创建第二份嵌套仓库。

克隆并进入项目目录后先读：`AGENTS.md`、本手册、`docs/CURRENT_PHASE.md`、`docs/EXECUTION_LOG.md`、`docs/V1_IMPLEMENTATION_GAPS.md`、`docs/CLOUD_SERVER_HANDOFF.md` 和主规划。

如另有交付 ZIP，它应只包含 ProductDirectorAI 受版本管理的源码、全部文档/合同和参考图，并排除 ZIP 自身、`var/`、`.venv/`、`node_modules/`、秘密与真实产物。ZIP 可作离线源码备份，Git clone 仍是优先方式；两者都不包含需要安装的依赖。

## 3. 建立新环境

项目已有 `scripts/setup.ps1`，但它包含旧电脑的 Codex runtime 回退路径，且当前脚本没有逐条显式检查所有原生命令退出码。因此新电脑首次恢复建议手工执行并逐步确认结果：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\apps\api\requirements.txt
cd .\apps\web
npm ci
cd ..\..
```

`apps/web` 同时存在 npm 与 pnpm 锁文件；当前交接基线建议 `npm ci`。不要把 `pnpm-workspace.yaml` 中的说明性 `allowBuilds.esbuild` 值当成已验证配置。依赖安装失败时保留完整错误和工具版本；可在当前 V1 恢复任务内处理必要的安装或回归问题。

随后分别检查：

```powershell
python --version
node --version
npm --version
blender --version
ffmpeg -version
ffprobe -version
```

如果 Blender 未加入 PATH，可在后续维护任务中配置实际安装路径；不要照抄旧电脑 `C:\Program Files\...` 或用户目录下的 runtime 路径。

## 4. 启动与本机检查

在两个 PowerShell 窗口、项目目录内分别启动：

```powershell
.\scripts\start-api.ps1
```

```powershell
.\scripts\start-web.ps1
```

访问 `http://127.0.0.1:4173/`，API 文档为 `http://127.0.0.1:8000/docs`。前端目前固定访问本机 `127.0.0.1:8000/api/v1`；远程部署和可配置 API 基址不应假定已经实现。

先做不产生外部费用的复验：

```powershell
.\.venv\Scripts\python.exe -m compileall .\apps\api
cd .\apps\web
npm run build
npm run test:sites
```

再检查 API 健康页。可使用明确标识、可再生成的 `tests/fixtures/generic-product.glb` 做技术复验；用户真实素材另做业务复验，私密素材不要求公开。不要把旧截图、测试夹具或历史 PASS 当成新电脑复验。当前渲染器存在固定相机、42mm 镜头和 EEVEE 等实现差距，UI 编辑字段也未完整进入渲染路径，详见差距文档。

## 5. 数据与凭证迁移

默认且最安全的方案是：新电脑建立空 `var/`，重新导入用户选择的原始图片/GLB，重新生成计划与成果，并在设置页重新输入 Provider 凭证。

不要直接把旧 SQLite 当作便携备份。V1 Provider 凭证由 Windows DPAPI 按旧 Windows 用户加密；即使复制 `var/productdirector.db`，新电脑/新用户通常也不能解密。数据库还可能引用旧绝对路径、旧任务和缺失产物。迁移旧数据只有在用户明确要求、先制作私密备份、完成 Schema/路径审计和脱敏后进行；不得经 GitHub 传输。

如必须保留真实素材或成果，由用户通过其认可的私密存储手工迁移，核对许可、哈希和目录后再导入。SSH 私钥同样使用安全渠道迁移或在新电脑重新生成，绝不提交仓库。

## 6. 云服务器接续

当前新节点事实以 `docs/CLOUD_SERVER_HANDOFF.md` 为准：华北二 A、RTX 4090 24GB、约 97GB 根分区；公网 SSH、Blender 5.2.1、FFmpeg、Node 与 ProductDirectorAI 已验证，V1 通用 GLB 全链路已通过。旧节点扩容和 SSH 超时仅是历史记录，不得套用。

换电脑后从用户登录的控制台重新取得当前地址和账号，并重新验证新电脑出口与云防火墙 `/32`，不要照抄旧 IP。当前请求是暂停服务器排查/部署，先完成文档 GitHub 同步；恢复部署仍需用户明确继续，且 ComfyUI 验证属于 V2 或单独授权，不是 V1 云 Blender 的退出门。

## 7. 新电脑第一条继续指令

在新电脑选择 **SOL 5.6**，把下面内容作为第一条任务指令：

> 接手 ProductDirectorAI。先完整读取根目录 `AGENTS.md`、`docs/HANDOFF_NEW_COMPUTER.md`、`docs/README.md`、`docs/CURRENT_PHASE.md`、`docs/EXECUTION_LOG.md`、`docs/V1_IMPLEMENTATION_GAPS.md`、`docs/CLOUD_SERVER_HANDOFF.md`、`docs/reports/P0_ENVIRONMENT.md`、`docs/reports/V1_ACCEPTANCE.md`、`design-qa.md`、`contracts/README.md` 和 `ProductDirectorAI_V1-V6_Codex_Development_Plan.md`。先检查 Git 状态和新电脑环境，不覆盖用户改动，不从零重建。当前只授权 V1 维护；先复验本机 build、Sites worker 测试、API 健康和用户允许的最小素材链路，将历史 PASS 与本次结果分开记录。不要调用收费 API，不购买云资源，不进入 V2–V6。云端部署当前暂停；只有我明确要求继续时，才按云交接文档恢复。发现差距先报告并在 V1 范围内处理；完整 V1 未满足退出门前保持 PARTIAL。

这条指令不授权迁移旧数据库、调用 MiniMax、公开发布、修改云防火墙或部署 ComfyUI。
