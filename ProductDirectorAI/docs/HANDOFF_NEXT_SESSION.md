# 下一次操作交接说明（2026-09-13 云端会话后更新）

这份文件是**下次开工的第一入口**。先读它，再按需展开下面的报告。2026-09-13 在云端完成了两条开发线合并、V3-05 独立层真实闭环、V3-06/V3-07 验收组装、V4-01…07 与 V5-01…05；本节第 1/6/8 节已按新状态更新。提交：`b260f1f`（合并）、`edc2df7`（build_layers 帧号修复）、`c8f0396`（`--occluder` 独立遮挡物 GLB）、`5c8eb6b`（V3-06 审核界面）、`854d133`（V4-06 人物与互动界面）、`be33bcb`（V4-07 击打）、`bdf3bac`（V5-01）、`0e2b220`（V5-02）、`b289dd4`（V5-03/04）及本轮 V5-05 提交，均已推送 GitHub `main`；云端另有本地分支 `wip-v305-layers`。

> 阅读顺序建议：本文件 → [当前阶段](CURRENT_PHASE.md) → [V3-05 独立层真实闭环证据](reports/V305_LAYERS_REAL_EVIDENCE_2026-09-13.md) → [V3 任务书](reports/V3_TASK_BRIEF.md)。
> `NEXT_COMPUTER_START.md` 是 2026-09-11 换电脑时写的，其中"V1 仍为 PARTIAL""云 SSH 超时"等描述**已被本文件取代**，只作历史留档。

## 1. 当前状态（都已验证，不是计划）

| 项目 | 状态 |
| --- | --- |
| **V1** | **ACCEPTED**（用户 2026-09-12 确认，见 V1 验收确认单） |
| **V2** | **ACCEPTED**（同日确认） |
| **V3** | **进行中**：V3-01/02 通过；V3-03/04 完成；**V3-05 独立层真实闭环 PASS**（真实 H3 背景 + 三层独立层 + 真实遮挡物 + 双口径 + 双产品零误报）；**主规划 V3-04 QA 闭环补强**（阈值集/审批绑定/终态降级修复 + 四类故障注入）；**主规划 V3-06 审核界面已上线**（保真审核页三页 + 问题帧定位 + 线上真实审批 E2E，见 V306 报告）；**主规划 V3-07 真机校准与验收组装**（[V3_ACCEPTANCE 草稿](reports/V3_ACCEPTANCE.md)，七项未完成待人工复核） |
| **V4** | **进行中**：V4-01…06 全部落地（合同/校验引擎/渲染侧 Proxy 预演/人物能力/真实人物遮挡合成/人物与互动界面）+ 击打真实几何（V4-07）；[V4_ACCEPTANCE 草稿](reports/V4_ACCEPTANCE.md) 五项未完成（真人感人物层/双人动作/两轮人工审核/修订流程/浏览器走查） |
| **V5** | **进行中**：V5-01 摄取、V5-02 切镜（10/10 合成夹具 F1=1.0）、V5-03 观察/推断分离、V5-04 计划映射（误差 ≤1 帧/镜头、≤2 帧总）、**V5-05 参考重演双栏工作台已上线**（线上冒烟 10/10，见 [V505 报告](reports/V505_REENACTMENT_UI_2026-09-13.md)）、**V5-06 重演生成已完成**：3 条真实成片（Blender 五通道 + 真实 H3 背景 + Strict 合成 + 双产品 + QA 全过；RE1 75 帧 24/29/22 误差 0.00/0.01/−0.01 含 V4 `press_button` 互动镜头接触帧 46/46、间隙 0.1mm、穿透 4mm；RE2 48 帧；RE3 72 帧）+ Run manifest 引用溯源，见 [V5-06 报告](reports/V506_REENACTMENT_EVIDENCE_2026-09-13.md) 与 [V5_ACCEPTANCE 草稿](reports/V5_ACCEPTANCE.md)。剩：10 条许可参考素材人工标注夹具 + 浏览器人工走查（负责人参与） |
| **V6** | **已开工**：**V6-01 Platform Profile 与后期模板完成**（六组字段的版本化合同、六个种子 Profile、BLOCKING/WARNING 校验、成片兼容性、后期模板与配音适配策略；硬限制/规则/账号能力全部如实标注未核验或未配置；线上冒烟 16/16，见 [V6-01 报告](reports/V601_PLATFORM_PROFILE_2026-09-13.md)）；剩 V6-02…16（es-MX 文案/字幕/TTS、BGM/ducking/响度、Batch、发布包、成本账本、Automation/Webhook、总控台、四个平台连接器、权限与上线手册） |
| 后端测试 | **409/409**（云端，2026-09-13，V6-01 之后全量） |
| 前端 | build ✓ + Sites **4/4**（云端，2026-09-13） |
| 代码 | GitHub `main` = V5-06 提交，云端工作区干净 |
| 云端 | api / web / postgresql / comfy-h3 四个 systemd 服务均 active |

