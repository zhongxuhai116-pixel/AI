# ProductDirectorAI 云 GPU 选型与部署规范

> 选型日期：2026-09-10  
> 当前结论：优云智算作为主生产平台，AutoDL 作为低成本开发/备用平台。V1 不自动购买、创建或启停任何云资源。

## 1. 直接结论

用户截图中的优云智算配置可以用于 ProductDirectorAI：

- 地域：上海二 A；
- GPU：单卡 RTX 5090 32GB；
- CPU/内存：14 核 64GB；
- 计费：按量计费、关机不收 GPU/CPU/内存费用；
- 镜像：Ubuntu 22.04、CUDA 13.2、PyTorch 2.13、Python 3.12 基础容器镜像；
- 截图结算价：¥3.20/小时、¥70.53/日、¥1,923.60/月。

建议修改：系统盘不要保留 50GB。仅运行 V1 时至少 100GB；安装 ComfyUI、多个模型和视频工作流时建议 200GB，或把模型、缓存与成果放到可持久化独立云盘。先使用按量计费完成 2–5 小时基准，不先购买包日或包月。

当前基础镜像适合部署 V1 API、Web、Blender 和 FFmpeg，但不能视为已经安装 ProductDirectorAI 或 ComfyUI。CUDA 13.2、PyTorch 2.13 与具体 ComfyUI 自定义节点、xFormers、Flash Attention 或模型插件的兼容性必须实测。若节点不兼容，应改用平台中经过验证的 ComfyUI/视频社区镜像，或锁定项目验证过的 CUDA/PyTorch 组合，不在生产环境使用浮动 `latest`。

最终平台与镜像建议：使用优云智算；只跑 ComfyUI 时选“ComfyUI 纯净版”，部署 ProductDirectorAI 全栈时选“Ubuntu-nvidia 22.04”系统镜像。不要把当前 PyTorch 基础容器镜像直接定为长期生产基线。

## 2. 平台选择

| 目标 | 首选 | 原因 | 备选 |
| --- | --- | --- | --- |
| 当前 V1 云端 Blender 与普通 ComfyUI | 优云智算 RTX 5090 32GB | 速度、显存和后续 API 自动化平衡最好 | AutoDL RTX 5090 32GB |
| 最低成本开发与基础预演 | AutoDL RTX 4090 24GB | 官方展示约 ¥1.88/小时 | 优云智算 RTX 3090 24GB，约 ¥1.19/小时；速度更慢 |
| 大模型视频、长时序、多 ControlNet、显存安全 | 优云智算 RTX 4090 48GB | 48GB 显存、约 ¥3.30/小时，明显降低 OOM 风险 | AutoDL A40/L40/PRO 6000，先核对实时库存和价格 |
| 可重算批量预览 | 5090 抢占式 | 更低价格，任务需分片、Checkpoint 和重试 | 普通按量实例 |
| 最终成片、不可中断任务 | 普通按量 5090 或 4090 48GB | 不承担抢占回收风险 | 包日/包月仅在基准后评估 |
| V6 自动启停、批量 Worker | 优云智算 | 官方提供库存、价格、创建、查询、启停和释放 API/CLI | AutoDL 容器实例 Pro API |

AutoDL 当前公开价比优云智算更低：5090 32GB 约 ¥2.78/小时，4090 24GB 约 ¥1.88/小时；优云智算截图中的 5090 为 ¥3.20/小时。5 小时 5090 试跑价差约 ¥2.10，因此不应仅为一次 V1 试跑迁移平台。

长期策略不是只选一家：主生产 Worker 使用优云智算，开发和可重算任务允许路由到 AutoDL。任何平台在加入 Router 前都要通过同一能力、成本、状态与成果合同。

## 3. GPU 档位

### 3.1 `DEV_ECO`

- 候选：RTX 3090 24GB；
- 优云智算公开最低单卡价：¥1.19/小时；
- 用途：代码部署、Blender 预演、基础图片工作流、低分辨率 ComfyUI 验证；
- 不适合：现代大型视频模型、高分辨率长视频、多模型同时驻留；
- 退出条件：出现 OOM、单任务耗时超过预算，或需要 5090 特定加速时升级。

### 3.2 `DEFAULT_FAST`

- 候选：RTX 5090 32GB；
- 优云智算截图价：¥3.20/小时；AutoDL 公开参考价：¥2.78/小时；
- 用途：V1 云端 Blender、普通 ComfyUI、Flux/SDXL 的适配版本、1080p 预览和多数短视频试验；
- 当前默认：单卡、14 核 64GB、100–200GB 持久化空间；
- 退出条件：显存峰值持续超过 28GB、频繁 CPU OOM、模型要求 48GB，或任务需要稳定多卡互联时升级。

### 3.3 `VIDEO_SAFE`

