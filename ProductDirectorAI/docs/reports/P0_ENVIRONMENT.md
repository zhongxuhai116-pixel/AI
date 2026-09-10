# P0 本机环境验证

验证日期：2026-09-10

> 历史证据范围：以下结果仅对应旧 Windows 电脑、当时安装版本和通用测试夹具；不代表新电脑、云端节点或用户真实素材已经复验。旧电脑软件版本是快照，不是新电脑必须复制的版本承诺。

| 项目 | 结果 | 证据 |
|---|---|---|
| Blender Headless | PASS | Blender 5.2.1 LTS；成功导入可再生成的通用 GLB 并输出帧序列 |
| FFmpeg | PASS | FFmpeg 9.0.1；图片与 GLB 帧序列均编码 H.264 MP4 |
| Python API | PASS | FastAPI 0.116.1；健康、素材、计划、任务、产物接口实测 |
| 数据库 | PASS | SQLite 自动建表并跨请求保存素材、计划、任务 |
| 前端 | PASS | React 19 + Vite 6 生产构建成功 |
| 本机预览 | PASS | `http://127.0.0.1:4173/` 浏览器渲染成功 |

## 可执行文件

- Blender：`C:\Program Files\Blender Foundation\Blender 5.2\blender.exe`
- FFmpeg：由后端在 WinGet 安装目录自动发现
- Python：项目 `.venv\Scripts\python.exe`

## 实测产物

- 图片输入：540×960、24 fps、6 秒，203,699 bytes。
- GLB 输入：使用 `tests/fixtures/generic-product.glb`，540×960、24 fps、6 秒，133,554 bytes。
- 两条任务均到达 `SUCCEEDED / ARTIFACT / 100%`，并生成 `metadata.json`。

## 历史结论

当时 P0 技术范围通过。当前总状态仍为 PARTIAL；测试 GLB 是与业务品类无关的三块体商品夹具，仅用于技术验证，不代表用户产品。
