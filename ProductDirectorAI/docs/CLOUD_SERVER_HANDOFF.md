# 云服务器交接

> 本次换电脑交接：SSH 在认证前超时，未登录或部署本轮 A05 改动。下表部署/版本/运行状态均为历史观察，恢复连接后先只读复验。新代码配置见 [最新继续入口](NEXT_COMPUTER_START.md)。

更新日期：2026-09-11。本文只保存脱敏事实，不包含公网/内网地址、实例 ID、账号、密码、访问令牌、客户端公网 IP 或私钥。

## 当前新节点

| 项目 | 已验证事实 | 状态 |
| --- | --- | --- |
| 平台/地域 | 优云智算，华北二 A，Ubuntu-nvidia 22.04 | PASS |
| 计算 | 16 核、94GB、GeForce RTX 4090 24564 MiB | PASS |
| 驱动 | 570.153.02；nvidia-smi 显示 CUDA 12.8 驱动能力 | PASS；不等同于 Toolkit/PyTorch |
| 系统盘 | ext4 根分区约 97GB；最近检查可用约 19GB | 注意：现有 H3/ComfyUI 模型约 46GB，不再下载额外模型 |
| 公网 SSH | 专用 ED25519 密钥直连；服务器指纹先由网页终端核对 | PASS |
| Blender | 官方 Blender 5.2.1 LTS，`/usr/local/bin/blender` | PASS |
| FFmpeg | Ubuntu FFmpeg/ffprobe 4.4.2，`/usr/bin` | PASS |
| Node | 官方 Node.js 22.23.2，`/opt/productdirector/node` | PASS |
| 项目 | GitHub `main` 已同步最新已验收 V1 代码与文档 | PASS |
| 服务 | API `localhost:8000`；Web `localhost:4173`；systemd enabled/active | PASS；未公网暴露 |

旧节点曾完成约 291GB 根分区扩容，但该事实不得套用到当前新节点。当前节点禁止重复执行旧 growpart/resize2fs 记录，也不要在未核对云盘配置前格式化、重分区或假设已有 300GB。

## V1 云验收

| 门 | 退出证据 | 结果 |
| --- | --- | --- |
| CLOUD-01 环境 | OS、GPU、驱动、CPU/内存、磁盘、sudo | PASS |
| CLOUD-02 运行时 | Blender/Node 官方校验；FFmpeg/ffprobe 版本 | PASS |
| CLOUD-03 最小渲染 | 通用 GLB 可解码 PNG | PASS |
| CLOUD-04 GPU 证据 | 12 帧期间 21 次采样；峰值 GPU 61%、显存 1114 MiB | PASS |
| CLOUD-05 全链路 | API 上传→计划→批准→运行→H.264/Manifest | PASS |
| CLOUD-05b 计划语义 | 冻结 DirectorPlan → Blender 关键帧 → 成片/Manifest/scene.blend 复核 | PASS |
| CLOUD-05c 图片计划语义 | 冻结 DirectorPlan → FFmpeg 三段 2D 运动 → 成片/Manifest/边界帧复核 | PASS |
| CLOUD-06 受限远程 Worker | 当前仅 localhost 服务，无远程鉴权/调度 | NOT_STARTED |
| CLOUD-07 生产级对照 | 3 次真实任务、成本/恢复/回滚 | NOT_STARTED |

完整全链路产物：540×960、24fps、144 帧、6.000 秒、H.264。视频 SHA-256：`fadc6f3b41579289c1a81089554bd47df6d7b13d12a3d76fbf4ac026515baef7`。追加的 1080×1920 验收产物 SHA-256 为 `0c0f3603e98fad32f8e0d8f66c9a1f35bd812154f8b76c9700b308a848ab62ed`（作业 `bdaee1e7-8109-4610-b0e0-4dd25d243714`）。该结果证明 V1 通用 GLB 基础链路，不代替用户真实素材、远程鉴权、稳定性与完整 V1 验收。

计划语义复验使用同一通用 GLB：24 帧 85mm 定格、72 帧 24mm 侧向移动、48 帧 55mm 环绕。导出视频仍为 540×960、24fps、144 帧、6 秒；Manifest 保存冻结计划与 SHA-256。该作业的具体标识、地址和运行目录不进入 Git；可复验步骤见 `docs/reports/V1_DIRECTORPLAN_ACCEPTANCE.md`。

图片计划语义复验使用程序生成的通用 PNG，采用同一组时长与镜头字段，输出仍为 H.264、540×960、24fps、144 帧、6 秒；四个边界帧均有不同哈希。单图路径的 `hero_orbit` 是受控 2D 视差近似，并不表示图片得到物理 3D 环绕。该作业的具体标识、地址和运行目录不进入 Git。

## 服务与恢复

- API：`productdirector-v1-api.service`
- Web：`productdirector-v1-web.service`
- 项目：`/home/ubuntu/AI/ProductDirectorAI`
- Python 环境：`/home/ubuntu/AI/ProductDirectorAI/.venv`
- 运行数据：`/home/ubuntu/AI/ProductDirectorAI/var`

只读检查：`systemctl status productdirector-v1-api productdirector-v1-web`、`curl http://localhost:8000/api/v1/health`、`curl -I http://localhost:4173`。服务当前没有完整身份/权限层，不得直接改为所有网卡监听或开放云防火墙。需要远程访问时先实现鉴权或经用户明确授权配置受限隧道。

## MiniMax H3 决策

V2 首选 MiniMax H3 开放权重路线。它采用 [MiniMax H3 Community License](https://github.com/MiniMax-AI/MiniMax-H3)，准确说是开放权重而非默认等同 OSI 开源。官方完整 BF16 FL2VA 权重目录约 144GB，官方 SGLang 同时提供四卡基线和单张 RTX 4090 的量化/卸载路径。当前节点已经存在一套独立的 ComfyUI 单卡量化环境，含 Ref2VA、量化文本编码器、VAE 与 Turbo LoRA，并有可解码 H.264 试验产物；该环境尚未接入 ProductDirectorAI，也不作为 V2 通过证据。当前根盘只剩约 19GB，因此：

1. 不在当前节点下载官方完整 BF16 权重或重复下载现有单卡模型。
2. V2 先实现 Provider、任务合同、状态/失败处理与可替换适配器。
3. 真实集成优先复用现有单卡量化环境，先完成最小输入/输出、失败与成本证据；更高画质/完整 BF16 再评估多卡大盘节点。
4. 不把 H3 Context-IR、Regenerate-2K、未接入的量化工作流或独立 ComfyUI 试验标成 ProductDirectorAI 已支持。

## 安全边界

- 不自动购买、充值、扩容、开放公网服务、关机或删除实例。
- SSH 私钥只保存在本地隔离目录，不提交 Git；仓库只记录脱敏指纹核验流程，不记录指纹值、地址或用户名。
- 不将 API key、token、cookie、密码或旧 Windows DPAPI 数据写入 Linux 明文文件、日志、Manifest 或 Git。
- 真实用户产品、第三方模型权重和运行目录 `var/` 不提交仓库。
