# V1 本机真实 FFmpeg 图片链路验收（新电脑）

日期：2026-09-12。结论：**图片链路在本机真实跑通并产出可复验产物**；GLB 链路未在本机复验（本机没有 Blender），完整 V1 仍为 PARTIAL。

## 1. 为什么要做这次验收

换电脑后的历史上机记录里，图片/GLB 链路只有旧电脑的历史 PASS，新电脑尚未用真实进程跑通过。本机没有 Blender，但 FFmpeg/ffprobe 可用，因此先把不依赖 Blender 的图片链路做成**真实（非 mock）端到端证据**。

## 2. 环境与驱动方式

| 项目 | 本机实际 |
| --- | --- |
| Python | 3.12.10（项目 `.venv`） |
| API | `uvicorn productdirector_api.main:app`，`127.0.0.1:8000`，进程内配置 `PRODUCTDIRECTOR_OWNER_TOKEN` |
| FFmpeg / ffprobe | 8.0.1-full_build（`/api/v1/health` 返回 `available=true`） |
| Blender | 未安装（`available=false`），因此本次不执行 GLB 渲染 |

驱动脚本位于 `work/local-e2e/run_local_e2e.py`（`work/` 不进入 Git），它通过真实 HTTP 接口完成：登录会话 → 上传素材 → 生成模板计划 → 编辑三段分镜 → 批准 → 启动作业 → 轮询 → 下载 MP4 与 Manifest → ffprobe 与哈希校验。

素材是脚本程序生成的通用 PNG 夹具（非用户真实产品，可再生成），SHA-256 `c13edf66e416c76cc63949842776ddcf303e57af329a37aa45b70b0e7687781e`。

## 3. 执行结果

计划：`output` 设为 1080×1920 / 24fps / 6 秒；三段分镜编辑为 85mm 定格 24 帧、24mm 侧移 72 帧、55mm 收束 48 帧。

| 指标 | 结果 |
| --- | --- |
| 作业 ID | `3801f9c7-453c-4908-bb66-d8d22c128b47` |
| 终态 | `SUCCEEDED` / `ARTIFACT` / 100% |
| 编码 / 分辨率 | H.264 / 1080×1920 |
| 帧率 / 帧数 / 时长 | 24fps / 144 帧 / 6.000 秒 |
| 视频大小 | 206,693 字节 |
| 视频 SHA-256 | `b689c76aa8003e336ee4b680913f3852d0ae1fcf7a9bb14307574fe77401f4b1` |
| Manifest SHA-256 | `eb8fdeae901cee6c557af5fcdaa742d8eeb7f94e1f08963779e46518b7e1556b` |
| 计划快照 SHA-256 | `3ecab00c99b19b02445355b925931450af423e9625e0cbc8e5a6aa8aec48846d` |
| `preview_kind` | `IMAGE_2D`（明确不声明物理 3D 环绕） |

分镜语义确实驱动了成片：抽取第 1 / 24 / 25 / 96 / 97 / 144 帧，六张边界帧的 SHA-256 全部不同（`distinct_boundary_frames=6`）。Manifest 中保存的正是编辑后的三段分镜（`static/85mm`、`side_track/24mm`、`hero_orbit/55mm`），中文字段完整无乱码。

## 4. 本次覆盖与未覆盖

覆盖：真实 HTTP 鉴权会话与 CSRF、真实素材上传与哈希、模板计划、分镜编辑与冻结快照、批准、真实后台作业、真实 FFmpeg 出片、真实产物下载、ffprobe 技术检查与边界帧差异。

未覆盖：

- GLB/Blender 链路未在本机复验（本机无 Blender）；最近一次真实 GLB 证据仍是云端历史报告。
- 未使用用户真实产品素材；本机结果不替代业务验收。
- 未做云端部署与本轮代码的云端出片（云 SSH 仍被防火墙挡住）。

## 5. 复验方式

```powershell
cd <repo>\ProductDirectorAI
$env:PRODUCTDIRECTOR_OWNER_TOKEN = .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
$env:PYTHONPATH = 'apps/api'
Start-Process .\.venv\Scripts\python.exe -ArgumentList '-m','uvicorn','productdirector_api.main:app','--host','127.0.0.1','--port','8000' -WindowStyle Hidden
$env:PD_OWNER_TOKEN = $env:PRODUCTDIRECTOR_OWNER_TOKEN
.\.venv\Scripts\python.exe .\work\local-e2e\run_local_e2e.py
```

脚本会输出视频/清单哈希与 ffprobe 结果；`var/` 与 `work/` 均不进入 Git。