- 候选：优云智算 RTX 4090 48GB；
- 公开价：¥3.30/小时、¥72.60/日、¥1,980/月；
- 用途：Wan/Hunyuan 等已验证视频工作流、长时序、高分辨率、多 ControlNet、产品 Mask/Depth/参考层同时驻留；
- 特点：比截图中的 5090 只高 ¥0.10/小时，但多 16GB 显存；纯算力较慢，显存安全更高；
- 退出条件：单卡 48GB 仍不足或多租户吞吐达不到 SLA，才评估专业卡。

### 3.4 `LARGE_MODEL`

- 候选：H20 96GB、A800/A100 80GB、PRO 6000 96GB；
- 用途：大模型服务、多用户并发、专业显存容量、需要多卡互联的训练；
- 禁止默认选择：ProductDirectorAI 当前单产品视频工作流没有基准证明需要这些卡；
- 启用门：5090/4090 48GB 的真实基准失败，且性能或显存收益能够覆盖成本。

## 4. 不建议的默认选项

- P40 24GB：价格低但架构老，现代半精度、ComfyUI 节点和视频栈兼容/性能风险高；
- RTX 3080 Ti 12GB：可做轻量图像，但显存不足以作为视频路线默认 Worker；
- RTX 4090 24GB：可以跑 V1，若与 5090 价差很小则优先 5090；若在 AutoDL 仅 ¥1.88/小时并只做 V1，则有明显成本优势；
- 抢占式实例：不用于最终渲染、发布包或不可重复的生成；
- 包月：未完成真实吞吐和利用率基准前不得购买。

## 5. 地域、网络与存储

### 5.1 地域

- 华北二 A：优云智算 GPU 类型最全，包含 5090、4090 48GB、4090、3090、A100/A800/H20；适合主资源池；
- 上海二 B/上海区域：适合用户当前网络与低延迟开发，实际卡型以创建页为准；
- 华北一 C：资源和性价比有优势，但无独立外网 IP，不适合固定 IP 白名单/API 回调；删除实例不保留系统盘，需使用云存储 Pro。

V2–V6 若需要 MiniMax 回调、自动化 Webhook 或固定白名单，优先选择具备稳定网络入口的区域，并使用受限端口、防火墙和反向代理。不要把 Jupyter、SSH、ComfyUI 或数据库端口无鉴权公开到公网。

### 5.2 存储规划

| 分区 | 最低 | 建议 | 内容 |
| --- | ---: | ---: | --- |
| 系统/代码 | 100GB | 150GB | OS、Docker/环境、ProductDirectorAI、Blender、FFmpeg |
| 模型盘 | 200GB | 500GB+ | ComfyUI 模型、VAE、ControlNet、视频模型、缓存 |
| 成果盘 | 100GB | 按项目估算 | 帧、MP4、音频、发布包、Manifest |

模型盘和成果盘必须可持久化并支持配额监控。Worker 临时帧可在任务成功并上传成果后按保留策略清理。关机不收计算费不等于磁盘、镜像、对象存储和出网流量免费。

## 6. 计费策略

### 6.1 当前试跑

1. 选择普通按量计费；
2. 只开 1 张 GPU；
3. 完成 V1 部署、1 个图片项目、1 个 GLB 项目和 1 个 ComfyUI 轻量工作流；
4. 记录启动、下载模型、Blender 渲染、FFmpeg 编码、GPU 峰值、显存峰值、磁盘和出网；
5. 不工作时关机；需要低成本维护环境时，可评估优云智算无卡模式；
6. 2–5 小时后根据实际 `cost_per_successful_run` 决定平台与卡型。

### 6.2 估算示例

仅按公开计算资源单价，不含磁盘、镜像、存储和流量：

| 配置 | 5 小时 | 50 小时 | 100 小时 |
| --- | ---: | ---: | ---: |
| AutoDL 4090 24GB · ¥1.88/h | ¥9.40 | ¥94 | ¥188 |
| AutoDL 5090 32GB · ¥2.78/h | ¥13.90 | ¥139 | ¥278 |
| 优云 5090 32GB · ¥3.20/h | ¥16.00 | ¥160 | ¥320 |
| 优云 4090 48GB · ¥3.30/h | ¥16.50 | ¥165 | ¥330 |

比较平台必须使用“每个验收通过成果成本”，不能只看小时价。更便宜但频繁 OOM、下载慢、重建环境或中断的实例可能总成本更高。

## 7. ProductDirectorAI 数据与 API 预留

V2 增加但不在 V1 启用以下字段：

```json
{
  "provider": "compshare",
  "profile": "DEFAULT_FAST",
  "region": "cn-sh2",
  "zone": "resolved-at-runtime",
  "gpu_type": "5090",
  "gpu_count": 1,
  "cpu": 14,
  "memory_gb": 64,
  "billing": "POSTPAY",
  "disk_gb": 200,
  "price_snapshot": {
    "currency": "CNY",
    "amount_per_hour": 3.2,
    "captured_at": "2026-09-10T00:00:00+08:00",
    "source": "provider-console"
  }
}
```

