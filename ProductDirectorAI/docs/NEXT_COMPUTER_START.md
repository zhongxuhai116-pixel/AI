# 换电脑继续入口 · 2026-09-11

> **⚠️ 本文件已被取代（2026-09-12 收尾）。请改读 [下一次操作交接说明](HANDOFF_NEXT_SESSION.md)。**
> 下文写于 2026-09-11 换电脑当天，其中"完整 V1 仍为 PARTIAL""本轮云 SSH 连接超时、未登录服务器""H3 尚未接入项目"等描述**都已过时**：V1 与 V2 已于 2026-09-12 被用户判为 ACCEPTED，云端已部署并跑通双链路出片，H3 已通过 Provider 接入项目。保留本文件只为留档历史事实与恢复命令。

这是本次交接的最新入口，覆盖旧手册中“仅授权 V1”“V2 尚未解锁”“前端固定 localhost”“npm 阻塞”等过时描述。主规划和历史报告保留，历史 PASS 不代表本次云端验收。

## 仓库与恢复

仓库：`https://github.com/zhongxuhai116-pixel/AI.git`，分支 `main`，项目目录 `ProductDirectorAI`。本轮仅同步源码、测试和脱敏文档，云端没有部署本轮改动。

```powershell
git clone https://github.com/zhongxuhai116-pixel/AI.git
cd AI/ProductDirectorAI
git log -1 --oneline
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r apps/api/requirements-test.txt
cd apps/web
npm ci
npm run build
npm run test:sites
cd ../..
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'
```

新电脑需要自行安装 Python、Node/npm、Git；真实渲染还需 Blender、FFmpeg/ffprobe。不要复制旧 `node_modules`、`.venv` 或浏览器缓存。本轮更新 npm lockfile 后，在隔离空目录用 npm 11.6.2 完成 `npm ci`（67 个包）。本机原本 npm 不在 PATH，不要求新电脑沿用本机 Codex runtime 路径。

## 当前真实状态

- V1：PARTIAL；用户已授权逐阶段执行到 V6，每期必须验收，不能跳过 V1。
- A01–A03：已有局部证据，原报告“全部完成”口径过宽。干净安装、完整合同、PostgreSQL 迁移/恢复等仍须按主规划核对。
- A04：本地租约、重领、事件续读、worker-once 合同测试通过；换电脑后已补事务内租约校验、长任务心跳续租、取消胜出与终态保护，并修复租约到期时间的秒级截断缺陷（后端 28/28）。真实杀 Worker 进程恢复、编码失败自动重试与远程任务回收仍未闭环，见 [A04 恢复加固报告](reports/A04_RECOVERY_HARDENING.md)。
- A05：新增私有单 Owner 会话、CSRF、Owner/项目/素材访问检查、Worker 令牌及身份约束、同源代理、相对存储引用、Linux Fernet 凭证加密。安全测试已通过；完整 A05 仍为 PARTIAL。
- 本次本地验证（换电脑后复跑）：后端 28/28、前端 Vite 构建、Sites 4/4 通过；本机未安装 Blender。Worker 出片用例使用明确的 mock，不是本次真实 Blender 成片。
- 本次 SSH 在建立连接前超时，未登录服务器、未部署、未验证云端新版本。历史云 GPU/出片报告只代表历史结果。
- H3 已独立安装的事实保留；尚未接入项目。不要重复下载模型或占用现有 H3 任务。

详细范围和未完成项见 [A05 交接验收](reports/A05_SECURITY_HANDOFF.md)。

## 新鉴权配置（必须）

API 不再匿名放行。启动 API 的进程需要 `PRODUCTDIRECTOR_OWNER_TOKEN`：至少 32 字符的随机访问密钥。在工作台登录页输入该值；它不进入前端构建或浏览器持久存储。没有配置时 API 返回 503，这是预期保护。

开发/预览地址使用 `http://127.0.0.1:4173`。前端默认 `/api/v1`，Vite dev/preview 将 `/api` 代理到 `http://127.0.0.1:8000`；可用服务端变量 `PRODUCTDIRECTOR_API_PROXY` 指向受控隧道端口。`VITE_API_BASE` 仅用于公开的 API 地址，不得放任何密钥。推荐同源代理；跨站 Cookie 部署不在本轮验收范围。

```powershell
# 在 API 窗口生成本机临时访问密钥，仅放在当前进程环境中。
$env:PRODUCTDIRECTOR_OWNER_TOKEN = .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
$env:PYTHONPATH = 'apps/api'
.\.venv\Scripts\python.exe -m uvicorn productdirector_api.main:app --host 127.0.0.1 --port 8000
```

访问密钥由设备持有人通过私密方式取用；不要要求下一位执行者把密钥发到聊天、截图或 GitHub。新终端重启需重新设置环境。API 文档也受鉴权，完成同主机工作台登录后可访问。

第二个窗口：

```powershell
cd AI/ProductDirectorAI/apps/web
npm run dev -- --host 127.0.0.1 --port 4173 --strictPort
```

可选服务端配置：

| 变量 | 用途 |
| --- | --- |
| `PRODUCTDIRECTOR_ALLOWED_ORIGINS` | 精确浏览器 Origin 列表；默认 localhost 和 127.0.0.1 的 4173 端口 |
| `PRODUCTDIRECTOR_WORKER_TOKEN` | 至少 32 字符，必须与 Owner 密钥不同；未配置拒绝内部 HTTP Worker 请求 |
| `PRODUCTDIRECTOR_WORKER_ID` | 此 Worker 密钥绑定的身份，默认 `cloud-worker` |
| `PRODUCTDIRECTOR_SECRET_KEY` | Linux 保存 Provider 凭证所需的独立 Fernet 密钥；缺失/错误时拒绝保存 |

Fernet 密钥必须由 `Fernet.generate_key()` 生成并由服务持有人私密管理，不是任意密码。Windows 保留 DPAPI。旧 Windows 凭证、旧不安全草稿格式不能自动解密到 Linux，需重新配置。此轮未调用任何真实 Provider。

本轮是私有单 Owner 部署，不是多租户账号管理或可直接公网开放的完成版。继续仅监听回环地址，经已核验 SSH 隧道访问；不要开放云防火墙。

## 不经 Git 迁移的内容

`var/`、数据库、上传素材、成片、会话、Provider 凭证、SSH 私钥、模型、`.venv`、`node_modules`、`.env` 均不在仓库。私有素材和成果如需保留，由设备持有人通过私密渠道备份。源码同步不等于这些数据已经备份。

## 给新电脑上的 Codex / SOL 5.6

> 接手 ProductDirectorAI。先读取 AGENTS.md、docs/NEXT_COMPUTER_START.md、docs/CURRENT_PHASE.md、docs/reports/A05_SECURITY_HANDOFF.md、docs/EXECUTION_LOG.md 和主规划。用户已授权按阶段做到 V6，但当前完整 V1 仍为 PARTIAL。先核对 Git 和本机环境，运行前端 build、Sites 测试与后端 23 项回归。不要从零重写，不把局部测试当完整验收。本轮云 SSH 连接超时，先核对用户当前实例信息与现有私密密钥，再只读复验；恢复后继续 A04 真实任务/租约/取消恢复，再做 A05 授权远程输入、出片、成果回收。保留独立 H3 环境，不重复下载模型。每步记录证据并上传 GitHub；不把密钥、模型、私密素材和产物提交仓库。不要自动购买资源或调用收费 Provider。
