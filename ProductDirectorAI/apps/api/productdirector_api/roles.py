"""V6-16 角色与权限：Owner / Editor / Reviewer / Publisher 的显式权限矩阵。

诚实边界：
- 权限是**服务端强制**的（路由 → 权限表 + 中间件），不是前端隐藏按钮。
- 单机私有部署的 Owner 令牌仍是超级身份；成员用个人令牌（`pdm_…`）登录，令牌只存哈希、只显示一次。
- 权限不足一律 403 `permission_denied`，不做"静默降级"或"只读回退"。
- 审批与发布分离：Reviewer 能审批包但不能发布；Publisher 能发布但不能改计划或审批；Owner 全权。
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets

ROLES = ("owner", "editor", "reviewer", "publisher")
MEMBER_PREFIX = "pdm"

# 角色 → 权限集合
PERMISSIONS: dict[str, tuple[str, ...]] = {
    "owner": (
        "project:read", "project:write", "plan:write", "run:create", "asset:write", "batch:write",
        "localization:write", "audio:write", "package:build", "package:approve", "package:read",
        "qa:review", "publish:write", "cost:read", "cost:write", "automation:manage", "webhook:manage",
        "member:manage",
    ),
    "editor": (
        "project:read", "project:write", "plan:write", "run:create", "asset:write", "batch:write",
        "localization:write", "audio:write", "package:build", "package:read", "cost:read",
    ),
    "reviewer": (
        "project:read", "package:read", "package:approve", "qa:review", "cost:read",
    ),
    "publisher": (
        "project:read", "package:read", "publish:write", "cost:read",
    ),
}

# 路由 → 必需权限（按顺序匹配；未登记的路由对成员默认按"只读需 project:read"处理，写操作一律拒绝）
ROUTE_PERMISSIONS: tuple[tuple[str, str, str], ...] = (
    ("GET", r"^/api/v1/health$", "project:read"),
    ("GET", r"^/api/v1/(assets|plans|runs|jobs|projects|product-versions)", "project:read"),
    ("GET", r"^/api/v1/(platform-profiles|postproduction-presets|music-assets|packages|usage|costs|budgets)", "project:read"),
    ("GET", r"^/api/v1/console/overview$", "project:read"),
    ("POST", r"^/api/v1/(assets|plans|projects)", "plan:write"),
    ("PATCH", r"^/api/v1/plans/[^/]+$", "plan:write"),
    ("POST", r"^/api/v1/runs$", "run:create"),
    ("POST", r"^/api/v1/runs/[^/]+/(localizations|voiceovers|audio/mix|outputs)$", "localization:write"),
    ("POST", r"^/api/v1/audio/previews$", "localization:write"),
    ("POST", r"^/api/v1/(music-assets|video-sources)$", "audio:write"),
    ("POST", r"^/api/v1/batches", "batch:write"),
    ("POST", r"^/api/v1/batches/[^/]+/(pause|resume|cancel|retry-failed)$", "batch:write"),
    ("POST", r"^/api/v1/automation/generate$", "batch:write"),
    ("POST", r"^/api/v1/runs/[^/]+/packages$", "package:build"),
    ("POST", r"^/api/v1/packages/[^/]+/verify$", "package:approve"),
    ("POST", r"^/api/v1/packages/[^/]+/approve$", "package:approve"),
    ("POST", r"^/api/v1/batches/[^/]+/packages/archive$", "package:approve"),
    ("POST", r"^/api/v1/publishing/jobs$", "publish:write"),
    ("POST", r"^/api/v1/publishing/(accounts/connect|preflight|approvals)$", "publish:write"),
    ("DELETE", r"^/api/v1/publishing/accounts/[^/]+$", "publish:write"),
    ("POST", r"^/api/v1/publishing/oauth/[^/]+/callback$", "publish:write"),
    ("POST", r"^/api/v1/publishing/jobs/[^/]+/(reconcile|retry|cancel)$", "publish:write"),
    ("GET", r"^/api/v1/publishing", "project:read"),
    ("POST", r"^/api/v1/usage-events$", "cost:write"),
    ("POST", r"^/api/v1/budgets$", "cost:write"),
    ("PATCH", r"^/api/v1/budgets/[^/]+$", "cost:write"),
    ("POST", r"^/api/v1/budgets/[^/]+/(reserve|settle)$", "cost:write"),
    ("GET", r"^/api/v1/(automation-keys|webhooks)", "automation:manage"),
    ("POST", r"^/api/v1/automation-keys$", "automation:manage"),
    ("DELETE", r"^/api/v1/automation-keys/[^/]+$", "automation:manage"),
    ("GET", r"^/api/v1/automation/openapi$", "project:read"),
    ("POST", r"^/api/v1/webhooks", "webhook:manage"),
    ("PATCH", r"^/api/v1/webhooks/[^/]+$", "webhook:manage"),
    ("POST", r"^/api/v1/webhooks/[^/]+/(test|pause|resume|rotate-secret)$", "webhook:manage"),
    ("POST", r"^/api/v1/webhooks/[^/]+/dead-letters/[^/]+/replay$", "webhook:manage"),
    ("GET", r"^/api/v1/webhooks/catalog$", "webhook:manage"),
    ("GET", r"^/api/v1/members", "member:manage"),
    ("POST", r"^/api/v1/members", "member:manage"),
)

OWNER_ONLY_PATH = re.compile(r"^/api/v1/members")


class RoleError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 403, detail: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.detail = detail or {}

    def as_detail(self) -> dict:
        return {"code": self.code, "detail": self.message, **self.detail}


def permissions_for(role: str) -> tuple[str, ...]:
    if role not in PERMISSIONS:
        raise RoleError("unknown_role", f"未知角色 {role}", status_code=422,
                        detail={"roles": list(ROLES)})
    return PERMISSIONS[role]


def required_permission(method: str, path: str) -> tuple[str | None, bool]:
    """返回 (必需权限, 是否已登记)。未登记路径的写操作必须拒绝（不默认放行）。"""
    normalized = path.rstrip("/") or path
    for route_method, pattern, permission in ROUTE_PERMISSIONS:
        if route_method == method and re.match(pattern, normalized):
            return permission, True
    return None, False


def check_permission(role: str, method: str, path: str) -> None:
    permission, registered = required_permission(method, path)
    if not registered:
        if method in ("GET", "HEAD", "OPTIONS"):
            permission = "project:read"
        else:
            raise RoleError("permission_denied", f"该写操作未登记权限：{method} {path}（默认拒绝）",
                            detail={"role": role, "path": path, "method": method})
    if permission not in permissions_for(role):
        raise RoleError(
            "permission_denied",
            f"角色 {role} 没有权限 {permission}（{method} {path}）",
            detail={"role": role, "required_permission": permission, "path": path, "method": method},
        )


def generate_member_token() -> dict:
    member_id = secrets.token_hex(6)
    secret = secrets.token_urlsafe(32)
    return {
        "member_id": member_id,
        "plaintext": f"{MEMBER_PREFIX}_{member_id}_{secret}",
        "token_hash": hash_token(secret),
        "hint": f"…{secret[-4:]}",
    }


def hash_token(secret: str) -> str:
    return hashlib.sha256(f"{MEMBER_PREFIX}:{secret}".encode("utf-8")).hexdigest()


def parse_token(raw: str) -> tuple[str, str] | None:
    if not raw or not raw.startswith(f"{MEMBER_PREFIX}_"):
        return None
    parts = raw.split("_", 2)
    if len(parts) != 3 or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]


def token_matches(secret: str, stored_hash: str) -> bool:
    return bool(stored_hash) and hmac.compare_digest(hash_token(secret), stored_hash)


def member_public(row, *, reveal: str | None = None) -> dict:
    return {
        "id": row["id"],
        "member_id": row["member_id"],
        "name": row["name"],
        "role": row["role"],
        "permissions": list(permissions_for(row["role"])),
        "token_hint": row["token_hint"],
        "revoked_at": row["revoked_at"],
        "last_used_at": row["last_used_at"],
        "call_count": row["call_count"],
        "created_at": row["created_at"],
        "token": reveal,
        "note": ("令牌只在创建时显示一次；服务端只保存哈希" if reveal else "令牌不可回读；遗失请撤销后重建"),
    }


def matrix() -> dict:
    return {
        "roles": list(ROLES),
        "permissions": {role: list(permissions_for(role)) for role in ROLES},
        "separation_of_duties": {
            "reviewer": "可审批发布包，但不能发布（避免同一人既审核又发布）",
            "publisher": "可发布已审批的包，但不能改计划/文案或审批",
            "editor": "可创建计划/批次/后期，但不能审批发布包、不能发布",
            "owner": "全权，含成员与自动化/Webhook 管理",
        },
        "enforcement": "服务端路由 → 权限表 + 中间件强制；未登记的写操作默认拒绝",
    }
