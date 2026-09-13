"""Private single-owner deployment authentication; no anonymous fallback."""
from contextvars import ContextVar
import hashlib
import hmac
import os
import secrets
import time

from fastapi import HTTPException, Request

OWNER_TOKEN = os.getenv("PRODUCTDIRECTOR_OWNER_TOKEN", "")
OWNER_ID = "owner-default"
WORKER_TOKEN = os.getenv("PRODUCTDIRECTOR_WORKER_TOKEN", "")
WORKER_ID = os.getenv("PRODUCTDIRECTOR_WORKER_ID", "cloud-worker")
COOKIE = "pd_session"
SESSION_SECONDS = 8 * 60 * 60
current_owner: ContextVar[str | None] = ContextVar("current_owner", default=None)
# V6-16：成员身份（角色权限）；浏览器会话与 Owner 令牌视为 owner 角色
current_member: ContextVar[dict | None] = ContextVar("current_member", default=None)
# V6-07：Automation Key 身份（只放非敏感字段；密钥明文永不进入上下文或日志）
current_automation_key: ContextVar[dict | None] = ContextVar("current_automation_key", default=None)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def matches(value: str, expected: str) -> bool:
    return bool(expected) and hmac.compare_digest(value.encode(), expected.encode())


def owner_configured() -> None:
    if len(OWNER_TOKEN) < 32 or matches(OWNER_TOKEN, WORKER_TOKEN):
        raise HTTPException(503, "服务端需要配置独立的 Owner 访问密钥（至少 32 字符）")


def check_origin(request: Request, allowed_origins: list[str], required: bool = False) -> None:
    origin = request.headers.get("origin")
    if (required and not origin) or (origin and origin not in allowed_origins):
        raise HTTPException(403, "来源地址不在允许列表")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "不允许跨站访问")


def authenticate(request: Request, connect, allowed_origins: list[str]) -> str:
    owner_configured()
    check_origin(request, allowed_origins)
    authorization = request.headers.get("authorization", "")
    if authorization:
        if authorization.startswith(f"Bearer {_MEMBER_PREFIX}_"):
            return authenticate_member(request, authorization[len("Bearer "):], connect)
        if authorization.startswith(f"Bearer pda_"):
            return authenticate_automation(request, authorization[len("Bearer "):], connect)
        if not matches(authorization, f"Bearer {OWNER_TOKEN}"):
            raise HTTPException(401, "未授权")
        request.state.csrf_token = None
        request.state.member = {"role": "owner", "name": "owner", "member_id": "owner"}
        return OWNER_ID
    token = request.cookies.get(COOKIE, "")
    with connect() as db:
        session = db.execute(
            "SELECT * FROM auth_sessions WHERE token_hash = ? AND expires_at > ?",
            (digest(token), time.time()),
        ).fetchone()
    if not session or not matches(session["key_fingerprint"], digest(OWNER_TOKEN)):
        raise HTTPException(401, "请先登录工作台")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        check_origin(request, allowed_origins, required=True)
        if not matches(request.headers.get("x-csrf-token", ""), session["csrf_token"]):
            raise HTTPException(403, "会话校验失败，请重新登录")
    request.state.csrf_token = session["csrf_token"]
    return session["owner_id"]


def create_session(request: Request, response, token: str, connect, allowed_origins: list[str]) -> dict:
    owner_configured()
    check_origin(request, allowed_origins, required=True)
    if not matches(token, OWNER_TOKEN):
        raise HTTPException(401, "访问密钥无效")
    session_token, csrf_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with connect() as db:
        db.execute("DELETE FROM auth_sessions WHERE expires_at <= ? OR token_hash = ?",
                   (time.time(), digest(request.cookies.get(COOKIE, ""))))
        db.execute("INSERT INTO auth_sessions VALUES (?, ?, ?, ?, ?)",
                   (digest(session_token), OWNER_ID, csrf_token, time.time() + SESSION_SECONDS, digest(OWNER_TOKEN)))
    # Plain HTTP is allowed only for a loopback URL reached through SSH forwarding.
    secure = request.url.scheme == "https" or request.url.hostname not in {"localhost", "127.0.0.1", "::1"}
    response.set_cookie(COOKIE, session_token, max_age=SESSION_SECONDS, httponly=True,
                        secure=secure, samesite="strict", path="/")
    return {"owner_id": OWNER_ID, "csrf_token": csrf_token}


