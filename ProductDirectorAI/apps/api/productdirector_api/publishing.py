"""V6-10…15 发布框架：连接器接口、状态机、预检、审批绑定与防重。

诚实边界（对应主规划 12.11/12.12/12.13）：
- 四个默认原生连接器（TikTok / YouTube / Instagram / Facebook Page）在**没有真实授权**时一律
  `BLOCKED` + `NOT_CONFIGURED`：本模块不做任何"假装发布成功"，也不把"打开平台网页/下载 MP4"当成一键发布。
- 状态机区分「提交成功」「上传完成」「平台处理中」「已发布」；只有查询或可信回调确认最终成功才标 `PUBLISHED`。
- 拿不到公开链接时只保留平台 ID，**不捏造 URL**。
- 防重键 `package_version + account + publish_intent_id`；用户再次发布必须创建新 intent。
- 上游无幂等能力且提交结果未知 → `RECONCILING`/`BLOCKED`，通过既有 ID 或人工核对；不宣称"恰好一次发布"。
- 审批绑定 package hash / 账号 / 平台 / 文案 / 可见性 / 范围 / 失效时间；任何绑定字段变化都会使审批失效。
"""
from __future__ import annotations

import hashlib
import json
from typing import Literal

PUBLISH_STATES = (
    "DRAFT", "PREFLIGHT", "WAITING_APPROVAL", "QUEUED", "UPLOADING", "PROCESSING", "PUBLISHED",
    "AUTH_REQUIRED", "BLOCKED", "RECONCILING", "FAILED", "CANCEL_REQUESTED", "CANCELLED",
)
TERMINAL_STATES = ("PUBLISHED", "FAILED", "CANCELLED", "BLOCKED")
ACTIVE_STATES = ("QUEUED", "UPLOADING", "PROCESSING", "CANCEL_REQUESTED", "RECONCILING")

# 允许的状态迁移：显式列出，避免"提交成功即已发布"这类语义混淆
TRANSITIONS: dict[str, tuple[str, ...]] = {
    "DRAFT": ("PREFLIGHT", "CANCELLED"),
    "PREFLIGHT": ("WAITING_APPROVAL", "BLOCKED", "AUTH_REQUIRED", "FAILED", "CANCELLED"),
    "WAITING_APPROVAL": ("QUEUED", "BLOCKED", "CANCELLED", "FAILED"),
    "QUEUED": ("UPLOADING", "BLOCKED", "AUTH_REQUIRED", "RECONCILING", "FAILED", "CANCEL_REQUESTED"),
    "UPLOADING": ("PROCESSING", "RECONCILING", "FAILED", "AUTH_REQUIRED", "CANCEL_REQUESTED"),
    "PROCESSING": ("PUBLISHED", "FAILED", "RECONCILING", "CANCEL_REQUESTED"),
    "RECONCILING": ("PUBLISHED", "FAILED", "QUEUED", "BLOCKED", "CANCELLED"),
    "CANCEL_REQUESTED": ("CANCELLED", "PUBLISHED", "FAILED", "RECONCILING"),
    "AUTH_REQUIRED": ("QUEUED", "BLOCKED", "CANCELLED", "FAILED"),
    "PUBLISHED": (),
    "FAILED": ("QUEUED",),
    "CANCELLED": (),
    "BLOCKED": ("QUEUED", "CANCELLED"),
}

PLATFORMS = ("tiktok", "youtube", "instagram", "facebook_page")

