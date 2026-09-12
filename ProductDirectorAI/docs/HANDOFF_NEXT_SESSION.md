# 下一次操作交接说明（2026-09-12 收尾）

这份文件是**下次开工的第一入口**。先读它，再按需展开下面的报告。本轮已把全部记录推送到 GitHub，本地工作区干净；具体提交号用 `git log -1 --oneline` 查（就是包含本文件的这一次提交）。

> 阅读顺序建议：本文件 → [当前阶段](CURRENT_PHASE.md) → [V3 任务书](reports/V3_TASK_BRIEF.md) → [V1 验收确认单](reports/V1_READY_FOR_REVIEW.md)。
> `NEXT_COMPUTER_START.md` 是 2026-09-11 换电脑时写的，其中"V1 仍为 PARTIAL""云 SSH 超时"等描述**已被本文件取代**，只作历史留档。

## 1. 当前状态（都已验证，不是计划）

| 项目 | 状态 |
| --- | --- |
| **V1** | **ACCEPTED**（用户 2026-09-12 确认，见 V1 验收确认单） |
| **V2** | **ACCEPTED**（同日确认） |
| **V3** | **进行中**：V3-01 / V3-02 通过；V3-03 / V3-04 完成；V3-05 核心通过、有未完成项；V3-06 … V3-10 未开工 |
| 后端测试 | **152/152**（本地与云端各跑一次，结果一致） |
| 代码 | 本地与 `origin/main` 一致，工作区干净 |
| 云端 | api / web / postgresql / comfy-h3 四个 systemd 服务均 active |

明确没有完成的：V3 只做到第 5 项的一半；V4 / V5 / V6 一行代码都没写，不能因为 V1 / V2 已验收就对外说"V6 完成"。

## 2. 本轮推到 GitHub 的东西

有两条独立的线，别混：

| 线 | 仓库 | 内容 |
| --- | --- | --- |
| 主线：ProductDirectorAI（V1→V6） | `https://github.com/zhongxuhai116-pixel/AI.git`，目录 `ProductDirectorAI/` | 源码、合同、Blender 脚本、测试、全部脱敏报告；本轮补上本交接文档与文档索引修正 |
| 支线：H3 / ComfyUI 独立服务 | `https://github.com/zhongxuhai116-pixel/comfyui-h3-cloud-records.git`（私有） | 部署档案原有；**本轮新增 `optimization/`**——加速版工作流、加速节点代码、微基准与全过程耗时、加速前后抽帧对比、优化前工作流快照，提交 `cfdfd7f` |

两件事要说清楚：

1. H3 / ComfyUI 仍然是**独立服务**，还没有接进 ProductDirectorAI 之外的形态——项目侧只通过 Provider 接口调用它。
2. 加速那一轮只做了**一次同规格对比**（907.728 秒 → 667.537 秒，约 −26.46%），不是多次重复的统计结论；也没有做完整主观画质评测，只抽看了第 3 秒与第 10 秒。

## 3. 环境与访问

| 项 | 值 |
| --- | --- |
| 云端主机 | `117.50.44.60`（优云智算 华北二 A，RTX 4090 24GB，Ubuntu 22.04） |
| SSH | `ssh -i C:\Users\Administrator\.ssh\pd_ed25519 ubuntu@117.50.44.60` |
| 云端仓库 | `/home/ubuntu/AI`（Git 根），项目在 `ProductDirectorAI/` |
| 服务端密钥 | `/etc/productdirector/v1.env`（Owner / Worker / Fernet）、`/etc/productdirector/pg.env`（PostgreSQL DSN），均 root 0600 |
| 生产数据库 | PostgreSQL，库 `productdirector`；SQLite 文件保留在 `var/`，回滚只需移除 pg.env 里的 DSN 并重启 |
| 云防火墙 | `TCP:22` 仅放行本机当前出口 IP；**换网络后必须先在控制台加规则**，否则 SSH 一直超时（这是上次卡住一天的真实原因） |

常用命令：

```powershell
# 本地测试（期望 152/152）
cd C:\Users\Administrator\Desktop\MEET BENI 4K\GitHub-AI-Archive\ProductDirectorAI
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'

# 云端同步 + 复验 + 重启（改完代码并推送后）
ssh -i $env:USERPROFILE\.ssh\pd_ed25519 ubuntu@117.50.44.60 `
  "cd /home/ubuntu/AI && git pull --ff-only && cd ProductDirectorAI && .venv/bin/python -m unittest discover -s tests -p 'test_*.py' && sudo systemctl restart productdirector-v1-api productdirector-v1-web"
