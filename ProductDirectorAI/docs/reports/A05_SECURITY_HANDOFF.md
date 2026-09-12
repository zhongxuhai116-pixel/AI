# A05 安全远程闭环 · 换电脑交接

日期：2026-09-11。结论：**PARTIAL / 本地安全边界已验证，云端联调未完成**。

## 本次变更

- 私有单 Owner 部署使用服务端随机访问密钥换取 8 小时 HttpOnly、SameSite=Strict Cookie。数据库只保存会话 token 哈希；登出删除会话，过期或 Owner 密钥轮换使旧会话失效。
- Cookie 写请求要求精确允许的 Origin 和会话 CSRF token，不依赖 User-Agent 判断；错误返回 JSON 401/403/503。
- API、素材、任务、Provider 状态及 API 文档需认证；请求中的 Owner 必须匹配登录身份。任务详情、列表、取消、事件、下载均核对项目归属；素材记录绑定 Owner。
- 内部 HTTP Worker 接口要求独立密钥，且请求 Worker ID 必须匹配配置。Owner 密钥不能调用 Worker 接口，Worker 密钥不能读取 Owner API；不配置密钥时拒绝。
- 前端修复 API 地址正则语法错误，默认使用同源 `/api/v1`，Vite dev/preview 提供受控后端代理。登录密钥不进入构建或本地存储；CSRF token 仅保留内存。
- 新上传素材和产物使用相对存储引用；实际读取仍核对解析后的授权目录，产物限于当前 job 子目录。旧绝对路径仅在仍属于当前授权目录时兼容，不自动迁移 Windows 数据。
- Linux 使用 [cryptography Fernet](https://cryptography.io/en/latest/fernet/) 认证加密。禁止默认密钥、不安全草稿加密与明文回退；缺少密钥、格式不兼容、密文损坏均拒绝。Windows DPAPI 保留。
- MiniMax API 基址限制为允许的官方 HTTPS 主机，兼容末尾 `/v1`，防止重复拼接 `/v1/v1`。未访问真实 Provider。

## 本地验收证据

环境：Windows，Python 3.12.14，现有 Node / Vite 6.4.2。测试使用隔离临时数据库和通用 GLB 夹具。

| 检查 | 本次结果 |
| --- | --- |
| npm 11.6.2 `ci --prefix work/a05-clean-install-20260911`（隔离空目录） | PASS，安装 67 个包，lockfile 与 package.json 一致 |
| `python -m compileall -q apps/api tests` | PASS |
| `python -m unittest discover -s tests -p 'test_*.py'` | PASS，23/23（原 16 + 新增安全 7） |
| `node node_modules/vite/bin/vite.js build` | PASS；包体积提示，不是构建失败 |
| `node scripts/prepare-sites-build.mjs` | PASS；保留 Sites 打包结构 |
| `node --test tests/sites-worker.test.mjs` | PASS，4/4 |

安全用例覆盖：匿名/角色错误/缺配置拒绝，登录、有效 CSRF 上传、错误 CSRF/缺 Origin/伪造 User-Agent 拒绝，登出/过期/密钥轮换，Worker 身份匹配，跨 Owner 素材与跨项目任务访问，产物目录穿越，Linux 加解密与损坏/缺配置拒绝，Provider 地址白名单。

原 A04 合同测试隔离 Worker 鉴权，用于验证租约语义；新安全测试使用真实依赖和中间件，不绕过鉴权。worker-once 测试的渲染与 ffprobe 是 mock；授权视频/Manifest 下载通过同一测试夹具验证，不能记作真实云出片。

## 尚未验收 / 新电脑继续

1. 本次 SSH 认证前连接超时；无云端部署、新云端版本哈希或本次真实产物证据。先确认实例运行状态、当前地址、现有 SSH 访问策略与密钥，不直接开放公网服务。
2. A04 的领取/完成/失败检查存在跨事务竞争窗口；长渲染未持续续租，渲染进度更新还未全部按 epoch 拦截。取消/完成竞争、进程终止后重领、编码失败重试、独立 Worker 持续运行仍须补测修复。
3. 当前 Worker 本地 CLI 直接使用同机数据库/文件系统；HTTP 身份边界不等于远程任务输入下载、受限成果上传、真实云任务与回收已实现。不得宣告 A05 整包通过。
4. 尚未进行新登录页完整浏览器视觉/交互验收、TLS 反向代理部署或多用户管理。限制为私有单 Owner、回环/受控隧道场景；不适用于直接公网开放。
5. 老数据库自动增加素材 Owner 列默认归属原单 Owner；真实数据库切换前仍需私密备份和迁移演练。Linux 分支本轮通过模拟平台的单元测试，不代替真实 Linux 凭证保存验收。
6. V1 完整合同、PostgreSQL/恢复、用户素材、质量和 A06/A07 门均未结清。用户已授权 V6，但不能越级验收。

---

## 远程 Worker 受限输入/输出通道（2026-09-12 追加）

上面第 3 条已处理：Worker 不再必须与 API 同机读写数据库和文件系统。

### 新增接口（全部要求 Worker 令牌 + 有效租约）

| 方法 | 路由 | 作用 |
| --- | --- | --- |
| GET | `/internal/v1/workers/jobs/{id}/input` | 返回冻结的 DirectorPlan、输出规格与素材**下载地址**（不暴露文件系统路径） |
| GET | `/internal/v1/workers/jobs/{id}/input/asset` | 流式返回该任务的输入素材（需有效租约） |
| POST | `/internal/v1/workers/jobs/{id}/artifact` | 回传产物：文件名白名单（`preview.mp4` / `metadata.json`）、大小上限（400MB / 8MB）、且只允许写进该任务自己的目录 |

回传成功后返回 `storage_reference`，Worker 用它调用既有的 `complete` 接口即可。

### 测试（`tests/test_worker_remote_channel.py`，4 项）

Worker 令牌与租约校验、素材字节与文件名、白名单/空文件/超限拒绝、过期租约拒绝、以及"**新接口最初漏了 worker 令牌依赖**"这个真实缺陷（匿名请求返回 200 被测试当场抓到，现已修复）。

后端回归：本地与云端 **128/128**。

### 云端真实往返

```
claim            : 200  任务被 Worker 领取（lease_epoch 正常返回）
input            : 200  3 个镜头 + 素材下载地址（无 path 字段）
asset download   : 200  5,460 字节  sha 3d590f7fe5bdabd5（与仓库夹具一致）
artifact upload  : 200  preview.mp4（2,060 字节）与 metadata.json
complete         : 200  SUCCEEDED（使用回传的 storage_reference）
owner downloads  : 200  2,060 字节 —— 与 Worker 上传的字节一致
```

即：只持有 Worker 令牌的一方，**不需要数据库或文件系统权限**就能完成"取输入 → 本地渲染 → 回传产物 → 结单"的全流程；产物仍由 Owner 侧受鉴权下载。

### 仍未完成

- 本次验证在同一台机器上通过回环 HTTP 完成；跨机（真正远程）部署未做——通道本身与位置无关，但未在第二台机器上实测。
- 未做上传内容的深度校验（例如视频必须能被 ffprobe 解码），当前只做文件名、大小与租约边界。

本报告随源码提交；准确代码版本以包含本文件的 Git 提交为准。GitHub 推送结果由交接时远端 HEAD 校验，未包含私钥、凭证、数据库、模型或成片。