明确没有完成的：V3/V4/V5 阶段门（负责人人工复核三份 ACCEPTANCE 草稿）、V4 尾巴（真人感人物层/双人动作/修订流程/浏览器走查）、V5 的 10 条许可参考素材人工标注、V6-02…16。不能对外说"V3 完成"、"V5 完成"或"V6 完成"。

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
| `scripts/strict_composite.py` | **V3-05 Strict 合成**（冻结合同 + 可信 Alpha + 预检隔离 + `--layers shadow/reflection/occlusion` 独立层与遮挡豁免） |
| `scripts/build_layers.py` | **V3-05 独立层构建**（plate_full × plate 差分；输出帧号沿用输入帧号） |
| `scripts/h3_background.py` | **V3-05 真实 AI 背景**（自托管 ComfyUI，不收费；失败如实 BLOCKED） |
| `scripts/dual_product_check.py` / `scripts/strict_qa.py` | V3-06 双产品检测 / V3-07 Strict QA（云端真实证据见任务书 §12） |
| `blender/scripts/render_product.py` | 渲染器；`--passes` 五通道，`--layers` 追加 plate_full/plate/occlusion 独立层素材 |
| `blender/scripts/validate_fidelity_passes.py` | **V3-02 通道校验** + `--layers` 独立层校验（不依赖 Blender，venv 的 Pillow + OpenEXR） |
| `scripts/kill_worker_drill.py` | A04 真实杀 Worker 恢复演练 |

V3-05 真实流水线的一次完整执行脚本序列在云端 `/tmp/step{1..7}_*.sh`（证据目录 `/home/ubuntu/pd-v305-layers-20260913/`，不入 Git）。

## 6. 未完成清单（下次逐项推进）

### V3（当前重点）

1. **V3-05 剩余**：遮挡物自身投影的逐帧人工比对与真实人物遮挡素材（物体遮挡已用 `--occluder` 真实验证；540×960 与 1080×1920 两个口径已复验）；shadow/reflection 的 scene-linear 全链路映射；主规划 V3-05 的单图/多视图真实候选审核样例与相机限制运行时验证。
2. **主规划 V3-04 剩余**：阈值集真实数据集校准冻结（当前默认阈值集已版本化 + 防放宽校验）；ID 通道语义（无 Object Index，以产品遮罩渲染代替并如实记录）。
3. **主规划 V3-06 剩余**：浏览器人工走查（页面已上线：保真审核 → 版本审核/质检报告/通道查看 + 问题帧定位 + 审批按钮注明 Manifest hash；线上真实 E2E 已过，见 V306 报告）；运动镜头 Logo 位姿感知映射。
4. **主规划 V3-07**：**V3_ACCEPTANCE 草稿已产出**（两类产品×3 镜头×2 背景全矩阵、真机校准、legacy 回归、性能/存储、六类故障注入汇总，见 [V3_ACCEPTANCE](reports/V3_ACCEPTANCE.md)）；剩余：负责人人工复核 + 草稿第 6 节七项（阈值集校准冻结、真实候选审核样例、1080×1920 官方模型、更长镜头、透明材质显式不支持声明等）。

### V2 剩余（非阻塞，不挡 V3）

- 多任务长时压测（稳定性 / 失败率 / 显存泄漏）、费用换算、运行中任务的协作式取消。

### V5（已开工，V4 仍待负责人复核、不宣布 PASS）

