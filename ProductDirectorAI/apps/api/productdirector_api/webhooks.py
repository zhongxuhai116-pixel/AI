"""V6-08 事件与 Webhook 规则层：事件目录、签名、重试退避、脱敏与死信判定。

诚实边界（对应主规划 12.10）：
- 交付语义是**至少一次**：接收方必须按 `event_id` 去重；不承诺严格按序，payload 带
  `aggregate_revision` 让接收方丢弃旧状态。
- 签名 `HMAC-SHA256(secret, timestamp + "." + raw_body)`，时间戳与签名分两个头；
  重投使用新时间戳、**event_id 不变**；容差默认 5 分钟；支持短期双密钥轮换。
- 事件不内嵌视频文件或凭证；投递记录只保留脱敏片段。
- 重试 1m/5m/15m/1h/6h/24h，超过次数进死信；4xx（非 408/429）通常是配置/权限错误，
  暂停该目标而不是无脑重试。这些数值是本产品设置，不是平台事实。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import time

SCHEMA_VERSION = "1.0"
SIGNATURE_HEADER = "X-PDA-Signature"
TIMESTAMP_HEADER = "X-PDA-Timestamp"
EVENT_ID_HEADER = "X-PDA-Event-Id"
DELIVERY_HEADER = "X-PDA-Delivery-Attempt"
DEFAULT_TOLERANCE_SECONDS = 300
REQUEST_TIMEOUT_SECONDS = 10

EVENT_TYPES = (
    "batch.started",
    "batch.completed",
    "item.completed",
    "item.failed",
    "review.required",
    "package.ready",
    "publish.succeeded",
    "publish.failed",
    "budget.blocked",
    "webhook.test",
)

# 重试计划（秒）：第 1 次失败后等 1 分钟……第 6 次失败后进死信
RETRY_SCHEDULE_SECONDS = (60, 300, 900, 3600, 21600, 86400)
MAX_ATTEMPTS = len(RETRY_SCHEDULE_SECONDS) + 1  # 首次投递 + 6 次重试，之后进死信
SECRET_HINT_LENGTH = 4
RESPONSE_EXCERPT_LIMIT = 300

SECRET_PATTERNS = (
    re.compile(r"(?i)bearer\s+[a-z0-9._\-]{12,}"),
    re.compile(r"(?i)(api[_-]?key|secret|token|password)\"?\s*[:=]\s*\"?[a-z0-9._\-]{8,}"),
)
# 键名本身就表示凭证的字段（结构性检查，避免把正常的哈希/缓存键误判为凭证）
SECRET_KEY_PATTERN = re.compile(
    r"(?i)^(secret|secret_?\w*|token|access_token|refresh_token|api_?key|password|credential|cookie|authorization)$"
)
BANNED_CONTENT = ("data:video", "data:audio", "base64,", "access_token", "refresh_token", "cookie")


class WebhookError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 422, detail: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.detail = detail or {}

    def as_detail(self) -> dict:
        return {"code": self.code, "detail": self.message, **self.detail}


def canonical(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def normalize_event_types(event_types) -> list[str]:
    requested = [str(item).strip() for item in (event_types or []) if str(item).strip()]
    if not requested:
        raise WebhookError("event_types_required", f"至少订阅一个事件类型（可用：{list(EVENT_TYPES)}）")
    unknown = [item for item in requested if item not in EVENT_TYPES]
    if unknown:
        raise WebhookError("unknown_event_type", f"未知事件类型：{unknown}", detail={"event_types": list(EVENT_TYPES)})
    return sorted(set(requested))


def validate_url(url: str, *, allow_loopback_http: bool = True) -> str:
    """只接受 HTTPS；本机回环允许 http（用于本机接收端联调），其余 http 一律拒绝。"""
    candidate = (url or "").strip()
    if not candidate:
        raise WebhookError("url_required", "必须提供接收地址")
    if candidate.startswith("https://"):
        return candidate
    if allow_loopback_http and re.match(r"^http://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?(/|$)", candidate):
        return candidate
    raise WebhookError("insecure_url", "只接受 HTTPS 接收地址（本机回环联调可用 http://127.0.0.1）")


def check_payload_secrets(payload) -> list[str]:
    """事件负载不得内嵌凭证或文件内容（键名 + 文本双重检查，不误伤哈希/缓存键）。"""
    problems: list[str] = []
    text = payload if isinstance(payload, str) else canonical(payload)
    for pattern in SECRET_PATTERNS:
        match = pattern.search(text)
        if match:
            problems.append(f"疑似凭证/密钥：{match.group(0)[:24]}")
    problems.extend(_secret_key_problems(payload))
    lowered = text.lower()
    for banned in BANNED_CONTENT:
        if banned in lowered:
            problems.append(f"负载包含禁止内容：{banned}")
    return problems


def _secret_key_problems(payload, prefix: str = "") -> list[str]:
    """结构检查：只有键名明确表示凭证时才算问题。"""
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if SECRET_KEY_PATTERN.match(str(key)):
                found.append(f"禁止的凭证字段：{path}")
                continue
            found.extend(_secret_key_problems(value, path))
    elif isinstance(payload, list):
        for index, item in enumerate(payload[:50]):
            found.extend(_secret_key_problems(item, f"{prefix}[{index}]"))
    return found


def event_envelope(*, event_id: str, event_type: str, aggregate_type: str, aggregate_id: str,
                   aggregate_revision: int, payload: dict, occurred_at: str) -> dict:
    if event_type not in EVENT_TYPES:
        raise WebhookError("unknown_event_type", f"未知事件类型 {event_type}")
    envelope = {
        "schema_version": SCHEMA_VERSION,
        "event_id": event_id,
        "event_type": event_type,
        "aggregate": {"type": aggregate_type, "id": aggregate_id, "revision": int(aggregate_revision)},
        "occurred_at": occurred_at,
        "payload": payload,
        "delivery": {
            "semantics": "at_least_once",
            "dedupe_by": "event_id",
            "ordered": False,
            "note": "接收方按 event_id 去重；payload.aggregate.revision 用于丢弃旧状态",
        },
    }
    problems = check_payload_secrets(envelope)
    if problems:
        raise WebhookError("payload_contains_secrets", "事件负载包含凭证或文件内容，已拒绝写入 Outbox",
                           detail={"problems": problems})
    return envelope


def signing_key(secret: str, timestamp: str, raw_body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"),
                                timestamp.encode("utf-8") + b"." + raw_body,
                                hashlib.sha256).hexdigest()


def verify_signature(*, secret: str, timestamp: str, raw_body: bytes, signature: str) -> bool:
    expected = signing_key(secret, timestamp, raw_body)
    return hmac.compare_digest(expected, (signature or "").strip())


def timestamp_fresh(timestamp: str, *, now: float | None = None,
                    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS) -> bool:
    try:
        value = float(timestamp)
    except (TypeError, ValueError):
        return False
    current = time.time() if now is None else now
    return abs(current - value) <= tolerance_seconds


def verify_delivery(*, secrets: list[str], timestamp: str, raw_body: bytes, signature: str,
                    now: float | None = None,
                    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS) -> dict:
    """完整验签：时间戳容差 + 任一有效密钥（支持轮换期双密钥）。"""
    if not timestamp_fresh(timestamp, now=now, tolerance_seconds=tolerance_seconds):
        return {"valid": False, "reason": "timestamp_out_of_tolerance",
                "tolerance_seconds": tolerance_seconds}
    matched = None
    for index, secret in enumerate(secrets or []):
        if secret and verify_signature(secret=secret, timestamp=timestamp, raw_body=raw_body, signature=signature):
            matched = index
            break
    if matched is None:
        return {"valid": False, "reason": "signature_mismatch"}
    return {"valid": True, "key_index": matched,
            "note": "key_index=1 表示用轮换期内的上一个密钥通过验签" if matched else "使用当前密钥验签"}


def retry_delay_seconds(attempt: int) -> int | None:
    """第 attempt 次投递失败后的等待秒数；已无后续排期返回 None（进死信）。"""
    if attempt <= 0:
        raise WebhookError("invalid_attempt", "attempt 必须从 1 开始")
    if attempt > len(RETRY_SCHEDULE_SECONDS):
        return None
    return RETRY_SCHEDULE_SECONDS[attempt - 1]


def classify_response(status_code: int | None, error: str | None = None) -> dict:
    """投递结果分类：成功 / 可重试 / 不可重试（暂停目标）。"""
    if error and status_code is None:
        return {"outcome": "retry", "reason": f"网络错误：{error}", "retryable": True}
    if status_code is None:
        return {"outcome": "retry", "reason": "没有响应", "retryable": True}
    if 200 <= status_code < 300:
        return {"outcome": "delivered", "reason": "2xx", "retryable": False}
    if status_code in (408, 429) or status_code >= 500:
        return {"outcome": "retry", "reason": f"HTTP {status_code}", "retryable": True}
    return {"outcome": "dead", "reason": f"HTTP {status_code}：通常是配置或权限错误，已暂停该目标",
            "retryable": False}


def next_attempt_plan(attempt: int, classification: dict) -> dict:
    """给出该次投递后的动作：完成 / 计划重试 / 进死信。"""
    if classification["outcome"] == "delivered":
        return {"action": "delivered", "next_retry_in": None, "dead_letter": False}
    if classification["outcome"] == "dead":
        return {"action": "dead_letter", "next_retry_in": None, "dead_letter": True,
                "reason": classification["reason"]}
    delay = retry_delay_seconds(attempt)
    if delay is None:
        return {"action": "dead_letter", "next_retry_in": None, "dead_letter": True,
                "reason": f"超过最大投递次数 {MAX_ATTEMPTS}"}
    return {"action": "retry", "next_retry_in": delay, "dead_letter": False}


def mask_secret(secret: str) -> str:
    if not secret:
        return ""
    return f"…{secret[-SECRET_HINT_LENGTH:]}" if len(secret) > SECRET_HINT_LENGTH else "…"


def redact_response(text: str | None, limit: int = RESPONSE_EXCERPT_LIMIT) -> str:
    """投递记录只保留脱敏片段：截断 + 抹掉疑似凭证。"""
    snippet = (text or "")[:limit]
    for pattern in SECRET_PATTERNS:
        snippet = pattern.sub("[redacted]", snippet)
    return snippet


def delivery_public(attempt_row, *, include_signature: bool = True) -> dict:
    public = {
        "id": attempt_row["id"],
        "endpoint_id": attempt_row["endpoint_id"],
        "event_id": attempt_row["event_id"],
        "event_type": attempt_row["event_type"] if "event_type" in attempt_row.keys() else None,
        "attempt": attempt_row["attempt"],
        "status": attempt_row["status"],
        "response_status": attempt_row["response_status"],
        "response_excerpt": redact_response(attempt_row["response_excerpt"]),
        "error": attempt_row["error"],
        "next_retry_at": attempt_row["next_retry_at"],
        "created_at": attempt_row["created_at"],
    }
    if include_signature:
        public["signature"] = attempt_row["signature"]
    return public


def endpoint_public(row, *, reveal_secret: str | None = None) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "url": row["url"],
        "event_types": [item for item in (row["event_types"] or "").split(",") if item],
        "enabled": bool(row["enabled"]),
        "paused_at": row["paused_at"],
        "paused_reason": row["paused_reason"],
        "secret_current_hint": mask_secret(row["secret_current"]),
        "secret_previous_hint": mask_secret(row["secret_previous"]),
        "secret_previous_expires_at": row["secret_previous_expires_at"],
        "revision": row["revision"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "secret": reveal_secret,
        "note": ("密钥只在创建/轮换时显示一次；投递记录与列表都不返回密钥"
                 if reveal_secret else "密钥不可回读；可在轮换时获取新密钥"),
        "delivery_policy": {
            "timeout_seconds": REQUEST_TIMEOUT_SECONDS,
            "max_attempts": MAX_ATTEMPTS,
            "retry_schedule_seconds": list(RETRY_SCHEDULE_SECONDS),
            "signature": f"HMAC-SHA256(secret, {TIMESTAMP_HEADER} + '.' + raw_body)",
            "tolerance_seconds": DEFAULT_TOLERANCE_SECONDS,
            "semantics": "at_least_once",
            "note": "这些是本产品设置，不是平台事实",
        },
    }


def catalog() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "event_types": list(EVENT_TYPES),
        "headers": {
            "signature": SIGNATURE_HEADER,
            "timestamp": TIMESTAMP_HEADER,
            "event_id": EVENT_ID_HEADER,
            "attempt": DELIVERY_HEADER,
        },
        "signature_scheme": f"HMAC-SHA256(secret, {TIMESTAMP_HEADER} + '.' + raw_body)",
        "tolerance_seconds": DEFAULT_TOLERANCE_SECONDS,
        "retry_schedule_seconds": list(RETRY_SCHEDULE_SECONDS),
        "max_attempts": MAX_ATTEMPTS,
        "dead_letter": "超过最大次数的投递进入死信，可由管理员修复后重放（event_id 不变）",
        "rotation": "轮换后旧密钥在有效期内仍可验签（短期双密钥）",
        "note": ("交付语义为至少一次（接收方按 event_id 去重）；本产品不承诺事件顺序，"
                 "接收方按 aggregate.revision 丢弃旧状态"),
    }
