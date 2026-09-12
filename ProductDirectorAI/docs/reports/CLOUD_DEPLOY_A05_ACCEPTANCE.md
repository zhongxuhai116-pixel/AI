# 云端部署与双链路出片验收（A04/A05 代码，2026-09-12）

结论：**本轮代码已真实部署到云节点，并在新代码上完成图片与 GLB 两条真实链路的出片验收**。V1 仍未整体 ACCEPTED（用户真实素材、稳定性对照、远程 Worker 等门未结清）。

## 1. 部署前的真实状态（先复验，再改动）

| 项目 | 部署前事实 |
| --- | --- |
| 云端代码 | `68532ec`，落后本地 `main` 6 个提交 |
| 云端工作区 | 有未提交改动（`main.py`、`App.jsx`、`render_product.py`）与未跟踪的 `tests/test_director_plan.py` |
| `productdirector-v1-api` | active |
| `productdirector-v1-web` | **崩溃重启 305 次**：`Cannot find module '.../apps/web/node_modules/vite/bin/vite.js'` |
| 鉴权 | 旧代码无 A05 鉴权，`/api/v1/health` 匿名可访问 |

部署前已把云端工作区补丁、未跟踪文件与两个 systemd 单元备份到 `/home/ubuntu/pd-backup-20260912-093845`，再用 `git stash push --include-untracked` 保存本地改动后 `git merge --ff-only origin/main`。

## 2. 本轮部署动作

1. 云端仓库快进到 `8e6fe3c`（含 A04 恢复加固、A05 安全边界与验收脚本）。
2. `.venv/bin/pip install -r apps/api/requirements-test.txt`；`apps/web` 执行 `npm ci`（67 个包）与 `npm run build`，恢复 `dist/client` 与 `dist/server`，修好 web 服务缺依赖导致的崩溃重启。
3. 生成 `/etc/productdirector/v1.env`（`root:root`、`0600`）：独立 Owner 密钥、Worker 密钥、Worker ID 与 Fernet 密钥。**密钥不进入 Git、不进入聊天记录**；验收脚本在服务器上以 root 读取该文件后注入环境变量。
4. 两个 systemd 单元加入 `EnvironmentFile=/etc/productdirector/v1.env`，`daemon-reload` 后重启。

部署后状态：

| 检查 | 结果 |
| --- | --- |
| `systemctl is-active` | api = active，web = active |
| `/api/v1/health` 携带 Owner 令牌 | 200 |
| `/api/v1/health` 匿名 | **401**（A05 鉴权生效） |
| `http://127.0.0.1:4173/` | 200 |

## 3. 双链路真实验收

使用仓库内可复跑脚本 `scripts/v1_cloud_acceptance.py`，通过真实 HTTP 会话（登录 + CSRF）、真实上传、真实后台作业、真实出片与真实下载完成。产物目录：`var/acceptance/20260912T014015Z`。

### 3.1 图片链路

| 指标 | 结果 |
| --- | --- |
| 作业 ID | `3acb27ad-ecec-481f-856a-c059caf3cdff` |
| 终态 | `SUCCEEDED` / `ARTIFACT` / 100% |
| 观测耗时 | 3.0 秒 |
| 编码 / 分辨率 | H.264 / 1080×1920 |
| 帧率 / 帧数 / 时长 | 24fps / 144 帧 / 6.000 秒 |
| 视频 SHA-256 | `79cf26e8b1bae3063418d3d2d86e99fc614eb69ffd1d57014f8b0a68df36dde8` |
| Manifest SHA-256 | `740313b644329d063e22658f7a319deb9ff7e5e9a9ab0be9c75b2630d77781d7` |
| 计划快照 SHA-256 | `448ce75eff639344796b7a3dae6db8e8c04a9d6b7970dc593ca05cb139e95ecc` |
| `preview_kind` | `IMAGE_2D` |
| 边界帧 | 6 张哈希互不相同 |

### 3.2 GLB / Blender 链路

| 指标 | 结果 |
| --- | --- |
| 作业 ID | `afd2e8d0-8785-4d5b-9a97-1ebe7265667f` |
| 终态 | `SUCCEEDED` / `ARTIFACT` / 100% |
| 观测耗时 | 63.2 秒（真实 Blender headless 渲染 144 帧并编码） |
| 编码 / 分辨率 | H.264 / 1080×1920 |
| 帧率 / 帧数 / 时长 | 24fps / 144 帧 / 6.000 秒 |
| 视频 SHA-256 | `6e6f46a241645f81cc00314bad61b986f32ebb10f765ac67193a9a75610cf9d1` |
| Manifest SHA-256 | `66ef9e6887be8ae714a7587cc716da1d810d3f2953b6914b22020905e8487858` |
| 计划快照 SHA-256 | `6f3506bcb1d0c7c6312d4d7e05f4079153d17851ae426a60dc53ce3b321b41e3` |
| `preview_kind` | `BLENDER_3D` |
| 分镜语义 | 85mm 定格 24 帧 / 24mm 侧移 72 帧 / 55mm 环绕 48 帧，均写入 Manifest |
| 边界帧 | 6 张哈希互不相同 |

### 3.3 GPU 采样

渲染期间独立轮询 `nvidia-smi` 共 43 次采样：

| 指标 | 结果 |
| --- | ---: |
| 采样次数 | 43 |
| 峰值 GPU 利用率 | 75% |
| 峰值显存占用 | 1641 MiB |
| 利用率 ≥10% 的采样 | 16 |

该证据只覆盖本次 EEVEE/headless 路径，不代表 Cycles/CUDA/OptiX 或长期稳定性。

## 4. 仍然未完成

1. `var/` 中的旧数据库仍来自旧部署；本轮未做数据迁移演练（新代码只做兼容性 ALTER）。
2. 远程 Worker 任务输入下载、受限成果回收与公网 API 暴露仍未实现；当前只经回环访问。
3. 用户真实产品素材、至少 3 次稳定性与成本对照未执行。
4. A04 的真实“杀进程后重领”仍未在真实长渲染上实测（本轮只验证了正常完成路径）。
5. 前端登录页的完整浏览器视觉/交互验收未做。

## 5. 复跑方式

```bash
sudo -n bash -c 'set -a; . /etc/productdirector/v1.env; set +a; \
  PD_OWNER_TOKEN="$PRODUCTDIRECTOR_OWNER_TOKEN" \
  /home/ubuntu/AI/ProductDirectorAI/.venv/bin/python \
  /home/ubuntu/AI/ProductDirectorAI/scripts/v1_cloud_acceptance.py'
```

脚本输出 JSON 摘要并写入 `var/acceptance/<UTC 时间戳>/acceptance.json`。