需要的适配器能力：

- `list_regions()`、`list_instance_types()`、`check_capacity()`；
- `estimate_price()`，返回价格快照和有效时间；
- `create_worker()`、`start_worker()`、`stop_worker()`、`release_worker()`；
- `get_worker()`、`get_metrics()`、`get_access_endpoints()`；
- `attach_storage()`、`wait_ready()`、`drain_jobs()`；
- 所有写操作使用幂等键、审计日志、预算上限与人工授权门。

V1 的设置页只展示静态选型快照，不接受云密钥、不调用这些 API。V6 Automation 只有在预算、权限、配额、库存、价格和发布任务均通过验证后，才能调度 Worker；创建或续费计算资源仍需要明确的管理员策略，不能由模型文本直接触发。

## 8. 云 Worker 验收基准

每个平台和每个 GPU Profile 必须跑同一套测试：

1. 基础健康：GPU、驱动、CUDA、磁盘、网络、时间同步；
2. V1 图片链路：上传 → DirectorPlan → FFmpeg 预演 → Manifest；
3. V1 GLB 链路：Blender headless → 144 帧 → MP4 → 成果检查；
4. ComfyUI 轻量链路：批准 workflow → `/prompt` → 事件/历史 → 下载；
5. 断线恢复：API/Worker 重启后任务状态可对账；
6. 成本：保存启动时间、可用时间、执行时间、空闲时间和实际账单；
7. 安全：公网端口最小化，凭证不进入日志、Git、截图或 Manifest；
8. 清理：任务完成后上传成果、释放临时帧，关机后确认计算费停止。

通过条件：连续 3 次同类任务成功，成果合同完整，无裸密钥，状态不虚构，费用误差在价格快照允许范围内。未通过的平台保留为 `UNVERIFIED`，不得进入 AUTO 路由。

## 9. 禁止事项

- 不自动点击“立即部署”、充值、付款、续费或购买包月；
- 不把云平台登录密码、Cookie、公钥/私钥或 API Token 写入仓库；
- 不根据 GPU 名称承诺出片时间，必须先跑项目基准；
- 不把 50GB 系统盘当作完整 ComfyUI/视频模型存储；
- 不把抢占式任务的中断伪装为普通失败或成功；
- 不公开无鉴权的 ComfyUI、Jupyter、数据库或 Worker 管理端口；
- 不因某平台小时价低就静默切换，必须保留 RouteDecision 和价格快照；
- 不让云 Worker持有发布平台凭证；发布由受限 Publishing Worker 完成。

## 10. Codex 下一阶段施工任务

只有用户明确授权 V2 或“接入云 Worker”后执行：

1. 新建 ADR：确认优云智算主平台、AutoDL 备用与凭证边界；
2. 在后端增加通用 `ComputeProvider` 合同和假适配器合同测试；
3. 增加优云智算只读探测：区域、规格、库存、价格；
4. 完成管理员凭证存储，前端永不回显完整凭证；
5. 以幂等键实现创建/启停/释放，写操作默认关闭；
6. 部署锁定版本的 Worker 镜像，记录镜像摘要；
7. 在 5090 32GB 上完成 V1 图片和 GLB 基准；
8. 在 4090 48GB 上完成选定视频工作流基准；
9. 实现 AutoDL 备用适配器，只在同一合同测试通过后启用；
10. 形成 `docs/reports/CLOUD_GPU_ACCEPTANCE.md`，包含脱敏配置、性能、成本、失败恢复与安全证据。

阶段退出条件：至少一个普通按量节点完成连续 3 次真实任务；关机后计算费用停止；成果持久化；Worker 失联可恢复；价格、库存和任务状态均来自 Provider，而非前端模拟；没有发生自动购买或凭证泄露。

## 11. 官方资料

- [优云智算价格列表](https://compshare.cn/price-list)
- [优云智算计费概览](https://compshare.cn/docs/operation/charge/bill)
- [优云智算 GPU 卡型](https://compshare.cn/docs/operation/introduce/gpu)
- [优云智算区域说明](https://compshare.cn/docs/operation/introduce/region)
- [优云智算无卡模式](https://compshare.cn/docs/operation/gpu/cardlessmode)
- [优云智算抢占式实例](https://compshare.cn/docs/operation/gpuspot/gpuspot)
- [优云智算创建实例 API](https://compshare.cn/docs/gpus/instance/createcompshareinstance)
- [优云智算 CLI 与 SDK](https://compshare.cn/docs/gpus/cli)
- [AutoDL GPU 价格与卡型](https://www.autodl.com/home)
- [AutoDL GPU 选型](https://www.autodl.com/docs/gpu/)
- [AutoDL 容器实例 Pro API](https://www.autodl.com/docs/instance_pro_api/)

价格、折扣、库存和区域会变化。每次创建实例前以已登录控制台的实际结算价为准，并保存非敏感价格快照。
