"""V6-08：事务性 Outbox 与签名 Webhook（签名/容差/重试/死信/重放/脱敏/轮换）。"""
from __future__ import annotations

import json
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tests"))
sys.path.insert(0, str(PROJECT / "apps" / "api"))

import test_job_control as fixtures  # noqa: E402

from productdirector_api import webhooks  # noqa: E402

main = fixtures.main


class Receiver:
    """本机接收端：记录收到的请求，按脚本返回状态码（用于验证重试与死信）。"""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.status_plan: list[int] = []
        self.lock = threading.Lock()
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length)
                # HTTP 头大小写不敏感：接收端按框架常规做大小写无关读取
                headers = {key.lower(): value for key, value in self.headers.items()}
                with receiver.lock:
                    receiver.requests.append({"headers": headers, "body": body})
                    status = receiver.status_plan.pop(0) if receiver.status_plan else 200
                self.send_response(status)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"ok" if status < 300 else b"boom token=abcdef1234567890")

            def log_message(self, *args):  # 静默
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/hook"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def bodies(self) -> list[dict]:
        with self.lock:
            return [json.loads(item["body"]) for item in self.requests]

    def header(self, index: int, name: str) -> str:
        with self.lock:
            return self.requests[index]["headers"][name.lower()]


class WebhookRuleTests(unittest.TestCase):
    def test_event_catalog_covers_plan_events(self) -> None:
        catalog = webhooks.catalog()
        for event in ("batch.started", "batch.completed", "item.completed", "item.failed",
                      "review.required", "package.ready", "publish.succeeded", "publish.failed",
                      "budget.blocked"):
            self.assertIn(event, catalog["event_types"])
        self.assertEqual(catalog["signature_scheme"], "HMAC-SHA256(secret, X-PDA-Timestamp + '.' + raw_body)")
        self.assertEqual(catalog["retry_schedule_seconds"], [60, 300, 900, 3600, 21600, 86400])
        self.assertEqual(catalog["max_attempts"], 7)
        self.assertFalse(catalog["note"].startswith("平台要求"))

    def test_envelope_marks_at_least_once_and_carries_revision(self) -> None:
        envelope = webhooks.event_envelope(
            event_id="e1", event_type="item.completed", aggregate_type="batch_item", aggregate_id="i1",
            aggregate_revision=7, payload={"status": "SUCCEEDED"}, occurred_at="2026-09-13T00:00:00Z",
        )
        self.assertEqual(envelope["schema_version"], webhooks.SCHEMA_VERSION)
        self.assertEqual(envelope["aggregate"]["revision"], 7)
        self.assertEqual(envelope["delivery"]["semantics"], "at_least_once")
        self.assertEqual(envelope["delivery"]["dedupe_by"], "event_id")
        self.assertFalse(envelope["delivery"]["ordered"])

    def test_envelope_rejects_secrets_and_media(self) -> None:
        with self.assertRaises(webhooks.WebhookError) as ctx:
            webhooks.event_envelope(event_id="e2", event_type="item.completed", aggregate_type="x", aggregate_id="y",
                                    aggregate_revision=1,
                                    payload={"access_token": "abcdefghijklmnop"}, occurred_at="now")
        self.assertEqual(ctx.exception.code, "payload_contains_secrets")
        problems = webhooks.check_payload_secrets({"note": "data:video/mp4;base64,AAAA"})
        self.assertTrue(anything.startswith("负载包含禁止内容") for anything in problems)

    def test_signature_and_tolerance(self) -> None:
        body = b'{"hello":"world"}'
        signature = webhooks.signing_key("s3cret", "1000", body)
        self.assertTrue(webhooks.verify_signature(secret="s3cret", timestamp="1000", raw_body=body,
                                                  signature=signature))
        self.assertFalse(webhooks.verify_signature(secret="other", timestamp="1000", raw_body=body,
                                                   signature=signature))
        self.assertFalse(webhooks.verify_signature(secret="s3cret", timestamp="1001", raw_body=body,
                                                   signature=signature))
        self.assertTrue(webhooks.timestamp_fresh("1000", now=1200))
        self.assertFalse(webhooks.timestamp_fresh("1000", now=1400))
        self.assertFalse(webhooks.timestamp_fresh("not-a-number", now=1000))

    def test_verify_delivery_supports_rotation_dual_keys(self) -> None:
        body = b'{"a":1}'
        timestamp = str(int(time.time()))
        old_signature = webhooks.signing_key("old-secret", timestamp, body)
        result = webhooks.verify_delivery(secrets=["new-secret", "old-secret"], timestamp=timestamp,
                                          raw_body=body, signature=old_signature)
        self.assertTrue(result["valid"])
        self.assertEqual(result["key_index"], 1)
        expired = webhooks.verify_delivery(secrets=["old-secret"], timestamp="1", raw_body=body,
                                           signature=webhooks.signing_key("old-secret", "1", body))
        self.assertEqual(expired["reason"], "timestamp_out_of_tolerance")

    def test_response_classification_and_retry_plan(self) -> None:
        self.assertEqual(webhooks.classify_response(200)["outcome"], "delivered")
        self.assertEqual(webhooks.classify_response(500)["outcome"], "retry")
        self.assertEqual(webhooks.classify_response(429)["outcome"], "retry")
        self.assertEqual(webhooks.classify_response(408)["outcome"], "retry")
        self.assertEqual(webhooks.classify_response(400)["outcome"], "dead")
        self.assertEqual(webhooks.classify_response(403)["outcome"], "dead")
        self.assertEqual(webhooks.classify_response(None, "timeout")["outcome"], "retry")
        self.assertEqual(webhooks.retry_delay_seconds(1), 60)
        self.assertEqual(webhooks.retry_delay_seconds(6), 86400)
        self.assertIsNone(webhooks.retry_delay_seconds(7))
        self.assertEqual(webhooks.MAX_ATTEMPTS, 7)
        plan = webhooks.next_attempt_plan(1, webhooks.classify_response(503))
        self.assertEqual(plan["action"], "retry")
        self.assertEqual(plan["next_retry_in"], 60)
        final = webhooks.next_attempt_plan(7, webhooks.classify_response(503))
        self.assertTrue(final["dead_letter"])
        self.assertIn("最大投递次数", final["reason"])

    def test_url_validation(self) -> None:
        self.assertEqual(webhooks.validate_url("https://example.com/hook"), "https://example.com/hook")
        self.assertEqual(webhooks.validate_url("http://127.0.0.1:9000/hook"), "http://127.0.0.1:9000/hook")
        with self.assertRaises(webhooks.WebhookError) as ctx:
            webhooks.validate_url("http://evil.example.com/hook")
        self.assertEqual(ctx.exception.code, "insecure_url")
        with self.assertRaises(webhooks.WebhookError):
            webhooks.validate_url("")

    def test_event_type_normalization(self) -> None:
        self.assertEqual(webhooks.normalize_event_types(["batch.completed", "batch.completed"]),
                         ["batch.completed"])
        with self.assertRaises(webhooks.WebhookError) as ctx:
            webhooks.normalize_event_types([])
        self.assertEqual(ctx.exception.code, "event_types_required")
        with self.assertRaises(webhooks.WebhookError) as ctx:
            webhooks.normalize_event_types(["nope.event"])
        self.assertEqual(ctx.exception.code, "unknown_event_type")

    def test_redaction_and_secret_masking(self) -> None:
        text = "error: api_key=abcdef1234567890 and bearer abcdefghijklmnopqrst"
        cleaned = webhooks.redact_response(text)
        self.assertNotIn("abcdef1234567890", cleaned)
        self.assertIn("[redacted]", cleaned)
        self.assertEqual(webhooks.mask_secret("supersecretvalue")[-4:], "alue")
        self.assertEqual(webhooks.mask_secret(""), "")