# 连接器能力：只有真实授权后才会变成可用；这里声明"本产品会做什么/不会做什么"
CONNECTOR_CAPABILITIES: dict[str, dict] = {
    "tiktok": {
        "display_name": "TikTok", "native": True,
        "requires": ["registered_app", "direct_post_scope_approval", "user_authorization", "creator_info_query"],
        "cancel_supported": False,
        "notes": ["未审核客户端存在私密可见限制", "创建发布前必须查询 creator info", "取消发布上游不支持，只能显式返回 unsupported"],
        "docs": "docs/integrations/tiktok.md",
    },
    "youtube": {
        "display_name": "YouTube / Shorts", "native": True,
        "requires": ["oauth_client", "youtube.upload_scope", "verified_channel_for_thumbnail"],
        "cancel_supported": True,
        "notes": ["上传成功不等于处理完成", "Shorts 无独立参数：按比例与时长判定", "删除已发布视频属于取消后的独立动作"],
        "docs": "docs/integrations/youtube.md",
    },
    "instagram": {
        "display_name": "Instagram Reels", "native": True,
        "requires": ["professional_account", "facebook_page_link", "instagram_content_publish", "hosted_video_url"],
        "cancel_supported": False,
        "notes": ["视频必须可通过公网 URL 访问，不支持二进制直传", "容器状态需要轮询", "账号必须为专业账号并绑定 Page"],
        "docs": "docs/integrations/instagram.md",
    },
    "facebook_page": {
        "display_name": "Facebook Page", "native": True,
        "requires": ["page_access_token", "pages_manage_posts", "publish_video"],
        "cancel_supported": True,
        "notes": ["范围只到页面视频发布，不含 Ads 广告系列与花费", "上传完成后仍可能处于处理中"],
        "docs": "docs/integrations/facebook_page.md",
    },
}

VISIBILITY_BY_PLATFORM: dict[str, tuple[str, ...]] = {
    "tiktok": ("public_to_everyone", "mutual_follow_friends", "followers", "self_only"),
    "youtube": ("public", "unlisted", "private"),
    "instagram": ("public", "close_friends"),  # 仅文档明确的取值；不复制其他平台的隐私项
    "facebook_page": ("public", "unpublished"),
}

REQUIRED_DISCLOSURE_FIELDS = ("is_synthetic_media", "is_branded_content", "made_for_kids")


class PublishError(ValueError):
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


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def can_transition(current: str, target: str) -> bool:
    return target in TRANSITIONS.get(current, ())


def check_transition(current: str, target: str) -> None:
    if current not in PUBLISH_STATES:
        raise PublishError("unknown_state", f"未知发布状态 {current}", status_code=409)
    if not can_transition(current, target):
        raise PublishError(
            "invalid_transition",
            f"不允许的状态迁移 {current} → {target}",
            status_code=409,
            detail={"allowed": list(TRANSITIONS.get(current, ()))},
        )


def dedupe_key(*, package_version_id: str, account_id: str, publish_intent_id: str) -> str:
    """防重键：同一包版本 + 账号 + 意图只允许存在一个发布任务。"""
    return sha256_text(f"{package_version_id}|{account_id}|{publish_intent_id}")


def connector_status(platform: str, credentials: dict | None) -> dict:
    """连接器可用性：没有真实授权就如实 BLOCKED，不做任何假装。"""
    if platform not in CONNECTOR_CAPABILITIES:
        raise PublishError("unknown_platform", f"未知平台 {platform}", detail={"platforms": list(PLATFORMS)})
    capability = CONNECTOR_CAPABILITIES[platform]
    credentials = credentials or {}
    missing = [item for item in capability["requires"] if not credentials.get(item)]
    ready = not missing
    return {
        "platform": platform,
        "display_name": capability["display_name"],
        "native": capability["native"],
        "status": "READY" if ready else "NOT_CONFIGURED",
        "missing_requirements": missing,
        "cancel_supported": capability["cancel_supported"],
        "notes": capability["notes"],
        "docs": capability["docs"],
        "honest_note": ("本产品不把「打开平台网页」或「下载 MP4」当作一键发布完成；未配置授权时一律 BLOCKED"
                        if not ready else "已具备配置项：仍需在真实账号上完成授权才能发布"),
    }