_MEMBER_PREFIX = "pdm"


def authenticate_member(request: Request, raw_token: str, connect) -> str:
    """V6-16：成员令牌认证（角色权限）。令牌只存哈希；撤销后立即失效。"""
    from . import roles
    parsed = roles.parse_token(raw_token)
    if parsed is None:
        raise HTTPException(401, "成员令牌格式无效")
    member_id, secret = parsed
    with connect() as db:
        row = db.execute("SELECT * FROM members WHERE member_id = ? AND revoked_at IS NULL", (member_id,)).fetchone()
    if not row or not roles.token_matches(secret, row["token_hash"]):
        raise HTTPException(401, "成员令牌无效")
    identity = {"member_id": member_id, "name": row["name"], "role": row["role"], "row_id": row["id"]}
    request.state.member = identity
    request.state.csrf_token = None
    with connect() as db:
        db.execute("UPDATE members SET last_used_at = ?, call_count = call_count + 1 WHERE id = ?",
                   (row["last_used_at"] and row["last_used_at"] or _iso_now(), row["id"]))
    return row["owner_id"]


def _iso_now() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def authenticate_automation(request: Request, raw_key: str, connect) -> str:
    """V6-07：Automation Key 认证（无 Cookie/CSRF；IP 允许列表与本 Key 绑定）。"""
    from . import automation
    parsed = automation.parse_key(raw_key)
    if parsed is None:
        raise HTTPException(401, "Automation Key 格式无效")
    key_id, secret = parsed
    with connect() as db:
        row = db.execute("SELECT * FROM automation_keys WHERE key_id = ?", (key_id,)).fetchone()
    if not row or not automation.secret_matches(secret, row["secret_hash"]):
        raise HTTPException(401, "Automation Key 无效")
    if row["revoked_at"]:
        raise HTTPException(401, {"code": "key_revoked", "detail": f"Key 已于 {row['revoked_at']} 撤销"})
    if row["expires_at"] is not None and float(row["expires_at"]) <= time.time():
        raise HTTPException(401, {"code": "key_expired", "detail": "Key 已过期"})
    client_ip = request.client.host if request.client else None
    if not automation.ip_allowed(client_ip, row["ip_allowlist"] or ""):
        raise HTTPException(403, {"code": "ip_not_allowed", "detail": "调用来源 IP 不在该 Key 的允许列表"})
    scopes = [item for item in (row["scopes"] or "").split(",") if item]
    identity = {
        "key_id": key_id,
        "name": row["name"],
        "scopes": scopes,
        "workspace_id": row["workspace_id"],
        "project_id": row["project_id"],
        "rate_limit_per_minute": int(row["rate_limit_per_minute"]),
        "budget_limit_amount": row["budget_limit_amount"],
        "budget_period": row["budget_period"],
        "prefix": row["prefix"],
        "client_ip": client_ip,
    }
    request.state.automation_key = identity
    request.state.csrf_token = None
    return row["owner_id"]


def ensure_worker(request: Request) -> None:
    if len(WORKER_TOKEN) < 32 or matches(WORKER_TOKEN, OWNER_TOKEN):
        raise HTTPException(503, "服务端未配置独立的 Worker 访问密钥")
    if request.headers.get("origin") or request.headers.get("sec-fetch-site"):
        raise HTTPException(403, "Worker 接口不接受浏览器访问")
    if not matches(request.headers.get("authorization", ""), f"Bearer {WORKER_TOKEN}"):
        raise HTTPException(401, "Worker 未授权")


def check_worker_id(worker_id: str) -> None:
    if worker_id != WORKER_ID:
        raise HTTPException(403, "Worker 身份不匹配")