class WebhookApiTests(unittest.TestCase):
    _create_asset = fixtures.JobControlAcceptanceTests._create_asset
    _create_plan = fixtures.JobControlAcceptanceTests._create_plan

    def setUp(self) -> None:
        fixtures.JobControlAcceptanceTests.setUp(self)
        self.receiver = Receiver()
        self.asset = self._create_asset()
        self.plan = self._create_plan()
        self.client.post(f"/api/v1/plans/{self.plan['id']}/approve", json={"approved": True})

    def tearDown(self) -> None:
        self.receiver.stop()
        fixtures.JobControlAcceptanceTests.tearDown(self)

    def _endpoint(self, event_types=None, **overrides) -> dict:
        body = {"name": "本机接收端", "url": self.receiver.url,
                "event_types": event_types or ["item.completed", "batch.completed", "webhook.test"]}
        body.update(overrides)
        response = self.client.post("/api/v1/webhooks", json=body)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def _batch(self, items_extra=None) -> dict:
        items = [{"plan_id": self.plan["id"], "profile_id": "tiktok-mx-9x16-esmx"}]
        body = {"name": "Webhook 批次", "items": items, "max_concurrent": 1}
        with patch("productdirector_api.main.advance_batch"):
            response = self.client.post("/api/v1/batches", json=body)
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()["batch"]

    def test_create_returns_secret_once_and_hides_it(self) -> None:
        created = self._endpoint()
        self.assertTrue(created["secret"])
        self.assertEqual(created["secret_current_hint"][-4:], created["secret"][-4:])
        listed = self.client.get("/api/v1/webhooks").json()
        entry = next(item for item in listed if item["id"] == created["id"])
        self.assertIsNone(entry["secret"])
        self.assertNotIn(created["secret"], json.dumps(entry))
        self.assertEqual(entry["delivery_policy"]["max_attempts"], 7)

    def test_insecure_and_unknown_event_rejected(self) -> None:
        bad_url = self.client.post("/api/v1/webhooks", json={"name": "x", "url": "http://evil.example.com/h",
                                                             "event_types": ["item.completed"]})
        self.assertEqual(bad_url.status_code, 422)
        self.assertEqual(bad_url.json()["detail"]["code"], "insecure_url")
        bad_event = self.client.post("/api/v1/webhooks", json={"name": "x", "url": self.receiver.url,
                                                               "event_types": ["nope"]})
        self.assertEqual(bad_event.status_code, 422)
        self.assertEqual(bad_event.json()["detail"]["code"], "unknown_event_type")

    def test_test_delivery_is_signed_and_verifiable(self) -> None:
        created = self._endpoint()
        response = self.client.post(f"/api/v1/webhooks/{created['id']}/test")
        self.assertEqual(response.status_code, 200, response.text)
        delivery = response.json()["delivery"]
        self.assertEqual(delivery["status"], "DELIVERED")
        received = self.receiver.requests[-1]
        timestamp = self.receiver.header(-1, webhooks.TIMESTAMP_HEADER)
        signature = self.receiver.header(-1, webhooks.SIGNATURE_HEADER)
        verified = webhooks.verify_delivery(secrets=[created["secret"]], timestamp=timestamp,
                                            raw_body=received["body"], signature=signature)
        self.assertTrue(verified["valid"], verified)
        envelope = json.loads(received["body"])
        self.assertEqual(envelope["event_type"], "webhook.test")
        self.assertEqual(envelope["delivery"]["semantics"], "at_least_once")
        self.assertEqual(self.receiver.header(-1, webhooks.EVENT_ID_HEADER), envelope["event_id"])

    def test_wrong_signature_and_stale_timestamp_fail_verification(self) -> None:
        self._endpoint()
        self.client.post("/api/v1/webhooks", json={"name": "x", "url": self.receiver.url,
                                                   "event_types": ["webhook.test"]})
        target = self.client.get("/api/v1/webhooks").json()[0]
        self.client.post(f"/api/v1/webhooks/{target['id']}/test")
        received = self.receiver.requests[-1]
        tampered = webhooks.verify_delivery(secrets=["wrong-secret"],
                                           timestamp=self.receiver.header(-1, webhooks.TIMESTAMP_HEADER),
                                           raw_body=received["body"],
                                           signature=self.receiver.header(-1, webhooks.SIGNATURE_HEADER))
        self.assertFalse(tampered["valid"])
        self.assertEqual(tampered["reason"], "signature_mismatch")
        stale = webhooks.verify_delivery(secrets=[target["secret"]], timestamp=str(int(time.time()) - 3600),
                                         raw_body=received["body"],
                                         signature=self.receiver.header(-1, webhooks.SIGNATURE_HEADER))
        self.assertEqual(stale["reason"], "timestamp_out_of_tolerance")

    def test_item_completed_event_is_emitted_in_same_transaction(self) -> None:
        created = self._endpoint(event_types=["item.completed"])
        batch = self._batch()
        with patch("productdirector_api.main.execute_job"):
            main.advance_batch(batch["id"])
        items = self.client.get(f"/api/v1/batches/{batch['id']}/items").json()["items"]
        job_id = items[0]["job_id"]
        with main.connect() as db:
            db.execute("UPDATE jobs SET status = 'VERIFICATION_PASSED', progress = 100 WHERE id = ?", (job_id,))
        self.client.get(f"/api/v1/batches/{batch['id']}")
        outcome = main.dispatch_webhooks(limit=20)
        self.assertGreaterEqual(outcome["delivered"], 1)
        events = [item["event_type"] for item in self.receiver.bodies()]
        self.assertIn("item.completed", events)
        delivered = next(item for item in self.receiver.bodies() if item["event_type"] == "item.completed")
        self.assertEqual(delivered["payload"]["status"], "SUCCEEDED")
        self.assertEqual(delivered["payload"]["batch_id"], batch["id"])
        self.assertIn("aggregate", delivered)
        self.assertEqual(delivered["aggregate"]["type"], "batch_item")

    def test_outbox_write_is_rolled_back_with_business_write(self) -> None:
        batch = self._batch()
        with main.connect() as db:
            db.execute("UPDATE batch_items SET status = 'QUEUED' WHERE batch_id = ?", (batch["id"],))
        with main.connect() as db:
            before = db.execute("SELECT count(*) AS n FROM outbox_events").fetchone()["n"]
        try:
            with main.connect() as db:
                main.emit_event(db, event_type="batch.started", aggregate_type="batch", aggregate_id=batch["id"],
                                aggregate_revision=1, payload={"batch_id": batch["id"], "status": "RUNNING"})
                raise RuntimeError("业务写入失败（测试注入）")
        except RuntimeError:
            pass
        with main.connect() as db:
            after = db.execute("SELECT count(*) AS n FROM outbox_events").fetchone()["n"]
        self.assertEqual(before, after, "业务事务回滚时 Outbox 事件必须一起回滚（不能出现已发送但业务未生效）")

    def test_event_types_filter_and_disabled_endpoint(self) -> None:
        only_batch = self._endpoint(event_types=["batch.completed"])
        disabled = self._endpoint(event_types=["item.completed"], enabled=False)
        response = self.client.post(f"/api/v1/webhooks/{disabled['id']}/test")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "endpoint_disabled")
        batch = self._batch()
        with main.connect() as db:
            db.execute("UPDATE batch_items SET status = 'SUCCEEDED' WHERE batch_id = ?", (batch["id"],))
        self.client.get(f"/api/v1/batches/{batch['id']}")
        events = [item["event_type"] for item in self.receiver.bodies()]
        self.assertNotIn("item.completed", events, "未订阅的事件不得投递")
        outcome = main.dispatch_webhooks(limit=20)
        endpoint_ids = {item["endpoint_id"] for item in outcome["results"]}
        self.assertIn(only_batch["id"], endpoint_ids)
        self.assertNotIn(disabled["id"], endpoint_ids)

    def test_retry_schedule_and_dead_letter_after_max_attempts(self) -> None:
        created = self._endpoint(event_types=["webhook.test"])
        self.receiver.status_plan = [500] * 8
        response = self.client.post(f"/api/v1/webhooks/{created['id']}/test")
        first = response.json()["delivery"]
        self.assertEqual(first["status"], "RETRY")
        self.assertIsNotNone(first["next_retry_at"])
        self.assertEqual(first["attempt"], 1)
        # 演练/恢复用 force_due：忽略重试排期，连续重投直到死信（首次投递 + 6 次重试 = 7 次）
        attempt = 1
        for _ in range(8):
            result = main.dispatch_webhooks(limit=5, force_due=True)
            entry = next((item for item in result["results"] if item["event_id"] == first["event_id"]), None)
            if entry is None:
                break
            attempt = entry["attempt"]
        self.assertEqual(attempt, webhooks.MAX_ATTEMPTS)
        outcome = main.dispatch_webhooks(limit=5, force_due=True)
        self.assertEqual([item for item in outcome["results"] if item["event_id"] == first["event_id"]], [],
                         "进入死信后不应再自动投递")
        history = self.client.get(f"/api/v1/webhooks/{created['id']}/deliveries").json()
        self.assertTrue(history["dead_letters"], "超过最大次数必须进入死信")
        dead = history["dead_letters"][0]
        self.assertEqual(dead["attempts"], webhooks.MAX_ATTEMPTS)
        self.assertIn("最大投递次数", dead["reason"])
        listed = next(item for item in self.client.get("/api/v1/webhooks").json() if item["id"] == created["id"])
        self.assertEqual(listed["stats"]["dead_letter"], 1)
        self.assertEqual(len(self.receiver.requests), webhooks.MAX_ATTEMPTS)

    def test_non_retryable_4xx_pauses_endpoint(self) -> None:
        created = self._endpoint(event_types=["webhook.test"])
        self.receiver.status_plan = [403]
        response = self.client.post(f"/api/v1/webhooks/{created['id']}/test")
        self.assertEqual(response.json()["delivery"]["status"], "DEAD")
        listed = next(item for item in self.client.get("/api/v1/webhooks").json() if item["id"] == created["id"])
        self.assertIsNotNone(listed["paused_at"])
        self.assertIn("已暂停", listed["paused_reason"])
        follow_up = self.client.post(f"/api/v1/webhooks/{created['id']}/test")
        self.assertEqual(follow_up.status_code, 409)
        self.assertEqual(follow_up.json()["detail"]["code"], "endpoint_paused")
        resumed = self.client.post(f"/api/v1/webhooks/{created['id']}/resume")
        self.assertEqual(resumed.status_code, 200)
        self.assertIsNone(resumed.json()["paused_at"])

    def test_dead_letter_replay_keeps_event_id(self) -> None:
        created = self._endpoint(event_types=["webhook.test"])
        self.receiver.status_plan = [400]
        response = self.client.post(f"/api/v1/webhooks/{created['id']}/test")
        event_id = response.json()["event_id"]
        replay = self.client.post(f"/api/v1/webhooks/{created['id']}/dead-letters/{event_id}/replay")
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["event_id"], event_id)
        self.assertEqual(replay.json()["delivery"]["status"], "DELIVERED")
        bodies = self.receiver.bodies()
        self.assertEqual(bodies[-1]["event_id"], event_id)

    def test_429_is_retried_not_paused(self) -> None:
        created = self._endpoint(event_types=["webhook.test"])
        self.receiver.status_plan = [429]
        response = self.client.post(f"/api/v1/webhooks/{created['id']}/test")
        self.assertEqual(response.json()["delivery"]["status"], "RETRY")
        listed = next(item for item in self.client.get("/api/v1/webhooks").json() if item["id"] == created["id"])
        self.assertIsNone(listed["paused_at"])

    def test_deliveries_history_is_desensitized(self) -> None:
        created = self._endpoint(event_types=["webhook.test"])
        self.receiver.status_plan = [500]
        self.client.post(f"/api/v1/webhooks/{created['id']}/test")
        history = self.client.get(f"/api/v1/webhooks/{created['id']}/deliveries").json()
        self.assertTrue(history["deliveries"])
        entry = history["deliveries"][0]
        self.assertNotIn("secret", json.dumps(entry))
        self.assertNotIn("abcdef1234567890", json.dumps(entry))
        self.assertIn("至少一次", history["note"])

    def test_rotation_allows_both_keys_then_only_new(self) -> None:
        created = self._endpoint(event_types=["webhook.test"])
        rotated = self.client.post(f"/api/v1/webhooks/{created['id']}/rotate-secret",
                                   json={"grace_seconds": 60})
        self.assertEqual(rotated.status_code, 200, rotated.text)
        new_secret = rotated.json()["secret"]
        self.assertNotEqual(new_secret, created["secret"])
        self.client.post(f"/api/v1/webhooks/{created['id']}/test")
        received = self.receiver.requests[-1]
        timestamp = self.receiver.header(-1, webhooks.TIMESTAMP_HEADER)
        signature = self.receiver.header(-1, webhooks.SIGNATURE_HEADER)
        # 投递用当前密钥签名 → 双密钥列表里第一把（当前）命中
        both = webhooks.verify_delivery(secrets=[new_secret, created["secret"]], timestamp=timestamp,
                                        raw_body=received["body"], signature=signature)
        self.assertTrue(both["valid"])
        self.assertEqual(both["key_index"], 0)
        self.assertTrue(webhooks.verify_delivery(secrets=[new_secret], timestamp=timestamp,
                                                 raw_body=received["body"], signature=signature)["valid"])
        # 轮换宽限期内：用旧密钥签名的请求仍可通过（短期双密钥）
        old_signature = webhooks.signing_key(created["secret"], timestamp, received["body"])
        during_grace = webhooks.verify_delivery(secrets=[new_secret, created["secret"]], timestamp=timestamp,
                                               raw_body=received["body"], signature=old_signature)
        self.assertTrue(during_grace["valid"])
        self.assertEqual(during_grace["key_index"], 1)
        self.assertIn("上一个密钥", during_grace["note"])
        # 宽限期结束后旧密钥不再被接受
        expired = webhooks.verify_delivery(secrets=[new_secret], timestamp=timestamp,
                                          raw_body=received["body"], signature=old_signature)
        self.assertFalse(expired["valid"])
        listed = next(item for item in self.client.get("/api/v1/webhooks").json() if item["id"] == created["id"])
        self.assertFalse(listed["secret_previous_expires_at"] is None and listed["secret_previous_hint"] == "…")

    def test_update_requires_current_revision_and_validates_url(self) -> None:
        created = self._endpoint()
        stale = self.client.patch(f"/api/v1/webhooks/{created['id']}",
                                  json={"revision": 99, "name": "改名"})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["detail"]["code"], "revision_conflict")
        updated = self.client.patch(f"/api/v1/webhooks/{created['id']}",
                                    json={"revision": created["revision"], "name": "改名",
                                          "event_types": ["batch.started"]})
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["name"], "改名")
        self.assertEqual(updated.json()["event_types"], ["batch.started"])
        self.assertEqual(updated.json()["revision"], created["revision"] + 1)
        insecure = self.client.patch(f"/api/v1/webhooks/{created['id']}",
                                     json={"revision": updated.json()["revision"],
                                           "url": "http://evil.example.com/h"})
        self.assertEqual(insecure.status_code, 422)

    def test_catalog_endpoint_documents_contract(self) -> None:
        catalog = self.client.get("/api/v1/webhooks/catalog").json()
        self.assertEqual(catalog["headers"]["signature"], "X-PDA-Signature")
        self.assertEqual(catalog["headers"]["timestamp"], "X-PDA-Timestamp")
        self.assertIn("至少一次", catalog["note"])

    def test_webhook_test_event_never_carries_credentials(self) -> None:
        created = self._endpoint(event_types=["webhook.test"])
        self.client.post(f"/api/v1/webhooks/{created['id']}/test")
        body = self.receiver.requests[-1]["body"].decode("utf-8")
        self.assertNotIn(created["secret"], body)
        self.assertNotIn("secret_current", body)


if __name__ == "__main__":
    unittest.main()