def preflight(*, platform: str, package: dict, account: dict, options: dict,
              connector: dict) -> dict:
    """预检：逐条给出可执行/阻断结果，并给出可用于审批绑定的快照 hash。"""
    problems: list[dict] = []
    warnings: list[dict] = []

    def block(code: str, message: str) -> None:
        problems.append({"code": code, "severity": "BLOCKING", "detail": message})

    def warn(code: str, message: str) -> None:
        warnings.append({"code": code, "severity": "WARNING", "detail": message})

    if platform not in CONNECTOR_CAPABILITIES:
        block("unknown_platform", f"未知平台 {platform}")
    if connector.get("status") != "READY":
        block("connector_not_configured",
              f"连接器未配置（缺少：{', '.join(connector.get('missing_requirements') or [])}）")
    if not account.get("connected"):
        block("account_not_connected", "账号未授权或已断开")
    if account.get("authorization_status") in ("AUTH_REQUIRED", "REVOKED", "EXPIRED"):
        block("account_authorization", f"账号授权状态：{account.get('authorization_status')}")
    if account.get("capabilities_checked_at") is None:
        warn("capabilities_stale", "账号能力未实时查询：发布前必须刷新账号能力")

    if package.get("status") != "APPROVED":
        block("package_not_approved", f"发布包状态为 {package.get('status')}，只有已审批的包可以发布")
    if not package.get("content_hash"):
        block("package_missing_hash", "发布包缺少内容哈希：无法绑定审批")
    if package.get("content_hash") and package.get("stored_hash") \
            and package["content_hash"] != package["stored_hash"]:
        block("package_hash_mismatch", "包内容哈希与构建时不一致（文件被修改）")
    if package.get("qa_passed") is not True:
        block("qa_not_passed", "QA 未通过或缺失：不能进入发布流程")
    if package.get("approval_ref") is None:
        block("approval_missing", "发布包缺少审批引用")

    visibility = options.get("visibility")
    allowed_visibility = VISIBILITY_BY_PLATFORM.get(platform, ())
    if not visibility:
        block("visibility_required", "必须显式选择目标可见性（不复制其他平台的隐私选项）")
    elif allowed_visibility and visibility not in allowed_visibility:
        block("visibility_unsupported",
              f"{platform} 不支持可见性 {visibility}（可选：{list(allowed_visibility)}）")

    disclosures = options.get("disclosures") or {}
    for field in REQUIRED_DISCLOSURE_FIELDS:
        if field not in disclosures:
            block("disclosure_missing", f"缺少必需声明字段：{field}")
    if disclosures.get("is_synthetic_media") is None:
        block("synthetic_media_declaration_required", "必须声明内容是否为合成/AI 生成素材")

    if options.get("caption"):
        if len(str(options["caption"])) > int(options.get("caption_limit") or 2200):
            block("caption_too_long", "文案超出平台上限")
    else:
        warn("caption_empty", "未提供文案")

    if package.get("music_license_ref") is None and package.get("has_music"):
        block("music_license_missing", "包内含 BGM 但没有许可引用")
    if package.get("unlicensed_font"):
        block("font_license_missing", "字幕字体缺少许可")

    duration = float(package.get("duration_seconds") or 0)
    max_duration = options.get("max_duration_seconds")
    if max_duration and duration > float(max_duration):
        block("duration_exceeds_platform", f"时长 {duration}s 超过平台上限 {max_duration}s")
    if duration <= 0:
        block("duration_missing", "包内缺少有效时长")

    if options.get("scheduled_at") and not options.get("timezone"):
        block("timezone_required", "定时发布必须带时区")

    snapshot = {
        "platform": platform,
        "package_id": package.get("id"),
        "package_version_id": package.get("version_id") or package.get("id"),
        "content_hash": package.get("content_hash"),
        "approval_ref": package.get("approval_ref"),
        "account_id": account.get("id"),
        "account_revision": account.get("revision"),
        "visibility": visibility,
        "caption": options.get("caption"),
        "disclosures": {field: disclosures.get(field) for field in REQUIRED_DISCLOSURE_FIELDS},
        "scheduled_at": options.get("scheduled_at"),
        "timezone": options.get("timezone"),
    }
    return {
        "platform": platform,
        "executable": not problems,
        "blocking": problems,
        "warnings": warnings,
        "snapshot": snapshot,
        "snapshot_hash": sha256_text(canonical(snapshot)),
        "note": "预检结果绑定到快照哈希：审批必须引用该哈希，任何绑定字段变化都会使审批失效",
    }


