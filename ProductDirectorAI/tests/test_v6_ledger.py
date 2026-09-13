"""V6-06：资源与成本账本（用量去重、预算预留/对账、四类金额、无报价不按 0）。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tests"))
sys.path.insert(0, str(PROJECT / "apps" / "api"))

import test_job_control as fixtures  # noqa: E402

from productdirector_api import ledger  # noqa: E402

main = fixtures.main


class LedgerRuleTests(unittest.TestCase):
    def test_dedupe_key_is_stable_per_provider_operation_item_event(self) -> None:
        first = ledger.usage_event_key(provider="h3-comfyui", operation_id="op-1",
                                      billing_item="video_generation_request", event_id="evt-1")
        again = ledger.usage_event_key(provider="h3-comfyui", operation_id="op-1",
                                      billing_item="video_generation_request", event_id="evt-1")
        other_event = ledger.usage_event_key(provider="h3-comfyui", operation_id="op-1",
                                             billing_item="video_generation_request", event_id="evt-2")
        self.assertEqual(first, again)
        self.assertNotEqual(first, other_event)

    def test_unknown_price_never_assumed_zero(self) -> None:
        price = ledger.price_for_event("some-paid-provider", "video_generation_request", 3.0)
        self.assertFalse(price["priced"])
        self.assertIsNone(price["amount"])
        self.assertIn("不得假设 0", price["reason"])

    def test_internal_rate_card_is_labelled_estimate(self) -> None:
        card = ledger.internal_rate_card()
        self.assertEqual(card["kind"], "internal_estimate")
        self.assertIn("不代表真实云账单", card["note"])
        price = ledger.price_for_event("productdirector.blender", "gpu_second", 1000.0)
        self.assertTrue(price["priced"])
        self.assertEqual(price["kind"], "internal_estimate")
        self.assertAlmostEqual(price["amount"], 0.12, places=6)

    def test_explicit_price_overrides_rate_card(self) -> None:
        price = ledger.price_for_event("productdirector.blender", "gpu_second", 10.0, explicit_price=1.23)
        self.assertEqual(price["source"], "explicit")
        self.assertAlmostEqual(price["amount"], 1.23, places=6)

    def test_reserved_is_not_added_into_actual(self) -> None:
        entries = [
            {"kind": "settled", "amount": 10.0},
            {"kind": "reserved", "amount": 5.0},
            {"kind": "accrued", "amount": 2.0},
            {"kind": "estimate", "amount": 99.0},
        ]
        snapshot = ledger.budget_snapshot(entries, 30.0)
        self.assertEqual(snapshot["settled"], 10.0)
        self.assertEqual(snapshot["reserved_outstanding"], 5.0)
        self.assertEqual(snapshot["accrued_unreserved"], 2.0)
        self.assertEqual(snapshot["committed"], 17.0)
        self.assertEqual(snapshot["available"], 13.0)
        self.assertNotIn("actual", snapshot["totals"])
        self.assertIn("available = limit", snapshot["formula"])

    def test_unlimited_budget_blocks_paid_route(self) -> None:
        check = ledger.check_budget({"limit_amount": None}, [], 5.0)
        self.assertFalse(check["allowed"])
        self.assertEqual(check["code"], "no_limit")
        missing = ledger.check_budget(None, [], 5.0)
        self.assertEqual(missing["code"], "no_budget")

    def test_budget_exhaustion_blocks_submission(self) -> None:
        entries = [{"kind": "settled", "amount": 9.0}]
        blocked = ledger.check_budget({"limit_amount": 10.0}, entries, 2.0)
        self.assertFalse(blocked["allowed"])
        self.assertEqual(blocked["code"], "budget_exhausted")
        allowed = ledger.check_budget({"limit_amount": 10.0}, entries, 1.0)
        self.assertTrue(allowed["allowed"])

    def test_settle_reports_overrun_and_release(self) -> None:
        over = ledger.settle_reservation(reserved_amount=5.0, actual_amount=7.5)
        self.assertTrue(over["overrun"])
        self.assertAlmostEqual(over["additional_accrual"], 2.5, places=6)
        self.assertAlmostEqual(over["release"], 0.0, places=6)
        under = ledger.settle_reservation(reserved_amount=5.0, actual_amount=3.0)
        self.assertFalse(under["overrun"])
        self.assertAlmostEqual(under["release"], 2.0, places=6)

    def test_resource_summary_aggregates_server_side(self) -> None:
        summary = ledger.resource_summary([
            {"provider": "ffmpeg", "unit": "cpu_second", "quantity": 3.0},
            {"provider": "ffmpeg", "unit": "cpu_second", "quantity": 1.5},
            {"provider": "espeak-ng", "unit": "tts_character", "quantity": 120.0},
        ])
        self.assertEqual(summary["event_count"], 3)
        self.assertEqual(summary["by_provider"]["ffmpeg"]["quantity"], 4.5)
        self.assertTrue(summary["by_provider"]["ffmpeg"]["internal_estimate"])
        self.assertEqual(summary["by_unit"]["tts_character"], 120.0)

    def test_unknown_kind_and_currency_rejected(self) -> None:
        with self.assertRaises(ledger.LedgerError) as ctx:
            ledger.ledger_entry(kind="actual", amount=1.0, currency="USD")
        self.assertEqual(ctx.exception.code, "unknown_kind")
        with self.assertRaises(ledger.LedgerError) as ctx:
            ledger.ledger_entry(kind="settled", amount=1.0, currency="EUR")
        self.assertEqual(ctx.exception.code, "unknown_currency")


class LedgerApiTests(unittest.TestCase):
    def setUp(self) -> None:
        fixtures.JobControlAcceptanceTests.setUp(self)

    def _budget(self, limit: float | None = 100.0, name: str = "V6 测试预算") -> dict:
        response = self.client.post("/api/v1/budgets",
                                   json={"name": name, "currency": "USD", "limit_amount": limit})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_budget_without_limit_cannot_reserve(self) -> None:
        budget = self._budget(limit=None)
        self.assertIsNone(budget["available"])
        blocked = self.client.post(f"/api/v1/budgets/{budget['id']}/reserve",
                                   json={"estimate_upper_bound": 1.0})
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertEqual(blocked.json()["detail"]["code"], "no_limit")

    def test_budget_currency_whitelist(self) -> None:
        response = self.client.post("/api/v1/budgets", json={"name": "x", "currency": "EUR"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "UNKNOWN_CURRENCY")

    def test_usage_event_dedupe_and_pricing(self) -> None:
        payload = {
            "provider": "h3-comfyui", "operation_id": "op-dedupe", "unit": "video_generation_request",
            "quantity": 2.0, "capability": "video_generation",
        }
        first = self.client.post("/api/v1/usage-events", json=payload)
        self.assertEqual(first.status_code, 201, first.text)
        body = first.json()
        self.assertFalse(body["deduplicated"])
        self.assertTrue(body["priced"])
        self.assertTrue(body["internal_estimate"])
        self.assertEqual(body["amount_source"], "internal_rate_card")
        self.assertAlmostEqual(body["amount"], 0.04, places=6)
        replay = self.client.post("/api/v1/usage-events", json=payload)
        self.assertEqual(replay.status_code, 201)
        self.assertTrue(replay.json()["deduplicated"])
        self.assertEqual(replay.json()["id"], body["id"])
        listed = self.client.get("/api/v1/usage", params={"provider": "h3-comfyui"})
        self.assertEqual(listed.status_code, 200, listed.text)
        ids = [item["id"] for item in listed.json()["events"]]
        self.assertEqual(ids.count(body["id"]), 1)

    def test_unpriced_provider_is_recorded_but_flagged(self) -> None:
        response = self.client.post("/api/v1/usage-events", json={
            "provider": "paid-video-api", "operation_id": "op-unknown", "unit": "video_generation_request",
            "quantity": 1.0,
        })
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertFalse(body["priced"])
        self.assertIsNone(body["amount"])
        self.assertIsNone(body["ledger_entry_id"])
        self.assertIn("不得假设 0", body["unpriced_reason"])
        costs = self.client.get("/api/v1/costs").json()
        self.assertEqual(costs["by_kind"]["accrued"], 0.0)

    def test_usage_unknown_unit_rejected(self) -> None:
        response = self.client.post("/api/v1/usage-events", json={
            "provider": "ffmpeg", "operation_id": "op-unit", "unit": "mystery_unit", "quantity": 1.0,
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "UNKNOWN_UNIT")

    def test_reserve_settle_flow_updates_snapshot(self) -> None:
        budget = self._budget(limit=10.0)
        reserve = self.client.post(f"/api/v1/budgets/{budget['id']}/reserve",
                                   json={"estimate_upper_bound": 4.0, "note": "提交前预留"})
        self.assertEqual(reserve.status_code, 200, reserve.text)
        reservation_id = reserve.json()["reservation_id"]
        self.assertEqual(reserve.json()["snapshot"]["reserved_outstanding"], 4.0)
        self.assertEqual(reserve.json()["snapshot"]["available"], 6.0)
        # 预留期间可用额度下降，超出的预留必须被阻断
        blocked = self.client.post(f"/api/v1/budgets/{budget['id']}/reserve",
                                   json={"estimate_upper_bound": 7.0})
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertEqual(blocked.json()["detail"]["code"], "budget_exhausted")
        settled = self.client.post(f"/api/v1/budgets/{budget['id']}/settle",
                                   json={"reservation_id": reservation_id, "actual_amount": 5.5})
        self.assertEqual(settled.status_code, 200, settled.text)
        body = settled.json()
        self.assertTrue(body["overrun"])
        self.assertAlmostEqual(body["additional_accrual"], 1.5, places=6)
        snapshot = body["snapshot"]
        self.assertEqual(snapshot["reserved_outstanding"], 0.0)
        self.assertEqual(snapshot["settled"], 5.5)
        self.assertEqual(snapshot["available"], 4.5)
        again = self.client.post(f"/api/v1/budgets/{budget['id']}/settle",
                                 json={"reservation_id": reservation_id, "actual_amount": 1.0})
        self.assertEqual(again.status_code, 409)
        self.assertEqual(again.json()["detail"]["code"], "reservation_settled")
        self.assertEqual(again.json()["detail"]["settled_entry_id"], body["settled_entry_id"])
        # 重复结算被拒后金额不再变化：不会双计
        costs = self.client.get("/api/v1/costs", params={"budget_id": budget["id"]}).json()
        self.assertEqual(costs["budgets"][0]["settled"], 5.5)
        reservation_entry = next(item for item in costs["entries"] if item["id"] == reservation_id)
        self.assertIsNotNone(reservation_entry["settled_at"])
        self.assertEqual(reservation_entry["settled_entry_id"], body["settled_entry_id"])

    def test_settle_under_reservation_releases_remainder(self) -> None:
        budget = self._budget(limit=10.0)
        reserve = self.client.post(f"/api/v1/budgets/{budget['id']}/reserve",
                                   json={"estimate_upper_bound": 6.0}).json()
        settled = self.client.post(f"/api/v1/budgets/{budget['id']}/settle",
                                   json={"reservation_id": reserve["reservation_id"], "actual_amount": 2.0})
        body = settled.json()
        self.assertFalse(body["overrun"])
        self.assertAlmostEqual(body["release"], 4.0, places=6)
        self.assertEqual(body["snapshot"]["available"], 8.0)

    def test_reservation_not_found(self) -> None:
        budget = self._budget()
        response = self.client.post(f"/api/v1/budgets/{budget['id']}/settle",
                                    json={"reservation_id": "00000000-0000-0000-0000-000000000000",
                                          "actual_amount": 1.0})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "reservation_not_found")

    def test_budget_update_requires_current_revision(self) -> None:
        budget = self._budget(limit=5.0)
        stale = self.client.patch(f"/api/v1/budgets/{budget['id']}",
                                  json={"revision": 99, "limit_amount": 20.0})
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["detail"]["code"], "revision_conflict")
        ok = self.client.patch(f"/api/v1/budgets/{budget['id']}",
                               json={"revision": budget["revision"], "limit_amount": 20.0,
                                     "reason": "追加预算"})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(ok.json()["revision"], budget["revision"] + 1)
        self.assertEqual(ok.json()["limit_amount"], 20.0)
        self.assertEqual(ok.json()["previous_limit_amount"], 5.0)
        self.assertIn("不追溯改写历史", ok.json()["note"])

    def test_usage_records_attach_to_existing_budget(self) -> None:
        budget = self._budget(limit=50.0)
        event = self.client.post("/api/v1/usage-events", json={
            "provider": "ffmpeg", "operation_id": "op-attach", "unit": "cpu_second", "quantity": 250.0,
        }).json()
        self.assertEqual(event["budget_id"], budget["id"])
        costs = self.client.get("/api/v1/costs").json()
        self.assertEqual(len(costs["budgets"]), 1)
        self.assertEqual(costs["budgets"][0]["accrued_unreserved"], 0.001)
        self.assertEqual(costs["budgets"][0]["available"], 49.999)
        self.assertEqual(costs["by_kind"]["accrued"], 0.001)

    def test_rate_card_endpoint_is_internal_estimate(self) -> None:
        card = self.client.get("/api/v1/rate-card")
        self.assertEqual(card.status_code, 200, card.text)
        body = card.json()
        self.assertEqual(body["kind"], "internal_estimate")
        self.assertIn("productdirector.blender", body["rates"])

    def test_costs_filter_by_run_and_missing_budget(self) -> None:
        response = self.client.get("/api/v1/costs", params={"budget_id": "does-not-exist"})
        self.assertEqual(response.status_code, 404)
        empty = self.client.get("/api/v1/costs").json()
        self.assertEqual(empty["budgets"], [])
        self.assertEqual(empty["entries"], [])
        self.assertIn("不与 actual 相加", empty["note"])

    def test_internal_usage_helper_is_idempotent(self) -> None:
        first = main.record_internal_usage(provider="ffmpeg", unit="cpu_second", quantity=12.0,
                                          operation_id="helper-op", capability="audio_mix",
                                          note="测试登记")
        self.assertIsNotNone(first)
        self.assertFalse(first["deduplicated"])
        second = main.record_internal_usage(provider="ffmpeg", unit="cpu_second", quantity=12.0,
                                            operation_id="helper-op", capability="audio_mix",
                                            note="测试登记")
        self.assertTrue(second["deduplicated"])
        self.assertEqual(first["id"], second["id"])
        self.assertIsNone(main.record_internal_usage(provider="ffmpeg", unit="cpu_second", quantity=0.0,
                                                     operation_id="helper-zero", capability="audio_mix"))


if __name__ == "__main__":
    unittest.main()
