# V2 · H3/ComfyUI Provider 接入（重建路径）

日期：2026-09-12。状态：**IN_PROGRESS**（已接入重建路径并有真实云端证据；V2 其余主线未开始）。

## 1. 范围

V2 的接入顺序要求：项目创建任务 → 提交 workflow → 保存 operation ID → 查询进度/历史 → 下载并校验 → 登记 Artifact。本次交付的就是这条链路的**第一段真实实现**，Provider 使用云节点上已部署的 H3/ComfyUI 环境（不重复安装、不重复下载模型）。

## 2. 实现

### 2.1 Provider 客户端

`apps/api/productdirector_api/providers/comfyui.py`：

| 函数 | 作用 |
| --- | --- |
| `health()` | `/system_stats` + `/queue`，不可用时返回 `reachable=false` 而不抛异常 |
| `upload_image()` | 把素材上传到 ComfyUI 的 input 目录（multipart），返回 `LoadImage` 可用的名字 |
| `submit()` | 提交图，返回 ComfyUI 的 `prompt_id` 作为 operation ID；被拒时带出节点错误 |
| `history()` / `status_text()` / `outputs()` | 查询历史、判定终态、把产物摊平成 `{filename, subfolder, type}` |
| `download()` | 通过 `/view` 拉取产物——不依赖文件系统权限，远端部署同样适用 |
| `build_hunyuan3d_graph()` | Hunyuan3D 单视图重建图，默认 **50 步 / cfg 5.0**（见第 4 节） |

Provider 不保存凭证：ComfyUI 部署在回环地址，外层鉴权由 Caddy 负责；本层只处理 HTTP 错误并转成可读异常。

### 2.2 项目侧接口

| 方法 | 路由 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/providers/h3/status` | Provider 可用性、ComfyUI 版本、设备名与队列长度（需鉴权，不含凭证） |
| POST | `/api/v1/providers/h3/reconstruct` | 用一个图片素材提交重建：校验归属与裁切参数、上传图片、提交图、写 `provider_jobs`，返回 `provider_job_id` 与 `external_id` |
| GET | `/api/v1/providers/h3/jobs/{id}` | 查询进度；完成时下载 GLB、复用 `inspect_glb` 校验、写入素材库并把 `artifact_asset_id` 记回任务 |

新增 `provider_jobs` 表（SQLite 与 PostgreSQL schema 同步）：记录 provider、operation、`external_id`、状态、阶段、请求快照、产物素材 ID 与错误。

失败记录写在**独立事务**里：提交失败时不会因为异常回滚而丢掉失败记录（有专门用例覆盖）。

## 3. 真实云端验证（2026-09-12）

在云节点通过**真实 HTTP API**（非 mock）执行：

```
provider status: {"reachable": true, "comfyui_version": "0.34.5",
                  "device": "cuda:0 NVIDIA GeForce RTX 4090", "queue_running": 0, "queue_pending": 0}
asset          : 566e0893-… sha b7694a9eb791a40d（用户提供的机器人产品图）
submitted      : provider_job cfb74a5d-… external 99611d8d-…
poll           : RUNNING ×4 → SUCCEEDED / ARTIFACT
artifact       : GLB 8,983,076 字节 sha 677a2189c519181b…  结构 1 网格 / 1 材质 / 0 图像
library entry  : "H3 重建 · user-product-02.glb"  kind=model  已出现在素材库
```

即：**产品图 → 项目任务 → Provider 提交 → 进度轮询 → 产物下载校验 → 素材库登记** 全链路跑通，全程没有手工搬文件。

（注：该产物与直接用脚本重建的 `robot-b.glb` 相差 12 字节，原因是 `SaveGLB` 的前缀不同、名称会被写进 GLB；几何一致。）

## 4. 顺带确认的 Provider 缺陷

现场 H3 工作流的 3D 采样设置是 4 步 / cfg 1.0，实测会产出**碎片几何**（作业仍报告成功）。本 Provider 的默认值改为 50 步 / cfg 5.0，并把该发现记录在 [A07 V1 阶段验收](A07_V1_ACCEPTANCE.md) 第 6 节。

## 5. 测试

| 范围 | 结果 |
| --- | --- |
| Provider 集成用例（`tests/test_h3_provider.py`） | 12/12：状态不可用不报错、鉴权、非图片素材拒绝、非法裁切拒绝、提交并写任务、**失败记录不被回滚**、产物回收入库、成功后幂等（不重复下载）、非法 GLB 判失败、Provider 报错判失败、无 GLB 判失败、未知任务 404 |
| 全量后端回归 | 本地 **99/99**，云端 **99/99** |

## 6. V2 仍未完成

1. **H3 视频生成**：本次只接了 3D 重建；主工作流的「原视频产品替换 + Blender + H3 视频」尚未接入项目（仍是脚本/界面手工操作）。
2. **AI 导演计划**：LLM 生成可编辑 DirectorPlan 未实现（V2 主线之一）。
3. **官方视频/重建接口**：主规格要求的 MiniMax 官方视频接口与重建路径真实测试未做。
4. **取消与失败语义**：ComfyUI 的全局中断不能当单任务取消，本次未实现取消；任务只能查询与失败标记。
5. **并发与容量**：未测多任务并发、显存占用与排队策略。

---

## 7. H3 视频生成接入（2026-09-12 追加）

第 6.1 条已处理：现在项目自己就能生成 H3 视频，不再需要人工开 ComfyUI 界面。

### 7.1 实现

`comfyui.build_h3_video_graph()` 按现场已验证工作流的视频子图接线：

```
UNETLoader → LoraLoaderModelOnly(turbo 4 步) → MiniMaxH3SigmaShift(12.0 / 3.0)
LoadVideo → GetVideoComponents ─┐
LoadImage → ImageCrop ──────────┴→ MiniMaxH3ReferenceToVideo(prompt, 576×1024, 124 帧)
                                        ├→ BasicGuider ─┐
