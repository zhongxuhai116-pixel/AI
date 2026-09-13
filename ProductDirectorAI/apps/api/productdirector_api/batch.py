"""V6-04 批次与变体：矩阵展开、相容性校验、去重提示、缓存键与状态聚合。

诚实边界（对应主规划 12.5）：
- **展开数量用服务端结果说话**：`产品版本 × 计划版本 × Profile 版本 × variations`，
  响应同时返回总数与逐项明细，避免 "count=6 到底是总数还是每平台数" 的歧义。
- 矩阵字段与显式 `items[]` **互斥**；同时提交直接 422，不做隐式取舍。
- 已批准 Plan 绑定固定 ProductVersion：矩阵里任何不相容组合都会让整次提交 422
  并逐条列出冲突；**不允许**偷偷替换计划内产品或悄悄少生成几项。
- 相同输入/配置/种子只做**去重提示**（需要 `request_item_key` 才能保留重复生产），
  不擅自合并或丢弃用户要求的任务。
- 批次状态描述生产；发布聚合状态单独表达（本模块不把生产完成等同于已发布）。
"""
from __future__ import annotations

import hashlib
import json
from typing import Literal

MAX_EXPANDED_ITEMS = 100
DEFAULT_MAX_CONCURRENT = 2

BATCH_STATUSES = (
    "DRAFT", "QUEUED", "RUNNING", "PAUSED", "WAITING_REVIEW",
    "COMPLETED", "PARTIAL_FAILED", "FAILED", "CANCELLED",
)
ITEM_STATUSES = ("PENDING", "QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED", "SKIPPED")


