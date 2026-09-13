"""V6-07 Automation API 基础件：Key 解析/校验、scope 判定、幂等指纹、限流窗口。

诚实边界（对应主规划 12.9）：
- Key 只在创建时显示一次；服务端只保存 `secret` 的哈希，日志只记录 Key ID，不打印密钥。
- scope 不足一律 403（`insufficient_scope`），不做“默默放行”；Key 管理接口只允许 Owner 会话。
- 限流数值是本产品初始设置**不是平台事实**（默认 60 请求/分钟，按 Key 与工作区双层），
  实际限制随错误与文档一起返回，可随压测调整。
- 幂等按 (owner, 调用方, 接口, 键) 隔离并绑定请求规范化哈希：同键不同体 → 409；
  付费/发布类 POST 缺少 `Idempotency-Key` → 428（不静默执行）。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time

KEY_PREFIX = "pda"
KEY_ID_BYTES = 8
SECRET_BYTES = 32
DEFAULT_RATE_LIMIT_PER_MINUTE = 60
WORKSPACE_RATE_LIMIT_PER_MINUTE = 60
MAX_RATE_LIMIT_PER_MINUTE = 6000
KEY_TTL_MAX_DAYS = 3650

SCOPES = (
    "projects:read",
    "assets:write",
    "generate:write",
    "jobs:read",
    "packages:read",
    "publish:write",
    "webhooks:manage",
)

# 路由 → 必需 scope（按 (方法, 路径正则) 顺序匹配；未列出的自动化路径默认拒绝）
ROUTE_SCOPES: tuple[tuple[str, str, str], ...] = (
    ("GET", r"^/api/v1/health$", "projects:read"),
    ("GET", r"^/api/v1/platform-profiles", "projects:read"),
    ("GET", r"^/api/v1/postproduction-presets", "projects:read"),
    ("GET", r"^/api/v1/projects", "projects:read"),
    ("GET", r"^/api/v1/assets", "projects:read"),
    ("GET", r"^/api/v1/plans", "projects:read"),
    ("GET", r"^/api/v1/runs", "jobs:read"),
    ("GET", r"^/api/v1/jobs", "jobs:read"),
    ("GET", r"^/api/v1/batches", "jobs:read"),
    ("GET", r"^/api/v1/automation/batches/", "jobs:read"),
    ("GET", r"^/api/v1/automation/jobs/", "jobs:read"),
    ("GET", r"^/api/v1/automation/openapi$", "projects:read"),
    ("GET", r"^/api/v1/packages", "packages:read"),
    ("GET", r"^/api/v1/usage", "projects:read"),
    ("GET", r"^/api/v1/costs", "projects:read"),
    ("GET", r"^/api/v1/budgets", "projects:read"),
    ("POST", r"^/api/v1/assets", "assets:write"),
    ("POST", r"^/api/v1/audio/previews$", "generate:write"),
    ("POST", r"^/api/v1/batches/preview$", "generate:write"),
    ("POST", r"^/api/v1/batches$", "generate:write"),
    ("POST", r"^/api/v1/batches/[^/]+/(pause|resume|cancel|retry-failed)$", "generate:write"),
    ("POST", r"^/api/v1/automation/generate$", "generate:write"),
    ("POST", r"^/api/v1/usage-events$", "generate:write"),
    ("POST", r"^/api/v1/budgets$", "generate:write"),
    ("PATCH", r"^/api/v1/budgets/[^/]+$", "generate:write"),
    ("POST", r"^/api/v1/budgets/[^/]+/(reserve|settle)$", "generate:write"),
    ("POST", r"^/api/v1/runs/[^/]+/packages$", "publish:write"),
    ("POST", r"^/api/v1/packages/[^/]+/(verify|approve)$", "publish:write"),
    ("POST", r"^/api/v1/batches/[^/]+/packages/archive$", "publish:write"),
    ("POST", r"^/api/v1/webhooks", "webhooks:manage"),
    ("GET", r"^/api/v1/webhooks", "webhooks:manage"),
)

# 必须带 Idempotency-Key 的自动化写入路径（创建付费任务/批次/发布）
REQUIRED_IDEMPOTENCY_ROUTES: tuple[tuple[str, str], ...] = (
    ("POST", r"^/api/v1/batches$"),
    ("POST", r"^/api/v1/automation/generate$"),
    ("POST", r"^/api/v1/audio/previews$"),
    ("POST", r"^/api/v1/runs/[^/]+/packages$"),
    ("POST", r"^/api/v1/batches/[^/]+/packages/archive$"),
)

OWNER_ONLY_PATHS = (r"^/api/v1/automation-keys",)


class AutomationError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 403, detail: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.detail = detail or {}

    def as_detail(self) -> dict:
        return {"code": self.code, "detail": self.message, **self.detail}


def required_scope(method: str, path: str) -> str | None:
    """返回该路由所需 scope；未登记的路由返回 None（自动化调用一律拒绝）。"""
    for route_method, pattern, scope in ROUTE_SCOPES:
        if route_method == method and re.match(pattern, path):
            return scope
    return None


def owner_only(path: str) -> bool:
    return any(re.match(pattern, path) for pattern in OWNER_ONLY_PATHS)


def idempotency_required(method: str, path: str) -> bool:
    return any(route_method == method and re.match(pattern, path) for route_method, pattern in REQUIRED_IDEMPOTENCY_ROUTES)


def missing_scopes(granted: list[str] | tuple[str, ...], required: str | None) -> list[str]:
    if required is None:
        return list(SCOPES)
    return [] if required in set(granted or ()) else [required]


def generate_key() -> dict:
    """生成 Key 明文与存储信息：`pda_<key_id>_<secret>`；只有哈希入库。"""
    key_id = secrets.token_hex(KEY_ID_BYTES)
    secret = secrets.token_urlsafe(SECRET_BYTES)
    return {
        "key_id": key_id,
        "secret": secret,
        "plaintext": f"{KEY_PREFIX}_{key_id}_{secret}",
        "secret_hash": hash_secret(secret),
        "fingerprint": fingerprint(secret),
        "prefix": f"{KEY_PREFIX}_{key_id}",
    }


def hash_secret(secret: str) -> str:
    return hashlib.sha256(f"{KEY_PREFIX}:{secret}".encode("utf-8")).hexdigest()


def fingerprint(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:12]


def parse_key(raw: str) -> tuple[str, str] | None:
    """解析 `pda_<key_id>_<secret>`；格式非法返回 None（不打印原值）。"""
    if not raw or not raw.startswith(f"{KEY_PREFIX}_"):
        return None
    parts = raw.split("_", 2)
    if len(parts) != 3 or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]


def secret_matches(secret: str, stored_hash: str) -> bool:
    return bool(stored_hash) and hmac.compare_digest(hash_secret(secret), stored_hash)


def ip_allowed(client_ip: str | None, allowlist: str) -> bool:
    """IP 允许列表（逗号分隔，支持前缀匹配与 `*` 通配）；空列表表示不限制。"""
    entries = [item.strip() for item in (allowlist or "").split(",") if item.strip()]
    if not entries:
        return True
    if not client_ip:
        return False
    for entry in entries:
        if entry == "*":
            return True
        if entry.endswith("*") and client_ip.startswith(entry[:-1]):
            return True
        if entry == client_ip:
            return True
    return False


def window_start(epoch_seconds: float, window_seconds: int = 60) -> int:
    return int(epoch_seconds // window_seconds) * window_seconds


def rate_limit_verdict(count: int, limit: int, window_started: int, now: float,
                       window_seconds: int = 60) -> dict:
    """限流判定：超限返回 retry_after 秒（到窗口结束），不静默丢弃计数。"""
    remaining = max(0, limit - count)
    return {
        "limit": limit,
        "count": count,
        "remaining": remaining,
        "window_seconds": window_seconds,
        "window_start": window_started,
        "reset_in": max(0, int(window_started + window_seconds - now) + 1),
        "allowed": count <= limit,
    }


def canonical_body(body) -> str:
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def request_fingerprint(body) -> str:
    return hashlib.sha256(canonical_body(body).encode("utf-8")).hexdigest()


def limits_document(*, key_limit: int | None = None, workspace_limit: int = WORKSPACE_RATE_LIMIT_PER_MINUTE) -> dict:
    """对外返回的限额说明：明确标注为本产品设置，不是平台事实。"""
    return {
        "requests_per_minute": {
            "per_key": key_limit if key_limit is not None else DEFAULT_RATE_LIMIT_PER_MINUTE,
            "per_workspace": workspace_limit,
        },
        "max_expanded_items_per_batch": 100,
        "default_concurrency": {"gpu_jobs": 2, "remote_generation_operations": 4},
        "note": "以上为本产品初始设置，不是平台或 Provider 的官方限制；可在部署压测后调整",
    }


def scopes_document() -> dict:
    return {
        "scopes": list(SCOPES),
        "route_scopes": [
            {"method": method, "path_regex": pattern, "scope": scope}
            for method, pattern, scope in ROUTE_SCOPES
        ],
        "requires_idempotency_key": [
            {"method": method, "path_regex": pattern} for method, pattern in REQUIRED_IDEMPOTENCY_ROUTES
        ],
        "key_management": "只有 Owner 会话可以创建/撤销 Key；Automation Key 无权管理 Key",
        "note": "未登记的路由不会对 Automation Key 放行（返回 403 insufficient_scope）",
    }


def ttl_seconds(days: int | None) -> int | None:
    if days is None:
        return None
    if days <= 0 or days > KEY_TTL_MAX_DAYS:
        raise AutomationError("invalid_ttl", f"有效期需在 1–{KEY_TTL_MAX_DAYS} 天之间", status_code=422)
    return int(days) * 86400


def now_epoch() -> float:
    return time.time()