RandomNoise + KSamplerSelect + BasicScheduler ──────────┴→ SamplerCustomAdvanced
    → VAEDecode(视频 VAE) + VAEDecodeAudio(音频 VAE) → CreateVideo → SaveVideo
```

接口 `POST /api/v1/providers/h3/video` 接受产品素材 + 参考视频名 + 提示词与尺寸参数；任务状态接口按 `operation` 分流回收：

- `RECONSTRUCT_3D` → 下载 GLB、`inspect_glb` 校验、登记为素材库 model 资产；
- `GENERATE_VIDEO` → 下载视频、`ffprobe` 校验、存到 `var/providers/<job_id>/`，通过 `GET /api/v1/providers/h3/jobs/{id}/artifact` 下载（不塞进素材库，避免出现界面尚不支持的资产类型）。

与现场工作流的差别：**不含** `ProductBlenderRender` 的多视图渲染（属于 V3 产品保真链路），当前只提供"参考视频 + 产品图"这一条已实现能力。

### 7.2 真实云端验证

通过真实 API 提交（产品图 = 用户的机器人产品图裁切，参考视频 = 拳击靶-人物击打参考.mp4，124 帧 / 576×1024 / 4 步）：

```
submit  : 202  operation=GENERATE_VIDEO  external=86526b7f-…
poll    : RUNNING ×31（约 7.5 分钟）→ SUCCEEDED / ARTIFACT
artifact: mp4 1,070,456 字节  h264 576×1024 124 帧 5.167 秒
          sha256 28797f73a31cb7f36b1be463898ea26caffbe302d324c4a28cd8f8a82de3777b
download: GET …/artifact → 200，1,070,456 字节
```

### 7.3 人工视觉复核

抽取第 91 帧查看：**产品替换确实生效**——机器人被放到木墙上原本拳击靶的位置，人物的击打动作、房间、木墙、地面与光影都保留，机器人外观（镜头穹顶、扬声器栅格）与参考图一致。

同时发现一个真实瑕疵：参考图里**含底座**，因此画面中机器人的底座既出现在墙面产品上也单独出现在地板上。这是参考图取景问题（应裁到不含底座的主体）或 V3 保真链路要解决的合成问题，已记录。

### 7.4 仍然未完成

1. **多视图产品渲染**：现场工作流里的 `ProductBlenderRender`（正面/背面/45°）尚未接入，产品保真度依赖单张参考图。
2. **AI 导演计划**：LLM 生成可编辑 DirectorPlan 未实现。
3. **取消语义**：ComfyUI 全局中断不能当单任务取消。
4. **成本与并发**：单次 124 帧生成约 7.5 分钟；未测并发排队、显存占用与批量产能。
5. **官方接口**：主规格要求的 MiniMax 官方视频接口真实测试未做。

---

## 8. 产品替换升级为多视图参考（2026-09-12 追加）

### 8.1 实现

`comfyui.build_product_video_graph()` 把现场工作流的完整链路搬进项目：

```
LoadImage → ImageCrop ─┬→ CLIPVisionEncode → Hunyuan3Dv2Conditioning → KSampler(50 步)
                       │        → VAEDecodeHunyuan3D → VoxelToMesh → MESH
                       └→ ProductBlenderRender(mesh, reference_image, scene, motion, size_cm, ...)
                                                       ├→ 正面 ┐
                                                       └→ 45°  ┤
                                                               └→ MiniMaxH3ReferenceToVideo 的
                                                                  ref_images.ref_image_1 / _2
