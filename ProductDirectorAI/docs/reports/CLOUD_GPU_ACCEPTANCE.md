# V1 云 GPU 验收报告

日期：2026-09-11
结论：V1 云基础设施与通用 GLB Blender/FFmpeg 端到端链路 PASS；V1 产品总状态仍为 PARTIAL。

## 环境与构建

- Ubuntu 22.04.4，RTX 4090 24564 MiB，驱动 570.153.02。
- Blender 5.2.1 LTS，Node.js 22.23.2，FFmpeg/ffprobe 4.4.2。
- Python compileall PASS；Vite build PASS；Sites worker tests 4/4 PASS。
- API 与 Web systemd 服务 enabled/active，仅监听 localhost。

## GPU 渲染证据

使用仓库的许可明确通用 GLB 进行 12 帧 Blender 5.2.1 headless 渲染，并在渲染进程期间独立轮询 nvidia-smi：

| 指标 | 结果 |
| --- | ---: |
| 输出帧数 | 12 |
| GPU 采样数 | 21 |
| 峰值 GPU 利用率 | 61% |
| 峰值显存 | 1114 MiB |

该证据只覆盖本次 EEVEE/headless 路径。它不代表 Cycles CUDA/OptiX、生成式视频模型或长期稳定性已通过。

## API 全链路证据

通过实际 `/api/v1` 接口执行：上传通用 GLB → 创建模板计划 → 批准 → 启动作业 → 轮询真实状态 → 下载视频与 Manifest。

| 指标 | 结果 |
| --- | --- |
| 作业终态 | SUCCEEDED / ARTIFACT / 100% |
| 编码 | H.264 |
| 分辨率 | 540×960 |
| 帧率 | 24fps |
| 帧数 | 144 |
| 时长 | 6.000 秒 |
| 视频大小 | 137263 字节 |
| 视频 SHA-256 | `fadc6f3b41579289c1a81089554bd47df6d7b13d12a3d76fbf4ac026515baef7` |
| Manifest SHA-256 | `0fa5cba661823bd57a87c28184dace670b6241ce3ce0008b976f2b16b9c4c153` |

## DirectorPlan 相机语义证据

本次更新后，GLB Run 在创建时将已批准计划写入作业目录；Blender 只读取该快照并验证三段帧数合计为 144。真实云端复验使用 24 / 72 / 48 帧三段计划，结果如下：

| 时间范围 | 用户编辑值 | 从 scene.blend 读取的实际相机状态 |
| --- | --- | --- |
| 1–24 帧 | `static`，85mm | 起止位置相同 `(0, -3.55, 1.2)`，焦距 85mm |
| 25–96 帧 | `side_track`，24mm | x 从 -1.35 移至 1.35，焦距 24mm |
| 97–144 帧 | `hero_orbit`，55mm | 相机从左前方移动至右前方，焦距 55mm |

编辑后的作业 SUCCEEDED，输出为 H.264、540×960、24fps、144 帧、6 秒。Manifest 包含冻结 DirectorPlan 和其 SHA-256；完整可复验记录见 `V1_DIRECTORPLAN_ACCEPTANCE.md`。

## 图片 DirectorPlan 语义证据

图片 API 也使用作业目录里的同一份冻结 DirectorPlan。真实 PNG 任务采用 85mm 定格 24 帧、24mm 横移 72 帧、55mm 收束 48 帧，输出为 H.264、540×960、24fps、144 帧、6.000 秒。四个镜头边界帧的 PNG 哈希均不同，视觉抽检确认构图随计划变化；Manifest 包含计划快照 SHA-256。单图 `hero_orbit` 是有意的 2D 视差近似，不替代 GLB 的物理相机环绕。

## 未通过或未覆盖

- 1080×1920 输出质量门。
- 任务租约、重启恢复、幂等、取消竞争和独立 Worker。
- 远程身份鉴权与授权、受限隧道或安全公网 API。
- 用户真实产品与至少 3 次稳定性/成本对照。
- MiniMax H3 与 ProductDirectorAI 的真实 Provider 集成；服务器已有独立单卡 ComfyUI 量化试验，但尚未接入。

因此本报告不能替代产品负责人或用户的完整 V1 ACCEPTED 决定。