def build_approval(*, preflight_result: dict, actor: str, expires_in_seconds: int,
                   confirmations: dict, scope: str = "single_publish") -> dict:
    """审批：必须精确引用预检快照哈希，并显式确认关键字段（不接受模糊同意）。"""
    if not preflight_result.get("executable"):
        raise PublishError("preflight_not_executable", "预检未通过：不允许创建审批",
                           status_code=409, detail={"blocking": preflight_result.get("blocking")})
    required = ("package_hash", "account", "visibility", "disclosures")
    missing = [item for item in required if not (confirmations or {}).get(item)]
    if missing:
        raise PublishError("confirmation_required",
                           f"审批必须显式确认：{', '.join(missing)}",
                           detail={"required_confirmations": list(required)})
    if scope not in ("single_publish", "automation_policy"):
        raise PublishError("unknown_scope", "审批范围只支持 single_publish 或 automation_policy")
    if expires_in_seconds <= 0 or expires_in_seconds > 30 * 86400:
        raise PublishError("invalid_expiry", "审批有效期需在 1 秒到 30 天之间")
    snapshot = preflight_result["snapshot"]
    return {
        "scope": scope,
        "snapshot_hash": preflight_result["snapshot_hash"],
        "binding": {
            "package_version_id": snapshot["package_version_id"],
            "content_hash": snapshot["content_hash"],
            "account_id": snapshot["account_id"],
            "platform": snapshot["platform"],
            "visibility": snapshot["visibility"],
            "caption": snapshot["caption"],
            "disclosures": snapshot["disclosures"],
        },
        "actor": actor,
        "expires_in_seconds": int(expires_in_seconds),
        "confirmations": {key: bool(value) for key, value in (confirmations or {}).items()},
        "note": ("审批绑定包哈希/账号/平台/文案/可见性/声明与范围；任何绑定字段变化都会使审批失效。"
                 "automation_policy 只在平台规则允许范围内生效，且可撤销。"),
    }


def approval_still_valid(*, approval_binding: dict, current_snapshot: dict) -> dict:
    """审批失效检查：逐字段比对，任何差异都视为失效（不做"大致相同"判断）。"""
    mismatches = []
    for field, expected in (approval_binding or {}).items():
        actual = current_snapshot.get(field)
        if canonical({"v": expected}) != canonical({"v": actual}):
            mismatches.append({"field": field, "approved": expected, "current": actual})
    return {"valid": not mismatches, "mismatches": mismatches,
            "note": "绑定字段变化即失效：需要重新预检并重新审批" if mismatches else "审批与当前快照一致"}


def submit_plan(*, connector: dict, package: dict, options: dict, execution_key: str,
                approval: dict) -> dict:
    """提交计划：连接器未配置/审批无效时拒绝执行，并明确说明原因（不静默降级）。"""
    if connector.get("status") != "READY":
        return {"accepted": False, "state": "BLOCKED", "reason": "connector_not_configured",
                "detail": f"缺少：{', '.join(connector.get('missing_requirements') or [])}",
                "note": "连接器未配置：本产品不会假装发布成功"}
    if not approval.get("valid"):
        return {"accepted": False, "state": "WAITING_APPROVAL", "reason": "approval_invalid",
                "detail": approval.get("mismatches"),
                "note": "审批与当前快照不一致：需要重新预检并重新审批"}
    if not execution_key:
        return {"accepted": False, "state": "FAILED", "reason": "execution_key_required",
                "detail": "提交必须带 execution_key（package_version + account + publish_intent_id）"}
    return {"accepted": True, "state": "QUEUED", "execution_key": execution_key,
            "note": "提交成功只是进入上传队列；上传完成与平台处理完成都不等于已发布",
            "options": dict(options or {})}


def interpret_query(*, operation_handle: dict | None, platform: str) -> dict:
    """上游查询结果解释：只按上游实际状态给结论，不把"提交成功"升级成"已发布"。"""
    if not operation_handle:
        return {"state": "RECONCILING", "reason": "no_handle",
                "note": "没有可查询的句柄：结果未知，进入对账而不是宣称成功"}
    status = str(operation_handle.get("status") or "").upper()
    external_id = operation_handle.get("external_id")
    permalink = operation_handle.get("permalink_url")
    if status in ("PUBLISHED", "SUCCEEDED", "COMPLETE", "FINISHED"):
        return {
            "state": "PUBLISHED",
            "external_publish_id": external_id,
            "permalink_url": permalink,
            "visibility": operation_handle.get("visibility"),
            "completed_at": operation_handle.get("completed_at"),
            "note": ("平台确认发布完成" if permalink else
                     "平台确认成功但没有公开链接：只保留平台 ID，不捏造 URL"),
            "fabricated_url": False,
        }
    if status in ("PROCESSING", "UPLOADING", "IN_PROGRESS", "PENDING"):
        return {"state": "PROCESSING", "external_publish_id": external_id,
                "note": "上游处理中：上传完成不等于已发布"}
    if status in ("FAILED", "ERROR", "REJECTED"):
        return {"state": "FAILED", "external_publish_id": external_id,
                "error": operation_handle.get("error") or status,
                "note": "上游失败：按是否可安全重试决定是否 retry"}
    if status in ("AUTH_REQUIRED", "TOKEN_INVALID"):
        return {"state": "AUTH_REQUIRED", "note": "授权失效：需要重新授权，不反复盲目刷新"}
    if status in ("UNKNOWN", "", None):
        return {"state": "RECONCILING", "external_publish_id": external_id,
                "note": "上游返回未知状态：进入对账，通过已有 ID 或人工核对"}
    return {"state": "RECONCILING", "external_publish_id": external_id,
            "note": f"未识别状态 {status}：进入对账"}