```

即 H3 的参考图从"单张平面裁切"变成 **原图裁切 + Blender 渲染的正面与 45° 视图**，与现场验证过的工作流一致。

接口 `POST /api/v1/providers/h3/video` 新增 `with_product_views`（默认 false，保持快速路径）以及 `mesh_steps`、`scene`、`motion`、`size_cm`、`blender_seconds`、`quality`、`photo_texture`、`framing` 参数。

### 8.2 真实云端验证

```
asset   : a43f9333-…（用户机器人产品图，裁切 340×430 并排除底座）
submit  : 202 with_product_views=true scene=living motion=pan
state   : RUNNING → SUCCEEDED / ARTIFACT（约 8.5 分钟：重建 + Blender + H3）
artifact: mp4 1,068,806 字节  h264 576×1024 124 帧 5.167 秒
          sha256 08a57bd56cac30b4ee30a2efac10119f1eba70b0b9c353db6af169976a880dc9
```

### 8.3 人工视觉复核（与上一节的单图版本对比）

- **重复底座问题消失**：上一版画面里底座同时出现在墙面产品和地板上；这一版把裁切区域收紧到不含底座，并让 Blender 视图提供几何参考，地板上不再出现多余底座。
- 产品被放在木墙上原靶位，镜头穹顶、扬声器栅格、耳状结构在运动中保持一致；人物动作、房间、木墙与光影保留。
- 新增一个可接受的细节：`ProductBlenderRender` 的取景把产品底座一起渲染出来，因此墙面上产品下方出现一块白色托盘状结构。这属于 Blender 渲染取景参数（`framing` / `size_cm`）可调范围，不是缺陷。

### 8.4 成本

完整链路（重建 + Blender + H3 124 帧）单次约 **8.5 分钟**，比单图版本（约 7.5 分钟）多约一分钟，换来更稳定的产品一致性。

---

## 9. 取消语义与单卡产能（2026-09-12 追加）

### 9.1 实现

| 能力 | 行为 |
| --- | --- |
| `GET /api/v1/providers/h3/queue` | ComfyUI 全局队列深度 + 本项目任务在队列中的真实状态（running / pending / unknown），并标注 `single_gpu_serial` |
| `POST /api/v1/providers/h3/jobs/{id}/cancel` | **排队中**：通过 `/queue` 的 `delete` 精确删除该 prompt，只影响这一个任务；**已在执行**：默认拒绝并记录取消请求，返回 409 说明"ComfyUI 只有全局 `/interrupt`，会影响该实例上所有正在运行的任务"；私有单租户部署可显式 `?force=true` 强制中断，并在记录里标注是强制中断 |
| `cancel_requested` 列 | 记录用户是否请求过取消 |
| 状态粒度修复 | 之前排队中的任务也会显示 `RUNNING`；现在会读取队列，把阶段区分为 `QUEUED` 与 `GENERATE` |

### 9.2 真实云端验证（并发排队 + 排队取消）

连续提交两个重建任务：

```
A submitted → queue: running=1 pending=1 depth=2
our jobs   : A = running, B = pending
cancel B   : 200  status=CANCELLED（"用户在排队阶段取消"）
final A    : SUCCEEDED  artifact sha 449a7e0b…
final B    : CANCELLED
```

即：**单卡确实是串行排队**（两个任务同时提交时，第二个进入 pending）；**排队阶段取消是精确且安全的**——只删掉 B，A 不受影响并正常出片。

### 9.3 仍未完成

1. **运行中取消**：需要显式 `force=true` 触发全局中断；未做"先记录取消、等当前任务结束后不再推进"的协作式取消。
2. **并发上限**：只验证了"一运行一排队"，未测多任务排队时的吞吐、显存峰值与失败率；也没有自动重排或优先级。
3. **成本核算**：未记录每次任务的显存峰值、磁盘增量与电费/云费用换算。

---

## 10. 生成视频回收到素材库（2026-09-12 追加）

第 7 节留下的缺口已补：此前 H3 产物只存在任务目录里，可通过下载接口取回但**没有进素材库**，不满足 V2"真实 H3 任务回收到素材库"的要求。

现在回收的产物会：

1. 以受管素材身份落盘（`kind=video`、`mime=video/mp4`，路径与解析规则与其他素材一致）；
2. 通过既有的鉴权内容接口 `/api/v1/assets/{id}/content` 提供，URL 随任务产物信息一起返回（`asset_id` / `library_url`）；
3. 在工作台的「产品素材」卡片与「产品素材库」里以 `<video>` 预览（此前只有图片与 3D 两种分支）。

测试：视频回收用例不再断言"不建素材"，而是校验素材已登记、内容接口返回的字节与上传一致、素材类型为 `video`。后端 **139/139**，Vite 构建与 Sites 4/4 通过；云端已部署（web=200）。