class ExpansionError(ValueError):
    """展开不可行（矩阵与 items 冲突、组合不相容、超出上限等）。"""

    def __init__(self, code: str, message: str, conflicts: list[dict] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.conflicts = conflicts or []

    def as_detail(self) -> dict:
        return {"code": self.code, "detail": self.message, "conflicts": self.conflicts}


def cache_key(
    *, product_version_id: str, plan_id: str, profile_version_id: str, variation: int,
    postproduction_preset_version_id: str = "", seed: int | None = None,
) -> str:
    """共享计算键：同一产品/计划/规格/后期模板/种子 → 同一 Master 可复用。"""
    payload = {
        "product_version_id": product_version_id,
        "plan_id": plan_id,
        "profile_version_id": profile_version_id,
        "variation": variation,
        "postproduction_preset_version_id": postproduction_preset_version_id,
        "seed": seed,
    }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def expand_matrix(
    *,
    product_versions: list[dict], plan_versions: list[dict], profile_versions: list[dict],
    variations_per_combination: int, seed_policy: str = "plan_derived",
    base_seed: int | None = None, postproduction_preset_version_id: str = "",
    requested_keys: list[str] | None = None,
) -> dict:
    """按 产品版本 × 计划版本 × Profile 版本 × 变体数 展开；返回逐项明细与去重提示。"""
    if variations_per_combination < 1:
        raise ExpansionError("invalid_variations", "variations_per_combination 必须 ≥ 1")
    if not plan_versions:
        raise ExpansionError("no_plans", "至少需要一个已批准计划版本")
    if not profile_versions:
        raise ExpansionError("no_profiles", "至少需要一个 Profile 版本")
    if not product_versions:
        raise ExpansionError("no_products", "至少需要一个产品版本")
    total = len(product_versions) * len(plan_versions) * len(profile_versions) * variations_per_combination
    if total > MAX_EXPANDED_ITEMS:
        raise ExpansionError(
            "over_limit",
            f"展开 {total} 项超过单次上限 {MAX_EXPANDED_ITEMS}：请拆分批次或减少变体数",
        )
    conflicts: list[dict] = []
    items: list[dict] = []
    seen_signatures: dict[str, int] = {}
    duplicates: list[dict] = []
    keys = list(requested_keys or [])
    index = 0
    for product in product_versions:
        for plan in plan_versions:
            # 已批准计划绑定固定产品版本：产品不匹配即为不相容组合
            if plan.get("product_asset_id") and product.get("product_asset_id") \
                    and plan["product_asset_id"] != product["product_asset_id"]:
                conflicts.append({
                    "plan_id": plan.get("plan_id"), "product_version_id": product.get("id"),
                    "reason": "已批准计划绑定的产品与所选产品版本不一致",
                    "plan_product_asset_id": plan["product_asset_id"],
                    "product_asset_id": product["product_asset_id"],
                })
                continue
            for profile in profile_versions:
                for variation in range(1, variations_per_combination + 1):
                    if seed_policy == "fixed" and base_seed is not None:
                        seed = base_seed
                    elif seed_policy == "per_variation":
                        seed = (base_seed or 0) + variation - 1
                    else:  # plan_derived：由计划与变体确定，可复现
                        seed = int(hashlib.sha256(
                            f"{plan.get('plan_id')}:{variation}".encode("utf-8")
                        ).hexdigest()[:8], 16)
                    key = cache_key(
                        product_version_id=product.get("id", ""), plan_id=plan.get("plan_id", ""),
                        profile_version_id=profile.get("id", ""), variation=variation,
                        postproduction_preset_version_id=postproduction_preset_version_id,
                        seed=seed,
                    )
                    signature = key
                    item = {
                        "index": index, "cache_key": key,
                        "product_version_id": product.get("id"),
                        "plan_id": plan.get("plan_id"),
                        "profile_id": profile.get("profile_id"),
                        "profile_version_id": profile.get("id"),
                        "variation": variation, "seed": seed,
                        "request_item_key": keys[index] if index < len(keys) else "",
                    }
                    if signature in seen_signatures:
                        duplicates.append({
                            "index": index, "duplicate_of": seen_signatures[signature],
                            "cache_key": key,
                            "detail": "相同输入/配置/种子：如需重复生产请为该行提供不同的 request_item_key",
                        })
                    else:
                        seen_signatures[signature] = index
                    items.append(item)
                    index += 1
    if conflicts:
        raise ExpansionError(
            "incompatible_combination",
            f"{len(conflicts)} 个组合不相容（已批准计划绑定固定产品版本）",
            conflicts,
        )
    return {
        "expanded_count": len(items),
        "requested_total": total,
        "items": items,
        "duplicates": duplicates,
        "dedupe_note": "重复项仅提示，不会自动合并；确认重复生产请使用不同的 request_item_key",
    }


def expand_items(items: list[dict]) -> dict:
    """显式逐行任务：每行必须给定计划与 Profile；不允许与矩阵字段同时出现。"""
    if not items:
        raise ExpansionError("no_items", "items[] 为空")
    if len(items) > MAX_EXPANDED_ITEMS:
        raise ExpansionError("over_limit", f"items 数 {len(items)} 超过上限 {MAX_EXPANDED_ITEMS}")
    expanded = []
    seen: dict[str, int] = {}
    duplicates = []
    for index, item in enumerate(items):
        missing = [field for field in ("plan_id", "profile_id") if not item.get(field)]
        if missing:
            raise ExpansionError("item_missing_field", f"第 {index + 1} 行缺少 {missing}", [item])
        seed = item.get("seed")
        key = cache_key(
            product_version_id=str(item.get("product_version_id") or ""),
            plan_id=str(item["plan_id"]), profile_version_id=str(item.get("profile_version_id") or item["profile_id"]),
            variation=int(item.get("variation") or 1),
            postproduction_preset_version_id=str(item.get("postproduction_preset_version_id") or ""),
            seed=seed,
        )
        explicit_key = str(item.get("request_item_key") or "")
        signature = explicit_key or key
        entry = {
            "index": index, "cache_key": key, "product_version_id": item.get("product_version_id"),
            "plan_id": item["plan_id"], "profile_id": item["profile_id"],
            "profile_version_id": item.get("profile_version_id"),
            "variation": int(item.get("variation") or 1), "seed": seed,
            "request_item_key": explicit_key,
        }
        if signature in seen:
            duplicates.append({"index": index, "duplicate_of": seen[signature], "cache_key": key,
                               "detail": "与前面的行输入/配置/种子相同：确认重复生产请给出不同的 request_item_key"})
        else:
            seen[signature] = index
        expanded.append(entry)
    return {"expanded_count": len(expanded), "items": expanded, "duplicates": duplicates,
            "dedupe_note": "重复项仅提示，不会自动合并"}


def aggregate_status(item_statuses: list[str], *, paused: bool = False, cancelled: bool = False) -> str:
    """批次状态聚合：生产状态与发布状态分开（本函数只描述生产）。"""
    statuses = [status for status in item_statuses]
    if not statuses:
        return "DRAFT"
    if cancelled and all(status in ("CANCELLED", "SKIPPED") for status in statuses):
        return "CANCELLED"
    succeeded = sum(1 for status in statuses if status == "SUCCEEDED")
    failed = sum(1 for status in statuses if status in ("FAILED",))
    cancelled_count = sum(1 for status in statuses if status == "CANCELLED")
    active = sum(1 for status in statuses if status in ("RUNNING", "QUEUED"))
    pending = sum(1 for status in statuses if status == "PENDING")
    if succeeded == len(statuses):
        return "COMPLETED"
    if (failed or cancelled_count) and not active and not pending:
        return "PARTIAL_FAILED" if succeeded else "FAILED"
    if paused and not active:
        return "PAUSED"
    if active:
        return "RUNNING"
    if succeeded and (failed or cancelled_count or pending):
        return "PARTIAL_FAILED" if not pending else "RUNNING"
    return "QUEUED"


def summarize(items: list[dict]) -> dict:
    """批次聚合摘要：**总数来自服务端**，不从客户端分页推算。"""
    counts: dict[str, int] = {status: 0 for status in ITEM_STATUSES}
    for item in items:
        status = item.get("status") or "PENDING"
        counts[status] = counts.get(status, 0) + 1
    return {
        "total": len(items),
        "by_status": counts,
        "succeeded": counts.get("SUCCEEDED", 0),
        "failed": counts.get("FAILED", 0),
        "cancelled": counts.get("CANCELLED", 0),
        "pending": counts.get("PENDING", 0),
        "active": counts.get("RUNNING", 0) + counts.get("QUEUED", 0),
        "production_complete": counts.get("SUCCEEDED", 0) == len(items) and bool(items),
    }


def select_retry_targets(items: list[dict], *, item_filter: list[str] | None = None) -> list[dict]:
    """重试只针对失败项；已成功产物不会被重跑（可复用需输入/能力/许可完全相符）。"""
    targets = []
    for item in items:
        if item.get("status") not in ("FAILED", "CANCELLED"):
            continue
        if item_filter and item.get("id") not in item_filter and str(item.get("index")) not in item_filter:
            continue
        targets.append(item)
    return targets


def parse_batch_status(value: str) -> str:
    if value not in BATCH_STATUSES:
        raise ExpansionError("unknown_status", f"未知批次状态 {value}")
    return value


def normalize_publish_intent(intent: dict | None) -> dict:
    """发布意图只是意图：`auto_publish` 默认 false，且不跳过平台授权/审批。"""
    intent = dict(intent or {})
    return {
        "auto_publish": bool(intent.get("auto_publish", False)),
        "requested_visibility": intent.get("requested_visibility") or "private",
        "account_ids": list(intent.get("account_ids") or []),
        "note": "auto_publish=true 仅表达意图；仍需平台授权、有效审批与可见性确认，不自动公开",
    }


def item_result_kind(status: str) -> Literal["production", "publish"]:
    return "production"
