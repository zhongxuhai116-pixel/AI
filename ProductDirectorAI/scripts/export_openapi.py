"""V6-16 OpenAPI 导出：把运行时 OpenAPI 文档与角色/scope/限流说明一起落盘。

用法（云端）：
    cd apps/api && ../../.venv/bin/python ../../scripts/export_openapi.py --out ../../docs/openapi/productdirector-v6.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导出 ProductDirectorAI OpenAPI 文档")
    parser.add_argument("--out", default="docs/openapi/productdirector-v6.json")
    parser.add_argument("--api-root", default="apps/api")
    args = parser.parse_args(argv)

    api_root = Path(args.api_root).resolve()
    sys.path.insert(0, str(api_root))
    from productdirector_api import main as api  # noqa: PLC0415
    from productdirector_api import automation, publishing, roles, webhooks  # noqa: PLC0415

    document = api.app.openapi()
    document["info"] = {
        "title": "ProductDirectorAI V6 API",
        "version": "6.0.0",
        "description": (
            "V1…V6 全部接口。认证：浏览器会话（Cookie + CSRF）或 Automation Key（pdm_/pda_ Bearer）。"
            "本文件由脚本从运行时生成，不手写。"
        ),
    }
    document["x-productdirector"] = {
        "roles": roles.matrix(),
        "automation_scopes": automation.scopes_document(),
        "automation_limits": automation.limits_document(),
        "webhook_events": list(webhooks.EVENT_TYPES),
        "webhook_delivery": webhooks.catalog(),
        "publishing_states": list(publishing.PUBLISH_STATES),
        "publishing_connectors": "见 GET /api/v1/publishing/connectors（连接器配置状态与未确认项在运行时返回）",
        "honest_boundaries": [
            "平台连接器未配置授权时一律 BLOCKED/NOT_CONFIGURED；本产品不伪造发布成功",
            "Webhook 交付为至少一次，接收方按 event_id 去重",
            "成本账本中自管资源为内部估算，未定价用量不按 0 计算",
        ],
    }
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths = len(document.get("paths", {}))
    operations = sum(len(item) for item in document.get("paths", {}).values())
    print(f"已导出 {output}：{paths} 条路径 / {operations} 个操作 / "
          f"{len(document['components']['schemas'])} 个 schema")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