```

注意两点：本地 Vite 构建必须在沙箱外跑（沙箱内读不到上级目录）；Git 需要 `-c safe.directory='C:/Users/Administrator/Desktop/MEET BENI 4K/GitHub-AI-Archive'`，因为目录属主是管理员。

## 4. 关键证据位置（都在仓库内）

| 主题 | 文件 |
| --- | --- |
| 当前阶段 | `docs/CURRENT_PHASE.md` |
| V1 验收确认单 | `docs/reports/V1_READY_FOR_REVIEW.md` |
| A01–A07 各阶段 | `docs/reports/` 下的 `A0*.md` |
| 云端部署与双链路出片 | `docs/reports/CLOUD_DEPLOY_A05_ACCEPTANCE.md` |
| V2 Provider / 视频 / 产能 | `docs/reports/V2_H3_PROVIDER_INTEGRATION.md` |
| V2 AI 导演 | `docs/reports/V2_AI_DIRECTOR.md` |
| **V3 任务书与全部排查记录** | `docs/reports/V3_TASK_BRIEF.md`（含 Blender 5 合成器 API 的完整踩坑记录） |
| 本地决策 | `docs/decisions/ADR-00*.md`（本地优先、原型基线） |
| H3 加速那一轮 | 独立仓库 `comfyui-h3-cloud-records` 的 `optimization/` 与 `README.md` |

## 5. 下次可以直接用的自动化脚本

| 脚本 | 用途 |
| --- | --- |
| `scripts/v1_cloud_acceptance.py` | 云端图片 + GLB 双链路真实验收 |
| `scripts/h3_reconstruct.py` / `glb_repeat_check.py` / `capacity_probe.py` | 3D 重建、三次作业一致性、产能探针 |
| `scripts/strict_composite.py` | **V3-05 Strict 合成**（线性空间 + 掩码外扩 + 像素锁定断言） |
| `blender/scripts/render_product.py` | 渲染器；`--passes` 开启多通道（beauty/alpha/depth/normal + 产品遮罩） |
| `blender/scripts/validate_fidelity_passes.py` | **V3-02 通道校验**（不依赖 Blender，用 venv 的 Pillow + OpenEXR） |
| `scripts/kill_worker_drill.py` | A04 真实杀 Worker 恢复演练 |

## 6. 未完成清单（下次逐项推进）

### V3（当前重点）

1. **V3-05 剩余**：背景目前是程序化图案，未接真实 AI 生成背景；**阴影 / 反射 / 人物遮挡未做成独立层**。
2. **V3-06 双产品检测**：背景出现多余产品影像要能检出并阻断（未开工）。
3. **V3-07 Strict QA**：Logo 缺失、轮廓异常、尺寸变化、Mask/ID 错误、缺帧的自动阻断（未开工）。
4. **V3-08 版本失效联动**：产品版本变化使旧计划的保真审批失效（未开工）。
5. **V3-09 审核界面**：版本审核页 + 通道查看器 + 质检页（未开工）。
6. **V3-10 验收报告**（未开工）。

### V2 剩余（非阻塞，不挡 V3）

- 多任务长时压测（稳定性 / 失败率 / 显存泄漏）、费用换算、运行中任务的协作式取消。

## 7. 不适合放进 Git 的东西（已按此处理）

| 内容 | 现状 |
| --- | --- |
| 用户产品图 | 本机 `真实素材/`、云端 `/home/ubuntu/pd-user-assets/`；**不入 Git** |
| 官方 STEP 模型 | 本机桌面 `AAA104.STEP`、云端 `/home/ubuntu/pd-official/`（及其转出的 GLB）；**不入 Git** |
| CC0 材质素材（Polyhaven `Camera_01`） | 云端 `/home/ubuntu/pd-cc0/`；来源与许可记录在 V3 任务书第 7 节 |
| H3 云端登录信息 / access.json | 仅在本机 `ComfyUI-Cloud-H3/云端登录信息.txt` 与 `access.json`；**不入 Git、不进聊天** |
| SSH 私钥、服务端密钥 | 本地 / 云端各自保存；**不入 Git、不进聊天记录** |
| 渲染产物、数据库、模型权重 | `var/`、`/tmp`、云端 `output/`；`.gitignore` 已覆盖 |

## 8. 建议的下一次操作

1. 先确认云防火墙放行了**当前**出口 IP，再跑一次本地测试与云端测试，确认环境没漂移（期望 152/152）。
2. 从 **V3-06 + V3-07** 继续：把"坏结果自动拦住"做出来。V3-07 的 Logo / 材质类故障必须**用 CC0 相机素材验证**——官方 STEP 转出的 GLB 没有材质，验不了 Logo 缺失。
3. 如果决定先补 V3-05 的独立层（阴影 / 反射 / 遮挡），顺序是：Blender 输出分层通道 → 合成器按层叠加 → 再回到 V3-06。
4. 每完成一项就更新 `docs/reports/V3_TASK_BRIEF.md` 的状态与证据，并提交推送；不要在没有证据时把任务标成完成。

## 9. 工程纪律（不要因为赶进度破例）

- `READY_FOR_REVIEW` 与 `ACCEPTED` 是两件事，只有用户能判 ACCEPTED。
- 没跑通的不写"完成"；发现的缺陷（例如 4 步采样产出碎片几何）如实写进报告。
- 不为了"看起来快"降低分辨率、时长或质量门。
- 严格保真模式**禁止用生成视频重绘可见产品主体**；图片不会被赋予物理环绕能力。
- 不自动购买云资源、不自动调用收费 API、不把任何密钥或私密素材提交仓库。
