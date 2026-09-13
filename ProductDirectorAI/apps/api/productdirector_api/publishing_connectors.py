"""V6-11…15 平台连接器：TikTok / YouTube / Instagram Reels / Facebook Page。

诚实边界（对应主规划 12.11 与 `docs/integrations/*.md` 的官方资料调研）：
- 端点、scope、素材限制都来自官方文档（见各平台文档的"来源清单"）；**未在任何文档里确认的数值一律不写死**，
  标记为 `unconfirmed` 并在预检里给出 WARNING 而不是假装有平台硬限制。
- 没有配置应用凭据（client id/secret 等）时，连接器状态是 `NOT_CONFIGURED`：授权跳转、换 token、
  能力查询、提交发布全部返回明确原因，**不伪造任何平台动作**。
- `cancel_publish` 在不支持取消的平台显式返回 `unsupported`（TikTok/Instagram 官方不支持取消已提交发布）。
- 平台要求的人工交互（隐私选择、合成内容声明、页面角色等）必须保留，禁止用浏览器自动化绕过。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from . import publishing

HTTP_TIMEOUT_SECONDS = 20
# 平台不确认的素材限制不写死：这些值是"官方文档未确认"，预检只给 WARNING
UNCONFIRMED = "官方文档未确认，需在真实账号上实测后写入配置"


def _env(platform: str, key: str) -> str:
    return (os.getenv(f"PRODUCTDIRECTOR_{platform.upper()}_{key}", "") or "").strip()


def credentials_from_env(platform: str) -> dict:
    """读取该平台的应用凭据与已授权账号令牌（本部署默认为空 → NOT_CONFIGURED）。"""
    return {
        "client_id": _env(platform, "CLIENT_ID"),
        "client_secret": _env(platform, "CLIENT_SECRET"),
        "access_token": _env(platform, "ACCESS_TOKEN"),
        "refresh_token": _env(platform, "REFRESH_TOKEN"),
        "account_ref": _env(platform, "ACCOUNT_REF"),
        "page_id": _env(platform, "PAGE_ID"),
        "oauth_base": _env(platform, "OAUTH_BASE"),
    }


def redirect_uri() -> str:
    base = (os.getenv("PRODUCTDIRECTOR_PUBLIC_BASE_URL", "") or "http://127.0.0.1:8000").rstrip("/")
    return f"{base}/api/v1/publishing/oauth/callback"


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


# ---------------------------------------------------------------------------
# 平台定义：URL / scope / 预检限制全部来自 docs/integrations/*.md 的官方来源
# ---------------------------------------------------------------------------

PLATFORM_SPECS: dict[str, dict] = {
    "tiktok": {
        "display_name": "TikTok",
        "authorize_url": "https://www.tiktok.com/v2/auth/authorize/",
        "token_url": "https://open.tiktokapis.com/v2/oauth/token/",
        "revoke_url": "https://open.tiktokapis.com/v2/oauth/revoke/",
        "scopes": ["video.publish", "video.upload"],
        "creator_info_url": "https://open.tiktokapis.com/v2/post/publish/creator_info/query/",
        "status_url": "https://open.tiktokapis.com/v2/post/publish/status/fetch/",
        "init_url": "https://open.tiktokapis.com/v2/post/publish/video/init/",
        "requires": ["client_id", "client_secret", "access_token"],
        "credential_fields": {"client_id": "client_key", "client_secret": "client_secret"},
        "visibility_field": "privacy_level",
        "cancel_supported": False,
        "limits": {
            "max_duration_seconds": 600,
            "caption_limit": 2200,
            "aspect_hint": "9:16",
            "requires_creator_info": True,
            "visibility_options": list(publishing.VISIBILITY_BY_PLATFORM["tiktok"]),
        },
        "unconfirmed": ["封面图单独上传的限制", "重试与断点续传规则", "话题/提及数量上限",
                        "定时发布是否支持（按不支持处理）"],
        "docs": "docs/integrations/tiktok.md",
    },
    "youtube": {
        "display_name": "YouTube / Shorts",
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "revoke_url": "https://oauth2.googleapis.com/revoke",
        "scopes": ["https://www.googleapis.com/auth/youtube.upload",
                   "https://www.googleapis.com/auth/youtube.readonly"],
        "requires": ["client_id", "client_secret", "access_token"],
        "credential_fields": {"client_id": "client_id", "client_secret": "client_secret"},
        "visibility_field": "status.privacyStatus",
        "cancel_supported": True,
        "limits": {
            "caption_limit": 5000,
            "aspect_hint": "9:16（Shorts 判定由平台完成，无专用参数）",
            "requires_upload_audit_for_public": True,
            "visibility_options": list(publishing.VISIBILITY_BY_PLATFORM["youtube"]),
        },
        "unconfirmed": ["videos.insert 的配额成本（官方页面自相矛盾）", "单文件最大体积",
                        "完整受支持容器/编解码清单", "官方轮询间隔与重试上限"],
        "docs": "docs/integrations/youtube.md",
    },
    "instagram": {
        "display_name": "Instagram Reels",
        "authorize_url": "https://www.facebook.com/v21.0/dialog/oauth",
        "token_url": "https://graph.facebook.com/v21.0/oauth/access_token",
        "revoke_url": "https://graph.facebook.com/v21.0/{user-id}/permissions",
        "scopes": ["instagram_basic", "instagram_content_publish", "pages_read_engagement", "pages_show_list"],
        "requires": ["client_id", "client_secret", "access_token", "page_id"],
        "credential_fields": {"client_id": "client_id", "client_secret": "client_secret"},
        "visibility_field": "visibility",
        "cancel_supported": False,
        "limits": {
            "max_duration_seconds": 900,
            "min_duration_seconds": 3,
            "max_file_size_bytes": 300 * 1024 * 1024,
            "caption_limit": 2200,
            "aspect_hint": "9:16 推荐（官方允许 0.01:1–10:1）",
            "hosted_url_required": True,
            "visibility_options": list(publishing.VISIBILITY_BY_PLATFORM["instagram"]),
        },
        "unconfirmed": ["发布速率上限（官方两处为 50 与 100）", "发布调用用 User 还是 Page token",
                        "permalink 的可用时机", "media_product_type 的确切取值"],
        "docs": "docs/integrations/instagram.md",
    },
    "facebook_page": {
        "display_name": "Facebook Page",
        "authorize_url": "https://www.facebook.com/v21.0/dialog/oauth",
        "token_url": "https://graph.facebook.com/v21.0/oauth/access_token",
        "revoke_url": "https://graph.facebook.com/v21.0/{user-id}/permissions",
        "scopes": ["pages_show_list", "pages_read_engagement", "pages_manage_posts"],
        "requires": ["client_id", "client_secret", "access_token", "page_id"],
        "credential_fields": {"client_id": "client_id", "client_secret": "client_secret"},
        "visibility_field": "published",
        "cancel_supported": True,
        "limits": {
            "caption_limit": 5000,
            "aspect_hint": "页面视频发布（不含 Ads 广告系列与花费）",
            "visibility_options": list(publishing.VISIBILITY_BY_PLATFORM["facebook_page"]),
        },
        "unconfirmed": ["普通视频最大体积/时长/编码", "title/description 长度上限",
                        "Page token 的确切有效期", "普通 /videos 的频率限制"],
        "docs": "docs/integrations/facebook_page.md",
    },
}


def platform_spec(platform: str) -> dict:
    if platform not in PLATFORM_SPECS:
        raise publishing.PublishError("unknown_platform", f"未知平台 {platform}",
                                      detail={"platforms": list(PLATFORM_SPECS)})
    return PLATFORM_SPECS[platform]


def connector_status(platform: str, credentials: dict | None = None) -> dict:
    """连接器状态：缺任一必需凭据即 NOT_CONFIGURED，并列出缺失项与官方资料位置。"""
    spec = platform_spec(platform)
    credentials = credentials or {}
    missing = [item for item in spec["requires"] if not credentials.get(item)]
    base = publishing.connector_status(platform, {item: credentials.get(item) for item in ["registered_app"]})
    return {
        "platform": platform,
        "display_name": spec["display_name"],
        "native": True,
        "status": "READY" if not missing else "NOT_CONFIGURED",
        "missing_requirements": missing,
        "required_credentials": spec["requires"],
        "env_vars": [f"PRODUCTDIRECTOR_{platform.upper()}_{item.upper()}" for item in spec["requires"]],
        "scopes": spec["scopes"],
        "cancel_supported": spec["cancel_supported"],
        "limits": spec["limits"],
        "unconfirmed_limits": spec["unconfirmed"],
        "docs": spec["docs"],
        "requires_user_interaction": True,
        "notes": [
            "平台要求的人工交互（隐私/可见性选择、合成内容声明、页面角色）必须保留",
            "禁止使用浏览器自动化绕过平台授权或审核",
        ] + ([] if not missing else [f"未配置凭据：{', '.join(missing)}（配置后仍需真实账号完成授权）"]),
        "honest_note": base["honest_note"],
    }


def connector_overview() -> dict:
    connectors = [connector_status(platform, credentials_from_env(platform)) for platform in PLATFORM_SPECS]
    return {
        "connectors": connectors,
        "ready": [item["platform"] for item in connectors if item["status"] == "READY"],
        "blocked": [item["platform"] for item in connectors if item["status"] != "READY"],
        "note": ("四个默认原生连接器已实现接口与预检规则；没有真实授权时一律 BLOCKED。"
                 "本产品不把「打开平台网页」或「下载 MP4」当成一键发布完成。"),
        "oauth": {
            "redirect_uri": redirect_uri(),
            "state": "一次性 state 落库校验（防 CSRF）",
            "pkce": "S256 code_challenge（授权码流程）",
            "callback": "/api/v1/publishing/oauth/{platform}/callback",
        },
    }


# ---------------------------------------------------------------------------
# 连接器接口实现（HTTP 层真实存在；凭据缺失时抛 not_configured，不静默降级）
# ---------------------------------------------------------------------------

def _require(platform: str, credentials: dict, keys: tuple[str, ...]) -> None:
    spec = platform_spec(platform)
    missing = [item for item in keys if not (credentials or {}).get(item)]
    if missing:
        raise publishing.PublishError(
            "not_configured",
            f"{spec['display_name']} 连接器未配置：缺少 {', '.join(missing)}；"
            f"需要设置环境变量 {', '.join(f'PRODUCTDIRECTOR_{platform.upper()}_{item.upper()}' for item in missing)}"
            f"（官方资料见 {spec['docs']}）",
            status_code=409,
            detail={"platform": platform, "missing": missing, "docs": spec["docs"]},
        )


def _post_form(url: str, data: dict, *, headers: dict | None = None) -> dict:
    body = urllib.parse.urlencode(data).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        raise publishing.PublishError("upstream_http_error", f"上游返回 {exc.code}：{raw[:200]}",
                                      status_code=409, detail={"status": exc.code}) from exc
    except Exception as exc:
        raise publishing.PublishError("network_error", f"无法访问上游：{exc}", status_code=409) from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise publishing.PublishError("invalid_response", f"上游响应不是 JSON：{raw[:200]}",
                                      status_code=409) from exc


def _get_json(url: str, *, headers: dict | None = None) -> dict:
    request = urllib.request.Request(url, method="GET")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        raise publishing.PublishError("upstream_http_error", f"上游返回 {exc.code}：{raw[:200]}",
                                      status_code=409, detail={"status": exc.code}) from exc
    except Exception as exc:
        raise publishing.PublishError("network_error", f"无法访问上游：{exc}", status_code=409) from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise publishing.PublishError("invalid_response", f"上游响应不是 JSON：{raw[:200]}",
                                      status_code=409) from exc


def begin_authorization(platform: str, *, state: str, code_challenge: str, redirect_uri_value: str = "",
                        redirect_uri: str = "", credentials: dict | None = None) -> dict:
    """返回官方授权跳转信息（未配置客户端凭据时明确 BLOCKED，不生成假链接）。"""
    spec = platform_spec(platform)
    credentials = credentials or {}
    missing = [item for item in ("client_id", "client_secret") if not credentials.get(item)]
    params = {
        "client_id": credentials.get("client_id", "<未配置>"),
        "response_type": "code",
        "redirect_uri": redirect_uri_value or redirect_uri,
        "scope": " ".join(spec["scopes"]),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if platform == "tiktok":
        params["client_key"] = credentials.get("client_id", "<未配置>")
        params.pop("client_id")
    return {
        "platform": platform,
        "status": "BLOCKED" if missing else "READY",
        "missing_credentials": missing,
        "authorize_url": spec["authorize_url"],
        "parameters": params,
        "url": f"{spec['authorize_url']}?{urllib.parse.urlencode(params)}" if not missing else None,
        "scopes": spec["scopes"],
        "docs": spec["docs"],
        "note": ("未配置客户端凭据：不会生成假的授权链接；配置后仍需真实账号在平台侧完成授权与审批"
                 if missing else "授权链接已生成：state 与 PKCE 已落库，回调必须带回同一 state"),
    }


def exchange_callback(platform: str, *, code: str, code_verifier: str, redirect_uri: str,
                      credentials: dict | None = None) -> dict:
    """用授权码换 token。凭据缺失 → not_configured；上游失败 → 明确错误（不落库假账号）。"""
    spec = platform_spec(platform)
    _require(platform, credentials or {}, ("client_id", "client_secret"))
    data = {
        "client_id": credentials["client_id"],
        "client_secret": credentials["client_secret"],
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }
    if platform == "tiktok":
        data = {"client_key": credentials["client_id"], "client_secret": credentials["client_secret"],
                "code": code, "grant_type": "authorization_code", "redirect_uri": redirect_uri,
                "code_verifier": code_verifier}
    if platform in ("instagram", "facebook_page"):
        data.pop("code_verifier", None)
    payload = _post_form(credentials.get("oauth_base") or spec["token_url"], data)
    access_token = payload.get("access_token")
    if not access_token:
        raise publishing.PublishError("token_exchange_failed",
                                      f"上游未返回 access_token：{json.dumps(payload, ensure_ascii=False)[:200]}",
                                      status_code=409)
    return {
        "connected": True,
        "account_ref": payload.get("open_id") or payload.get("user_id") or payload.get("id")
        or credentials.get("account_ref") or "account-unknown",
        "display_name": payload.get("display_name") or payload.get("screen_name") or platform,
        "credential_ref": f"env:PRODUCTDIRECTOR_{platform.upper()}_ACCESS_TOKEN",
        "scopes": (payload.get("scope") or "").split() or spec["scopes"],
        # 令牌本身不落库、不回传：只保存引用与过期信息
        "expires_in": payload.get("expires_in"),
        "refresh_expires_in": payload.get("refresh_expires_in"),
        "capabilities": {},
        "note": ("令牌只保存引用（credential_ref），不写明文、不回传；"
                 "能力需要再调 get_account_capabilities 才会变成已核验"),
    }


def refresh_authorization(platform: str, *, credentials: dict | None = None) -> dict:
    spec = platform_spec(platform)
    credentials = credentials or {}
    _require(platform, credentials, ("client_id", "client_secret", "refresh_token"))
    data = {"client_id": credentials["client_id"], "client_secret": credentials["client_secret"],
            "grant_type": "refresh_token", "refresh_token": credentials["refresh_token"]}
    if platform == "tiktok":
        data = {"client_key": credentials["client_id"], "client_secret": credentials["client_secret"],
                "grant_type": "refresh_token", "refresh_token": credentials["refresh_token"]}
    payload = _post_form(credentials.get("oauth_base") or spec["token_url"], data)
    if not payload.get("access_token"):
        # 平台刷新失败 → AUTH_REQUIRED，不反复盲目刷新
        return {"refreshed": False, "state": "AUTH_REQUIRED",
                "reason": f"上游未返回新令牌：{json.dumps(payload, ensure_ascii=False)[:160]}",
                "note": "刷新失败必须回到 AUTH_REQUIRED 由用户重新授权，不循环重试"}
    return {"refreshed": True, "expires_in": payload.get("expires_in"),
            "credential_ref": f"env:PRODUCTDIRECTOR_{platform.upper()}_ACCESS_TOKEN",
            "note": "新 refresh_token 若与旧值不同必须替换保存（TikTok 官方明确说明）"}


def get_account_capabilities(platform: str, account_ref: str, credentials: dict | None = None) -> dict:
    """实时查询账号能力：TikTok 用 creator_info（发布前必须），其他平台按官方能力接口。"""
    spec = platform_spec(platform)
    credentials = credentials or {}
    _require(platform, credentials, ("access_token",))
    if platform == "tiktok":
        payload = _post_form(spec["creator_info_url"], {},
                             headers={"Authorization": f"Bearer {credentials['access_token']}"})
        data = payload.get("data") or {}
        return {
            "platform": platform, "account_ref": account_ref, "checked_at": time.time(),
            "privacy_level_options": data.get("privacy_level_options"),
            "max_video_post_duration_sec": data.get("max_video_post_duration_sec"),
            "comment_disabled": data.get("comment_disabled"),
            "duet_disabled": data.get("duet_disabled"),
            "stitch_disabled": data.get("stitch_disabled"),
            "unconfirmed": spec["unconfirmed"],
        }
    if platform in ("instagram", "facebook_page"):
        page_id = credentials.get("page_id")
        url = (f"https://graph.facebook.com/v21.0/{page_id}?fields=id,name,username"
               f"&access_token={urllib.parse.quote(credentials['access_token'])}")
        payload = _get_json(url)
        return {"platform": platform, "account_ref": account_ref, "checked_at": time.time(),
                "page": payload, "unconfirmed": spec["unconfirmed"],
                "note": "PPA/2FA 等前置条件无法通过 API 判断（官方文档明确）"}
    url = ("https://www.googleapis.com/youtube/v3/channels?part=id,snippet,status&mine=true"
           f"&access_token={urllib.parse.quote(credentials['access_token'])}")
    payload = _get_json(url)
    items = payload.get("items") or []
    return {"platform": platform, "account_ref": account_ref, "checked_at": time.time(),
            "channel": (items[0] if items else None), "unconfirmed": spec["unconfirmed"],
            "note": "未验证项目上传的视频会被锁为 private（官方说明）"}


def validate_package(platform: str, package: dict, account: dict, options: dict,
                     credentials: dict | None = None) -> dict:
    """平台级素材校验：只使用官方确认的限制；未确认的项目给 WARNING。"""
    spec = platform_spec(platform)
    limits = spec["limits"]
    problems: list[dict] = []
    warnings: list[dict] = [{"code": "unconfirmed_limit", "detail": f"{item}：{UNCONFIRMED}"}
                            for item in spec["unconfirmed"]]
    duration = float(package.get("duration_seconds") or 0)
    if limits.get("max_duration_seconds") and duration > limits["max_duration_seconds"]:
        problems.append({"code": "duration_exceeds_platform", "severity": "BLOCKING",
                         "detail": f"时长 {duration}s 超过 {spec['display_name']} 上限 {limits['max_duration_seconds']}s"})
    if limits.get("min_duration_seconds") and duration and duration < limits["min_duration_seconds"]:
        problems.append({"code": "duration_below_platform", "severity": "BLOCKING",
                         "detail": f"时长 {duration}s 低于 {spec['display_name']} 下限 {limits['min_duration_seconds']}s"})
    caption = str(options.get("caption") or "")
    if limits.get("caption_limit") and len(caption) > limits["caption_limit"]:
        problems.append({"code": "caption_too_long", "severity": "BLOCKING",
                         "detail": f"文案 {len(caption)} 字符超过上限 {limits['caption_limit']}"})
    if limits.get("hosted_url_required") and not options.get("video_url"):
        problems.append({"code": "hosted_url_required", "severity": "BLOCKING",
                         "detail": f"{spec['display_name']} 要求视频可通过公网 URL 访问（不支持二进制直传）"})
    return {"platform": platform, "blocking": problems, "warnings": warnings,
            "limits_source": spec["docs"]}


def submit_publish(platform: str, package: dict, options: dict, execution_key: str,
                   credentials: dict | None = None) -> dict:
    """提交发布：凭据缺失或缺少平台前置条件时明确 BLOCKED，不伪造提交结果。"""
    spec = platform_spec(platform)
    credentials = credentials or {}
    _require(platform, credentials, ("access_token",))
    if platform == "tiktok" and spec["limits"].get("requires_creator_info") and not options.get("creator_info_checked"):
        raise publishing.PublishError("creator_info_required",
                                      "TikTok 官方要求创建发布请求前查询 creator info（含可见性选项与时长上限）",
                                      status_code=409, detail={"docs": spec["docs"]})
    return {
        "submitted": False,
        "state": "BLOCKED",
        "reason": "connector_submission_not_enabled_in_this_deployment",
        "note": ("连接器已具备官方端点与参数映射，但本部署没有真实账号授权与平台侧审批，"
                 "因此不会发起真实上传；配置凭据并在真实账号上完成授权后才能启用提交"),
        "endpoint": spec.get("init_url") or spec["token_url"],
        "execution_key": execution_key,
        "docs": spec["docs"],
    }


def query_publish(platform: str, external_id: str | None, credentials: dict | None = None) -> dict:
    spec = platform_spec(platform)
    credentials = credentials or {}
    _require(platform, credentials, ("access_token",))
    return {"status": "UNKNOWN", "external_id": external_id,
            "note": f"{spec['display_name']} 未配置真实授权：无法查询上游状态（不假装已发布）"}


def cancel_publish(platform: str, external_id: str | None, credentials: dict | None = None) -> dict:
    spec = platform_spec(platform)
    if not spec["cancel_supported"]:
        raise publishing.PublishError(
            "cancel_unsupported",
            f"{spec['display_name']} 官方接口不支持取消已提交的发布（只能停止本地轮询）",
            status_code=409, detail={"docs": spec["docs"]})
    credentials = credentials or {}
    _require(platform, credentials, ("access_token",))
    return {"cancelled": False, "state": "CANCEL_REQUESTED",
            "note": f"{spec['display_name']} 取消需要真实授权：当前未配置，任务保持 CANCEL_REQUESTED",
            "docs": spec["docs"]}