1. **V5-01 已完成**：参考视频摄取（本地上传入口 `POST /references/upload` + `POST /references` 素材/URL 两种来源）、ffprobe/ffmpeg 分析代理与 `source_to_proxy_map` 时间戳映射、URL 获取约束（仅公网 http(s) 视频、防 SSRF、大小/类型上限、失败提示本地上传）、代理取回接口；4 例测试 + 线上真实冒烟。
2. **V5-02 已完成**：本地切镜（ffmpeg scene SAD 硬切 + 渐变转场聚簇单独评分）、人工修订（仅草稿、新修订 + edited_from）、批准冻结；`evaluate_cuts` 指标对照（±0.2s，10/10 合成夹具 F1=1.0 过 0.90 门）；**剩余**：10 条真实许可参考片的人工标注夹具与运动镜头复验。
3. **V5-03 已完成**：逐段确定性观察（测量事实与启发式推断分离，每项 value/confidence/evidence/method；景别/焦距/相机路径/主体位置如实 null）；**剩余**：多模态模型接入（未配置，当前为确定性基线）。
4. **V5-04 已完成**：`POST /projects/{id}/plans/from-reference`（仅已批准分析版本）适配规划 + ReferenceMapping 冻结引用；保留时长每镜头误差 ≤1 帧、总误差 ≤2 帧（测试与线上冒烟验证）。
5. **V5-05 未开工**：双栏播放器、对齐时间线、差异与确认（端到端对照操作）。
6. **V5-06 未开工**：重演生成、原视频引用追踪、回归、`V5_ACCEPTANCE.md`（至少 3 条真实重演，含 1 条 V4 互动镜头）。

### V4（已开工，V3 仍待负责人复核、不宣布 PASS）

1. **V4-01 已完成**：锚点/人物/动作模板合同 + 版本化 + bbox→世界坐标变换（`interaction_geometry.py`），7 例测试。
2. **V4-02/03 合同与校验已完成**：交互计划（版本化 + 锚点集/产品版本绑定 + 失效联动）、纯数学校验引擎（接触/穿透/时序阈值）、数据版预演时间线、**渲染侧 Blender 人体 Proxy 预演 + 真实几何接触 QA 已闭环**（正例/负例真实验证，见 V402 报告），13+ 例测试；**未开工**：预演帧人工目检、击打（single_punch_target）真实几何与 IK、锚点编辑 UI、动作模板导入 UI。
3. **V4-04 已完成**：人物素材引用校验（存在 + Owner 归属）+ 能力报告（生成路线如实 NOT_CONFIGURED、授权素材逐引用状态、usable_for_final_person_layer）。
4. **V4-05 核心已完成**：真实人物遮挡合成（Proxy 人物层 72 帧、豁免 455 万像素、可见区域保真、双产品零误报）；接触 QA 由 V4-02/03 几何报告覆盖。**未做**：真人感人物层（生成工作流未配置 / 授权素材真实素材）、击打（single_punch_target）与双人非同时动作、合成帧人工目检。
5. **V4-06 已上线**：人物与互动界面（计划与锚点/人物/新建交互计划/校验与预演时间线），线上冒烟通过；**剩余**：浏览器人工走查、问题帧修订流程 E2E。
6. **V4-07 部分完成**：击打（single_punch_target）真实几何正例已验证（120 帧，接触 79/80、间隙/穿透 0，引擎奇偶一致）；[V4_ACCEPTANCE 草稿](reports/V4_ACCEPTANCE.md) 已产出；**未做**：双人非同时动作（按主规划单人验收后扩展）、回归收尾、真实成片两轮人工审核。

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

1. 先确认云防火墙放行了**当前**出口 IP，再跑一次云端测试确认环境没漂移（期望 **349/349**）。
2. **V3 收尾**：请负责人完成 [V3_ACCEPTANCE](reports/V3_ACCEPTANCE.md) 的人工复核（合成帧目检、H3 背景主观质量、问题帧热图），并决定第 6 节七项（阈值集校准冻结、真实候选审核样例、1080×1920 官方模型、透明材质显式不支持等）的处置；全部确认后 V3 才可标 PASS。
3. V3 阶段门关闭后按主规划进入 **V4**（锚点/人物/动作版本、Proxy、IK/时序/碰撞）。
4. 每完成一项就更新文档与证据，并提交推送；不要在没有证据时把任务标成完成。

## 9. 工程纪律（不要因为赶进度破例）

- `READY_FOR_REVIEW` 与 `ACCEPTED` 是两件事，只有用户能判 ACCEPTED。
- 没跑通的不写"完成"；发现的缺陷（例如 4 步采样产出碎片几何）如实写进报告。
- 不为了"看起来快"降低分辨率、时长或质量门。
- 严格保真模式**禁止用生成视频重绘可见产品主体**；图片不会被赋予物理环绕能力。
- 不自动购买云资源、不自动调用收费 API、不把任何密钥或私密素材提交仓库。
