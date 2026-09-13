"""V6-07：Automation API（Key/scope/幂等/限流/外部调用面与越权）。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tests"))
sys.path.insert(0, str(PROJECT / "apps" / "api"))

import test_job_control as fixtures  # noqa: E402

from productdirector_api import automation  # noqa: E402

main = fixtures.main


class AutomationRuleTests(unittest.TestCase):
    def test_key_format_and_parse(self) -> None:
        generated = automation.generate_key()
        self.assertTrue(generated["plaintext"].startswith("pda_"))
        parsed = automation.parse_key(generated["plaintext"])
        self.assertIsNotNone(parsed)
        key_id, secret = parsed
        self.assertEqual(key_id, generated["key_id"])
        self.assertEqual(secret, generated["secret"])
        self.assertTrue(automation.secret_matches(secret, generated["secret_hash"]))
        self.assertFalse(automation.secret_matches("wrong", generated["secret_hash"]))
        self.assertIsNone(automation.parse_key("pda_short"))
        self.assertIsNone(automation.parse_key("token-abc"))
        self.assertIsNone(automation.parse_key(""))

    def test_key_hash_is_not_plaintext(self) -> None:
        generated = automation.generate_key()
        self.assertNotIn(generated["secret"], generated["secret_hash"])
        self.assertEqual(len(generated["secret_hash"]), 64)
        self.assertEqual(len(generated["fingerprint"]), 12)

    def test_route_scope_mapping(self) -> None:
        self.assertEqual(automation.required_scope("GET", "/api/v1/packages"), "packages:read")
        self.assertEqual(automation.required_scope("POST", "/api/v1/batches"), "generate:write")
        self.assertEqual(automation.required_scope("POST", "/api/v1/runs/abc/packages"), "publish:write")
        self.assertEqual(automation.required_scope("GET", "/api/v1/webhooks/12/deliveries"), "webhooks:manage")
        self.assertIsNone(automation.required_scope("POST", "/api/v1/settings"))
        self.assertEqual(automation.missing_scopes(["jobs:read"], "packages:read"), ["packages:read"])
        self.assertEqual(automation.missing_scopes(["packages:read"], "packages:read"), [])
        self.assertTrue(automation.owner_only("/api/v1/automation-keys"))
        self.assertFalse(automation.owner_only("/api/v1/packages"))

    def test_idempotency_required_routes(self) -> None:
        self.assertTrue(automation.idempotency_required("POST", "/api/v1/batches"))
        self.assertTrue(automation.idempotency_required("POST", "/api/v1/automation/generate"))
        self.assertFalse(automation.idempotency_required("POST", "/api/v1/batches/preview"))
        self.assertFalse(automation.idempotency_required("POST", "/api/v1/batches/abc/cancel"))

    def test_ip_allowlist(self) -> None:
        self.assertTrue(automation.ip_allowed("10.0.0.5", ""))
        self.assertTrue(automation.ip_allowed("10.0.0.5", "10.0.0.5, 127.0.0.1"))
        self.assertFalse(automation.ip_allowed("10.0.0.9", "10.0.0.5"))
        self.assertTrue(automation.ip_allowed("10.0.0.9", "10.0.0.*"))
        self.assertTrue(automation.ip_allowed("anything", "*"))
        self.assertFalse(automation.ip_allowed(None, "10.0.0.5"))

    def test_rate_limit_verdict_and_window(self) -> None:
        self.assertEqual(automation.window_start(125.7, 60), 120)
        allowed = automation.rate_limit_verdict(3, 60, 120, 130.0)
        self.assertTrue(allowed["allowed"])
        self.assertEqual(allowed["remaining"], 57)
        blocked = automation.rate_limit_verdict(61, 60, 120, 130.0)
        self.assertFalse(blocked["allowed"])
        self.assertEqual(blocked["remaining"], 0)
        self.assertEqual(blocked["reset_in"], 51)

    def test_request_fingerprint_is_canonical(self) -> None:
        self.assertEqual(automation.request_fingerprint({"a": 1, "b": [2, 3]}),
                         automation.request_fingerprint({"b": [2, 3], "a": 1}))
        self.assertNotEqual(automation.request_fingerprint({"a": 1}), automation.request_fingerprint({"a": 2}))

    def test_limits_document_is_labelled_product_setting(self) -> None:
        limits = automation.limits_document(key_limit=10)
        self.assertEqual(limits["requests_per_minute"]["per_key"], 10)
        self.assertEqual(limits["requests_per_minute"]["per_workspace"], 60)
        self.assertIn("不是平台", limits["note"])

    def test_ttl_validation(self) -> None:
        self.assertEqual(automation.ttl_seconds(7), 7 * 86400)
        self.assertIsNone(automation.ttl_seconds(None))
        with self.assertRaises(automation.AutomationError):
            automation.ttl_seconds(0)
        with self.assertRaises(automation.AutomationError):
            automation.ttl_seconds(automation.KEY_TTL_MAX_DAYS + 1)


class AutomationApiTests(unittest.TestCase):
    _create_asset = fixtures.JobControlAcceptanceTests._create_asset
    _create_plan = fixtures.JobControlAcceptanceTests._create_plan

    def setUp(self) -> None:
        fixtures.JobControlAcceptanceTests.setUp(self)
        self.asset = self._create_asset()
        self.plan = self._create_plan()
        self.client.post(f"/api/v1/plans/{self.plan['id']}/approve", json={"approved": True})

    def _create_key(self, scopes=None, **overrides) -> dict:
        body = {"name": "外部自动化", "scopes": scopes or ["projects:read", "jobs:read", "generate:write"],
                "rate_limit_per_minute": 60}
        body.update(overrides)
        response = self.client.post("/api/v1/automation-keys", json=body)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def _key_client(self, key: str):
        from fastapi.testclient import TestClient

        return TestClient(main.app, headers={"Authorization": f"Bearer {key}"})

    def _batch_body(self) -> dict:
        return {"name": "自动化批次",
                "items": [{"plan_id": self.plan["id"], "profile_id": "tiktok-mx-9x16-esmx"}],
                "max_concurrent": 1}

    def test_key_secret_shown_once_and_never_listed(self) -> None:
        created = self._create_key()
        self.assertTrue(created["key"].startswith("pda_"))
        self.assertEqual(created["key_id"], automation.parse_key(created["key"])[0])
        listed = self.client.get("/api/v1/automation-keys").json()
        entry = next(item for item in listed if item["id"] == created["id"])
        self.assertIsNone(entry["key"])
        self.assertEqual(entry["prefix"], created["prefix"])
        self.assertNotIn("secret", entry)
        self.assertNotIn("secret_hash", entry)

    def test_unknown_scope_and_empty_scope_rejected(self) -> None:
        empty = self.client.post("/api/v1/automation-keys", json={"name": "x", "scopes": []})
        self.assertEqual(empty.status_code, 422)
        self.assertEqual(empty.json()["detail"]["code"], "SCOPES_REQUIRED")
        bad = self.client.post("/api/v1/automation-keys", json={"name": "x", "scopes": ["root:all"]})
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(bad.json()["detail"]["code"], "UNKNOWN_SCOPE")

    def test_key_can_read_and_scope_blocks_write(self) -> None:
        created = self._create_key(scopes=["projects:read"])
        client = self._key_client(created["key"])
        allowed = client.get("/api/v1/platform-profiles")
        self.assertEqual(allowed.status_code, 200, allowed.text)
        blocked = client.post("/api/v1/batches", json=self._batch_body(),
                              headers={"Idempotency-Key": "k-1"})
        self.assertEqual(blocked.status_code, 403, blocked.text)
        detail = blocked.json()["detail"]
        self.assertEqual(detail["code"], "insufficient_scope")
        self.assertEqual(detail["required_scopes"], ["generate:write"])
        self.assertEqual(detail["granted_scopes"], ["projects:read"])

    def test_unregistered_route_is_denied(self) -> None:
        created = self._create_key(scopes=list(automation.SCOPES))
        client = self._key_client(created["key"])
        response = client.get("/api/v1/settings/provider")
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["detail"]["code"], "insufficient_scope")

    def test_automation_key_cannot_manage_keys(self) -> None:
        created = self._create_key(scopes=list(automation.SCOPES))
        client = self._key_client(created["key"])
        response = client.get("/api/v1/automation-keys")
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["detail"]["code"], "owner_session_required")

    def test_revoked_key_is_rejected(self) -> None:
        created = self._create_key()
        client = self._key_client(created["key"])
        self.assertEqual(client.get("/api/v1/packages").status_code, 403)  # 缺 packages:read
        revoked = self.client.request("DELETE", f"/api/v1/automation-keys/{created['id']}",
                                      json={"reason": "轮换"})
        self.assertEqual(revoked.status_code, 200, revoked.text)
        self.assertTrue(revoked.json()["revoked"])
        after = client.get("/api/v1/platform-profiles")
        self.assertEqual(after.status_code, 401, after.text)
        self.assertEqual(after.json()["detail"]["code"], "key_revoked")
        again = self.client.request("DELETE", f"/api/v1/automation-keys/{created['id']}", json={})
        self.assertEqual(again.status_code, 200)
        self.assertIn("幂等", again.json()["note"])

    def test_bad_key_and_unknown_key_id_rejected(self) -> None:
        client = self._key_client("pda_deadbeef_notarealsecret")
        self.assertEqual(client.get("/api/v1/platform-profiles").status_code, 401)
        client = self._key_client("not-a-key")
        self.assertEqual(client.get("/api/v1/platform-profiles").status_code, 401)
        created = self._create_key()
        tampered = created["key"].rsplit("_", 1)[0] + "_tampered"
        self.assertEqual(self._key_client(tampered).get("/api/v1/platform-profiles").status_code, 401)

    def test_owner_session_only_for_key_management(self) -> None:
        created = self._create_key()
        owner_list = self.client.get("/api/v1/automation-keys")
        self.assertEqual(owner_list.status_code, 200)
        self.assertTrue(any(item["id"] == created["id"] for item in owner_list.json()))

    def test_rate_limit_returns_429_with_headers(self) -> None:
        created = self._create_key(scopes=["projects:read"], rate_limit_per_minute=2)
        client = self._key_client(created["key"])
        first = client.get("/api/v1/platform-profiles")
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.headers["X-RateLimit-Limit"], "2")
        second = client.get("/api/v1/platform-profiles")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.headers["X-RateLimit-Remaining"], "0")
        third = client.get("/api/v1/platform-profiles")
        self.assertEqual(third.status_code, 429, third.text)
        detail = third.json()["detail"]
        self.assertEqual(detail["code"], "rate_limited")
        self.assertEqual(detail["limit_scope"], "key")
        self.assertGreaterEqual(detail["retry_after_seconds"], 1)
        self.assertIn("Retry-After", third.headers)

    def test_idempotency_required_for_automation_writes(self) -> None:
        created = self._create_key()
        client = self._key_client(created["key"])
        with patch("productdirector_api.main.advance_batch"):
            missing = client.post("/api/v1/automation/generate", json=self._batch_body())
        self.assertEqual(missing.status_code, 428, missing.text)
        self.assertEqual(missing.json()["detail"]["code"], "idempotency_key_required")

    def test_idempotent_replay_and_conflict(self) -> None:
        created = self._create_key()
        client = self._key_client(created["key"])
        body = self._batch_body()
        with patch("productdirector_api.main.advance_batch"):
            first = client.post("/api/v1/automation/generate", json=body, headers={"Idempotency-Key": "gen-1"})
            self.assertEqual(first.status_code, 202, first.text)
            replay = client.post("/api/v1/automation/generate", json=body, headers={"Idempotency-Key": "gen-1"})
            self.assertEqual(replay.status_code, 202, replay.text)
            self.assertEqual(replay.headers.get("Idempotency-Replayed"), "true")
            self.assertEqual(replay.json()["batch"]["id"], first.json()["batch"]["id"])
            conflict = client.post("/api/v1/automation/generate",
                                   json={**body, "name": "改了内容"},
                                   headers={"Idempotency-Key": "gen-1"})
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(conflict.json()["detail"]["code"], "idempotency_conflict")

    def test_automation_generate_status_split(self) -> None:
        created = self._create_key()
        client = self._key_client(created["key"])
        with patch("productdirector_api.main.advance_batch"):
            response = client.post("/api/v1/automation/generate",
                                   json={**self._batch_body(), "auto_publish": False},
                                   headers={"Idempotency-Key": "gen-split"})
        self.assertEqual(response.status_code, 202, response.text)
        body = response.json()
        batch_id = body["batch"]["id"]
        self.assertEqual(body["automation"]["actor"], f"key:{created['key_id']}")
        self.assertFalse(body["automation"]["auto_publish_requested"])
        aggregate = client.get(f"/api/v1/automation/batches/{batch_id}")
        self.assertEqual(aggregate.status_code, 200, aggregate.text)
        self.assertEqual(aggregate.json()["id"], batch_id)
        items = self.client.get(f"/api/v1/batches/{batch_id}/items").json()
        item_id = items["items"][0]["id"]
        job = client.get(f"/api/v1/automation/jobs/{item_id}")
        self.assertEqual(job.status_code, 200, job.text)
        payload = job.json()
        self.assertEqual(payload["kind"], "batch_item")
        self.assertIn("production", payload)
        self.assertIn("publish", payload)
        self.assertEqual(payload["publish"]["status"], "NOT_REQUESTED")
        self.assertIn("不等于已发布", payload["publish"]["note"])

    def test_auto_publish_requires_budget(self) -> None:
        created = self._create_key()
        client = self._key_client(created["key"])
        with patch("productdirector_api.main.advance_batch"):
            response = client.post("/api/v1/automation/generate",
                                   json={**self._batch_body(), "auto_publish": True},
                                   headers={"Idempotency-Key": "gen-ap"})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["detail"]["code"], "BUDGET_REQUIRED_FOR_AUTO_PUBLISH")

    def test_auto_publish_with_unlimited_budget_is_blocked(self) -> None:
        budget = self.client.post("/api/v1/budgets", json={"name": "无上限", "limit_amount": None}).json()
        created = self._create_key()
        client = self._key_client(created["key"])
        with patch("productdirector_api.main.advance_batch"):
            response = client.post("/api/v1/automation/generate",
                                   json={**self._batch_body(), "auto_publish": True, "budget_id": budget["id"]},
                                   headers={"Idempotency-Key": "gen-ap2"})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "no_limit")

    def test_auto_publish_records_intent_only(self) -> None:
        budget = self.client.post("/api/v1/budgets", json={"name": "有上限", "limit_amount": 50}).json()
        created = self._create_key()
        client = self._key_client(created["key"])
        with patch("productdirector_api.main.advance_batch"):
            response = client.post("/api/v1/automation/generate",
                                   json={**self._batch_body(), "auto_publish": True,
                                         "budget_id": budget["id"],
                                         "publish_intent": {"requested_visibility": "private"}},
                                   headers={"Idempotency-Key": "gen-ap3"})
        self.assertEqual(response.status_code, 202, response.text)
        body = response.json()
        self.assertTrue(body["automation"]["auto_publish_requested"])
        self.assertIn("不会自动公开", body["automation"]["note"])
        detail = client.get(f"/api/v1/automation/batches/{body['batch']['id']}").json()
        self.assertTrue(detail["publish_intent"]["auto_publish"])
        self.assertEqual(detail["publish_status"], "NOT_REQUESTED")

    def test_key_with_budget_limit_is_blocked_after_spend(self) -> None:
        created = self._create_key(scopes=["generate:write"], budget_limit_amount=0.0, budget_period="month")
        client = self._key_client(created["key"])
        spend = self.client.post("/api/v1/usage-events", json={
            "provider": "ffmpeg", "operation_id": "key-budget-op", "unit": "cpu_second", "quantity": 10,
        })
        self.assertEqual(spend.status_code, 201)
        with patch("productdirector_api.main.advance_batch"):
            blocked = client.post("/api/v1/automation/generate", json=self._batch_body(),
                                  headers={"Idempotency-Key": "gen-budget"})
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertEqual(blocked.json()["detail"]["code"], "key_budget_exhausted")

    def test_key_ip_allowlist_blocks_other_client(self) -> None:
        created = self._create_key(scopes=["projects:read"], ip_allowlist="203.0.113.9")
        client = self._key_client(created["key"])
        response = client.get("/api/v1/platform-profiles")
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["detail"]["code"], "ip_not_allowed")
        loopback = self._create_key(scopes=["projects:read"], ip_allowlist="127.0.0.1,testclient")
        self.assertEqual(self._key_client(loopback["key"]).get("/api/v1/platform-profiles").status_code, 200)

    def test_expired_key_is_rejected(self) -> None:
        created = self._create_key()
        with main.connect() as db:
            db.execute("UPDATE automation_keys SET expires_at = ? WHERE id = ?", (1.0, created["id"]))
        response = self._key_client(created["key"]).get("/api/v1/platform-profiles")
        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(response.json()["detail"]["code"], "key_expired")

    def test_invalid_ttl_rejected(self) -> None:
        response = self.client.post("/api/v1/automation-keys",
                                    json={"name": "x", "scopes": ["projects:read"], "expires_in_days": 0})
        self.assertEqual(response.status_code, 422)

    def test_openapi_surface_documents_scopes_and_limits(self) -> None:
        owner_view = self.client.get("/api/v1/automation/openapi")
        self.assertEqual(owner_view.status_code, 200, owner_view.text)
        body = owner_view.json()
        self.assertIn("generate:write", body["scopes"]["scopes"])
        self.assertTrue(body["scopes"]["requires_idempotency_key"])
        self.assertIn("428", body["error_codes"])
        self.assertIn("不是平台", body["limits"]["note"])
        created = self._create_key(scopes=["projects:read"], rate_limit_per_minute=17)
        key_view = self._key_client(created["key"]).get("/api/v1/automation/openapi")
        self.assertEqual(key_view.status_code, 200, key_view.text)
        self.assertEqual(key_view.json()["limits"]["requests_per_minute"]["per_key"], 17)

    def test_key_last_used_and_call_count_tracked(self) -> None:
        created = self._create_key(scopes=["projects:read"])
        before = next(item for item in self.client.get("/api/v1/automation-keys").json()
                      if item["id"] == created["id"])
        self.assertEqual(before["call_count"], 0)
        self.assertIsNone(before["last_used_at"])
        self._key_client(created["key"]).get("/api/v1/platform-profiles")
        after = next(item for item in self.client.get("/api/v1/automation-keys").json()
                     if item["id"] == created["id"])
        self.assertEqual(after["call_count"], 1)
        self.assertIsNotNone(after["last_used_at"])


if __name__ == "__main__":
    unittest.main()
