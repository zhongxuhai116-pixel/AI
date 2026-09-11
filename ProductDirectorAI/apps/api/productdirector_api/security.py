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
        if not matches(authorization, f"Bearer {OWNER_TOKEN}"):
            raise HTTPException(401, "未授权")
        request.state.csrf_token = None
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