def cancel_capability(platform: str, state: str) -> dict:
    """取消支持程度：显式返回 unsupported 也合法（不假装取消成功）。"""
    capability = CONNECTOR_CAPABILITIES.get(platform)
    if capability is None:
        raise PublishError("unknown_platform", f"未知平台 {platform}")
    if state in TERMINAL_STATES:
        return {"supported": False, "effect": "none", "reason": f"任务已处于终态 {state}"}
    if not capability["cancel_supported"]:
        return {"supported": False, "effect": "none",
                "reason": f"{capability['display_name']} 官方接口不支持取消已提交的发布：只能如实返回 unsupported"}
    return {"supported": True, "effect": "stop_in_flight_or_delete_post",
            "note": "取消的含义由平台决定：可能只是停止后续处理，也可能删除已发布内容"}


def retry_decision(*, state: str, failure_code: str | None, submission_unknown: bool) -> dict:
    """重试判定：只对确定可安全重试的失败执行；结果未知时先对账。"""
    if submission_unknown:
        return {"retry": False, "reason": "submission_unknown",
                "note": "提交结果未知：必须先对账（reconcile），否则可能造成重复发布"}
    if state not in ("FAILED", "BLOCKED", "AUTH_REQUIRED"):
        return {"retry": False, "reason": "not_retryable_state",
                "note": f"状态 {state} 不允许重试"}
    if failure_code in ("rate_limited", "upstream_5xx", "network"):
        return {"retry": True, "reason": failure_code,
                "note": "上游明确失败且未产生发布：可安全重试"}
    if failure_code in ("auth_expired",):
        return {"retry": False, "reason": "auth_expired",
                "note": "先重新授权，再决定是否重试（不盲目刷新）"}
    return {"retry": False, "reason": failure_code or "unknown_failure",
            "note": "失败原因不明确：需要人工确认后再重试"}


def job_public(row, *, connector: dict | None = None) -> dict:
    payload = {}
    try:
        payload = json.loads(row["payload"]) if row["payload"] else {}
    except (json.JSONDecodeError, TypeError, KeyError):
        payload = {}
    return {
        "id": row["id"],
        "platform": row["platform"],
        "account_id": row["account_id"],
        "package_id": row["package_id"],
        "package_version_id": row["package_version_id"],
        "publish_intent_id": row["publish_intent_id"],
        "state": row["state"],
        "previous_state": payload.get("previous_state"),
        "visibility": payload.get("visibility"),
        "external_publish_id": row["external_publish_id"],
        "permalink_url": row["permalink_url"],
        "blocked_reason": payload.get("blocked_reason"),
        "last_error": row["last_error"],
        "approval_id": row["approval_id"],
        "attempt_count": row["attempt_count"],
        "submission_unknown": bool(payload.get("submission_unknown")),
        "history": payload.get("history", []),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "connector": connector,
        "honest_note": ("提交成功 ≠ 已发布；只有平台查询或可信回调确认后才标 PUBLISHED"
                        if row["state"] not in ("PUBLISHED",) else
                        ("已发布（有公开链接）" if row["permalink_url"] else
                         "平台确认成功但没有公开链接：只保留平台 ID")),
    }


def append_history(payload: dict, state: str, note: str, at: str) -> dict:
    history = list(payload.get("history") or [])
    history.append({"state": state, "note": note, "at": at})
    payload["history"] = history[-40:]
    return payload
