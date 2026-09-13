"""V6-06 资源与成本账本：用量事件去重、预算预留/对账、四类金额与可用额度。

诚实边界（对应主规划 12.8）：
- 金额分四类：`estimate`（估算）、`reserved`（预留）、`accrued`（已发生未对账）、`settled`（已确认实际）。
  可用额度 = 限额 − settled − 未结预留 − 未预留的已发生额；**UI 不能把 reserved 与 actual 相加造成双计**。
- 每个用量事件以 (provider, operation_id, billing_item, event_id) 去重：回调重放不重复计费。
- 无已知报价时**不得假设 0**：要么管理员给出明确上限，要么阻断该付费路线。
- 预估不是强制封顶：上游实际账单可能更高；系统遇到超支立即停止后续提交并如实报告差异。
- 自管 GPU 的内部成本率**标注为内部估算**；本系统不做真实云资源采购与客户扣款。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Literal

LEDGER_KINDS = ("estimate", "reserved", "accrued", "settled")
RESOURCE_UNITS = (
    "gpu_second", "cpu_second", "storage_byte_day", "network_byte",
    "llm_token", "tts_character", "tts_second", "video_generation_request", "music_license_use",
)
INTERNAL_PROVIDERS = {"productdirector.blender", "h3-comfyui", "espeak-ng", "ffmpeg"}
CURRENCIES = ("USD", "MXN", "CNY")


class LedgerError(ValueError):
    def __init__(self, code: str, message: str, detail: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}

    def as_detail(self) -> dict:
        return {"code": self.code, "detail": self.message, **self.detail}


def canonical(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def usage_event_key(*, provider: str, operation_id: str, billing_item: str, event_id: str) -> str:
    """用量事件去重键：同一 Provider 的同一次操作不会被重复计费。"""
    return hashlib.sha256(f"{provider}|{operation_id}|{billing_item}|{event_id}".encode("utf-8")).hexdigest()


def budget_snapshot(entries: list[dict], limit_amount: float | None) -> dict:
    """按四类金额汇总并给出可用额度（服务端计算，不从分页行推算）。"""
    totals = {kind: 0.0 for kind in LEDGER_KINDS}
    for entry in entries:
        kind = entry.get("kind")
        if kind in totals:
            totals[kind] += float(entry.get("amount") or 0.0)
    settled = totals["settled"]
    reserved_outstanding = totals["reserved"]
    accrued_unreserved = totals["accrued"]
    committed = settled + reserved_outstanding + accrued_unreserved
    available = None if limit_amount is None else round(float(limit_amount) - committed, 6)
    return {
        "totals": {kind: round(value, 6) for kind, value in totals.items()},
        "settled": round(settled, 6),
        "reserved_outstanding": round(reserved_outstanding, 6),
        "accrued_unreserved": round(accrued_unreserved, 6),
        "committed": round(committed, 6),
        "limit_amount": limit_amount,
        "available": available,
        "formula": "available = limit − settled − reserved_outstanding − accrued_unreserved",
        "note": "reserved 与 settled 不相加进 actual；显示口径见 formula",
    }


def check_budget(budget: dict | None, entries: list[dict], estimate_upper_bound: float) -> dict:
    """提交前预算检查：无预算账户或无已知报价时如实阻断，不假设 0。"""
    if budget is None:
        return {"allowed": False, "code": "no_budget", "reason": "没有预算账户：付费路线必须先建立预算"}
    snapshot = budget_snapshot(entries, budget.get("limit_amount"))
    if budget.get("limit_amount") is None:
        return {"allowed": False, "code": "no_limit", "reason": "预算未设置上限：无已知报价时不得按 0 估算",
                "snapshot": snapshot}
    if snapshot["available"] is None or snapshot["available"] < estimate_upper_bound:
        return {"allowed": False, "code": "budget_exhausted",
                "reason": (f"可用额度 {snapshot['available']} 小于本次预估上界 {estimate_upper_bound}"
                           if snapshot["available"] is not None else "预算未设置上限"),
                "snapshot": snapshot}
    return {"allowed": True, "snapshot": snapshot, "reserve_amount": estimate_upper_bound}


def internal_rate_card() -> dict:
    """自管资源的内部成本率（标注为内部估算，不是真实账单）。"""
    return {
        "kind": "internal_estimate",
        "note": "自管 GPU/CPU 的内部成本率仅用于估算，不代表真实云账单；本系统不做资源采购",
        "rates": {
            "productdirector.blender": {"unit": "gpu_second", "amount_per_unit": 0.00012, "currency": "USD"},
            "h3-comfyui": {"unit": "video_generation_request", "amount_per_unit": 0.02, "currency": "USD"},
            "espeak-ng": {"unit": "tts_character", "amount_per_unit": 0.000002, "currency": "USD"},
            "ffmpeg": {"unit": "cpu_second", "amount_per_unit": 0.000004, "currency": "USD"},
        },
    }


def price_for_event(provider: str, unit: str, quantity: float, rate_card: dict | None = None,
                    explicit_price: float | None = None) -> dict:
    """给用量事件定价：显式价格优先；自管资源用内部估算；外部付费且无价格 → 阻断。"""
    if explicit_price is not None:
        return {"priced": True, "amount": round(float(explicit_price), 6), "source": "explicit",
                "kind": "internal_estimate" if provider in INTERNAL_PROVIDERS else "provider_quote"}
    card = (rate_card or internal_rate_card())["rates"].get(provider)
    if card and card.get("unit") == unit:
        return {"priced": True, "amount": round(card["amount_per_unit"] * float(quantity), 6),
                "source": "internal_rate_card", "kind": "internal_estimate", "currency": card["currency"]}
    return {"priced": False, "amount": None, "source": "unknown",
            "reason": f"没有 {provider}/{unit} 的已知报价：不得假设 0，需管理员给出上限或阻断付费路线"}


def resource_summary(events: list[dict]) -> dict:
    """按 Provider/单位/能力聚合（服务端聚合，UI 不再自行相加）。"""
    by_provider: dict[str, dict] = {}
    by_unit: dict[str, float] = {}
    for event in events:
        provider = str(event.get("provider") or "unknown")
        unit = str(event.get("unit") or "unknown")
        quantity = float(event.get("quantity") or 0.0)
        bucket = by_provider.setdefault(provider, {"unit": unit, "quantity": 0.0, "events": 0,
                                                  "internal_estimate": provider in INTERNAL_PROVIDERS})
        bucket["quantity"] = round(bucket["quantity"] + quantity, 6)
        bucket["events"] += 1
        by_unit[unit] = round(by_unit.get(unit, 0.0) + quantity, 6)
    return {"event_count": len(events), "by_provider": by_provider, "by_unit": by_unit,
            "units_supported": list(RESOURCE_UNITS),
            "note": "自管资源按内部成本率估算，标注 internal_estimate；失败任务同样计入消耗"}


def ledger_entry(*, kind: str, amount: float, currency: str, refs: dict | None = None, note: str = "") -> dict:
    if kind not in LEDGER_KINDS:
        raise LedgerError("unknown_kind", f"未知账本类型 {kind}（可用 {LEDGER_KINDS}）")
    if currency not in CURRENCIES:
        raise LedgerError("unknown_currency", f"不支持的币种 {currency}")
    return {"kind": kind, "amount": round(float(amount), 6), "currency": currency,
            "refs": refs or {}, "note": note}


def settle_reservation(*, reserved_amount: float, actual_amount: float) -> dict:
    """对账：原子消耗/释放预留；取消不等于无费用（已提交调用仍需对账）。"""
    difference = round(float(actual_amount) - float(reserved_amount), 6)
    return {
        "reserved_amount": round(float(reserved_amount), 6),
        "actual_amount": round(float(actual_amount), 6),
        "release": round(max(0.0, -difference), 6),
        "additional_accrual": round(max(0.0, difference), 6),
        "overrun": difference > 0,
        "note": "实际高于预留时记入 additional_accrual 并如实报告差异（预估不是强制封顶）",
    }
