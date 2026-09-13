# 安装与迁移（2026-09-13）

这是当前安装入口，优先于旧交接文档中的旧路径和旧阶段说明。仓库含完整应用源码、前后端、渲染脚本、合同、测试、文档与安装脚本；依赖由安装工具下载，并非离线安装包。

## A. 换电脑继续使用现有云端（当前使用方式）

新电脑不需要 GPU、Python、Node 或模型。安装 Git 和 Windows OpenSSH 客户端，克隆项目：

```powershell
git clone https://github.com/zhongxuhai116-pixel/AI.git
cd AI\ProductDirectorAI
powershell -ExecutionPolicy Bypass -File .\scripts\connect-cloud.ps1 -Server 用户名@服务器地址 -IdentityFile C:\Keys\自己的SSH私钥
```

保持隧道窗口运行，浏览器打开 http://127.0.0.1:4173/ ，使用现有服务器的 Owner 登录密钥。服务器地址、私钥与登录密钥通过私人渠道交接，Git 中没有。新出口 IP 需获云防火墙 SSH 放行；首次连接核对服务器指纹。4173 被占用时先关闭旧隧道，或显式设置 LocalPort；自定义端口还需核对 API 允许的 Origin。

这种方式保留服务器上的历史素材、模型、任务和批次，无需复制数据库。网页下载保存到新电脑；当前 Codex 的自动收集任务和本地下载文件夹不会随 git clone 迁移，需要另行设置。

## B. 新电脑安装本地工作台（Windows x64）

安装 Python 3.12.x（勾选 PATH）、Node.js 22.x 或 24.x、Git、FFmpeg/ffprobe、Blender，并将命令所在目录加入 PATH。Blender 常规安装不会自动加入 PATH，需将包含 blender.exe 的目录加入用户 PATH。软件入口：[Python](https://www.python.org/downloads/windows/)、[Node.js](https://nodejs.org/en/download)、[Git](https://git-scm.com/downloads)、[Blender](https://www.blender.org/download/)、[FFmpeg](https://ffmpeg.org/download.html)。可使用 [WinGet](https://learn.microsoft.com/en-us/windows/package-manager/winget/install) 的 search/show 核对包和版本后安装。

打开新的 PowerShell：

```powershell
git clone https://github.com/zhongxuhai116-pixel/AI.git
cd AI\ProductDirectorAI
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\doctor.ps1
```

脚本需要联网；Python 限定 3.10–3.12、Node 支持 22.x/24.x；前端使用 package-lock.json 和 npm ci，后端直接依赖固定版本。可用 `-Python C:\Python312\python.exe` 选择解释器。重复运行保留现有 .env。依赖失败立即停止，不显示安装成功。

安装完成后，在两个窗口分别运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-api.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\start-web.ps1
```

浏览器访问 http://127.0.0.1:4173/ 。登录密钥是项目 .env 中 PRODUCTDIRECTOR_OWNER_TOKEN 的值；不要发到聊天或提交 Git。启动脚本读取 .env，已有进程环境变量优先。启动脚本是前台进程，关闭窗口会停止服务。

本地默认新建 SQLite 与 var/，不会自动显示旧服务器素材。基础图片预演与 GLB 渲染依赖 FFmpeg/Blender；真实图片→3D、逐场景 AI 视频还需要配置可用的 H3/ComfyUI。仅安装网页不能产生真实 AI 视频。

## C. 模型与云端环境迁移

已记录 deploy/environment 下的 API Python 3.10 包快照、ComfyUI Python 3.10 包快照和 model-manifest.json（文件名、大小、下载来源）。Linux 快照用于核对现有服务器，不应直接灌入 Windows 环境。ComfyUI 基线：官方 https://github.com/Comfy-Org/ComfyUI.git ，提交 7fd919f0caff66a52289ea5b19cb6eaca0da04ef。

现有服务器 Ubuntu 22.04、RTX 4090 24GB、94GB 内存；模型文件约 46GiB，此外还需框架、缓存、输出和系统空间。GPU 驱动、CUDA/PyTorch 必须匹配。模型源码的许可证和下载权限以其来源为准；manifest 的 main 下载地址不是不可变哈希锁，迁移时应与旧服务器文件 SHA256 比较。

完整迁移应按顺序：

1. 在新 Linux GPU 节点安装相容驱动、Python 3.10、Node 22、Blender、FFmpeg；克隆应用并安装 API requirements（PostgreSQL 使用 requirements-migrate.txt）。
2. 克隆上述 ComfyUI 提交，新建独立 venv，参考 ComfyUI 原始安装说明和环境快照恢复 PyTorch/CUDA/SageAttention。将 deploy/comfyui/custom_nodes 中的定制节点复制到新 ComfyUI/custom_nodes（先备份已有同名文件）。本仓库已包含参考素材、Blender 桥接和 SageAttention 加速节点；工作流构建代码位于 API providers/comfyui.py。optimization 中的基准视频和旧实验不是运行必需文件。Blender 桥接已改为 PATH/PRODUCTDIRECTOR_BLENDER 查找。
3. 从旧节点私下复制模型或按 manifest 下载，核对大小和 SHA256，保留原目录结构。不要复制 Linux venv 到 Windows。
4. 配置 PRODUCTDIRECTOR_COMFYUI_URL 指向可信网络内的 ComfyUI；服务保持 localhost，远程使用 SSH 隧道。将服务器密钥置于服务环境文件，设仅管理员可读。
5. 等生产队列排空后，备份数据库和 var/ 并保持一致。PostgreSQL 使用 pg_dump/pg_restore（参见 scripts/pg_restore_drill.sh），SQLite 应使用 SQLite backup API 或停服复制。不要只复制 PostgreSQL 的磁盘文件。
6. 凭证单独恢复：Windows DPAPI 与用户/机器绑定，新电脑重新录入 Provider 凭证；Linux 加密数据还依赖原 PRODUCTDIRECTOR_SECRET_KEY，未恢复它就重新录入，不把密钥提交 Git。
7. 验收登录、素材上传、GLB 渲染、图片→3D、真实三场景视频、批次自动接续和下载。通过后再切换，保留旧节点用于回退。

本次不会中断正在运行的 20 条视频批次，也没有将活跃数据库做成未经一致性校验的迁移包。已在 Windows Python 3.12 / Node 24 上新建 venv，实际执行安装、pip check、前端构建、API 导入（183 路由）、场景流水线 2 项测试、Sites 4 项测试。doctor 正确报告 Blender 不在 PATH；补充本机 Blender 5.1 PATH 后全部通过。空白新电脑及新 GPU 节点尚未做完整端到端迁移验收，迁移后仍需上述真实链路检查。

## 文件与更新

不要把 .env、SSH 私钥、var/、虚拟环境、node_modules、模型和用户视频放进 Git。它们需要私密备份或重新安装。源码更新使用 `git pull --ff-only` 后重跑 setup.ps1（先结束本机任务并停止服务）；数据库升级前另做备份。
