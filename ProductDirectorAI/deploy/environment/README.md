# 环境快照

采集日期 2026-09-13。API Linux 快照来自生产 API venv；ComfyUI 快照来自生产 ComfyUI venv；Windows 快照来自本轮新建环境安装验证。快照用于复核，不能跨平台直接混装。Windows 首次使用 scripts/setup.ps1 和 apps/api/requirements.txt。

ComfyUI 官方上游：https://github.com/Comfy-Org/ComfyUI.git ，提交 7fd919f0caff66a52289ea5b19cb6eaca0da04ef。GPU 环境 torch 2.11.0+cu128 / torchvision 0.26.0+cu128 / torchaudio 2.11.0+cu128 / SageAttention 1.0.6 / Triton 3.6.0。相应 CUDA wheel 需从相容的软件源安装，不能仅凭 freeze 假定普通 PyPI 提供所有构件。

model-manifest.json 为现有模型文件清单及下载来源，不包含模型二进制；源地址使用 main，迁移需另核 SHA256。不要把模型、密钥、数据库或生成视频加入此目录。

../comfyui/custom_nodes 是当前服务器本地定制节点源码；保留原注释。Blender 桥接仅修改了执行文件发现方式（PATH 或 PRODUCTDIRECTOR_BLENDER）。不包含 ComfyUI 上游软件和模型；它们遵循各自来源的许可证。
