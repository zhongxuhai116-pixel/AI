"""V6-10…15：发布框架与平台连接器（状态机、预检、审批绑定、防重、对账、取消、BLOCKED 诚实性）。"""
from __future__ import annotations

import json
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tests"))
sys.path.insert(0, str(PROJECT / "apps" / "api"))

import test_job_control as fixtures  # noqa: E402

from productdirector_api import publishing  # noqa: E402
from productdirector_api import publishing_connectors as connectors  # noqa: E402

main = fixtures.main


def _package(**overrides) -> dict:
    package = {"id": "pkg-1", "version_id": "pkg-1:v1", "status": "APPROVED", "content_hash": "hash-1",
               "stored_hash": "hash-1", "qa_passed": True, "approval_ref": {"kind": "qa_report", "id": "qa-1"},
               "duration_seconds": 15.0, "has_music": False, "music_license_ref": None,
               "unlicensed_font": False, "locale": "es-MX", "profile_id": "tiktok-mx-9x16-esmx"}
    package.update(overrides)
    return package


def _options(**overrides) -> dict:
    options = {"visibility": "public_to_everyone", "caption": "Hola",
               "disclosures": {"is_synthetic_media": True, "is_branded_content": False, "made_for_kids": False}}
    options.update(overrides)
    return options


class PublishingRuleTests(unittest.TestCase):
    def test_state_machine_forbids_direct_draft_to_published(self) -> None:
        self.assertTrue(publishing.can_transition("DRAFT", "PREFLIGHT"))
        self.assertFalse(publishing.can_transition("DRAFT", "PUBLISHED"))
        self.assertFalse(publishing.can_transition("QUEUED", "PUBLISHED"), "提交成功不等于已发布")
        self.assertTrue(publishing.can_transition("PROCESSING", "PUBLISHED"))
        with self.assertRaises(publishing.PublishError) as ctx:
            publishing.check_transition("DRAFT", "PUBLISHED")
        self.assertEqual(ctx.exception.code, "invalid_transition")
        self.assertEqual(publishing.TRANSITIONS["PUBLISHED"], ())

    def test_dedupe_key_is_package_account_intent(self) -> None:
        first = publishing.dedupe_key(package_version_id="p1", account_id="a1", publish_intent_id="i1")
        again = publishing.dedupe_key(package_version_id="p1", account_id="a1", publish_intent_id="i1")
        other_intent = publishing.dedupe_key(package_version_id="p1", account_id="a1", publish_intent_id="i2")
        self.assertEqual(first, again)
        self.assertNotEqual(first, other_intent)

    def test_preflight_blocks_missing_qa_and_approval(self) -> None:
        connector = {"status": "READY", "missing_requirements": []}
        account = {"id": "a1", "connected": True, "authorization_status": "CONNECTED", "capabilities_checked_at": "now",
                   "revision": 1}
        result = publishing.preflight(platform="tiktok", package=_package(status="BUILT", qa_passed=False,
                                                                        approval_ref=None),
                                      account=account, options=_options(), connector=connector)
        codes = {item["code"] for item in result["blocking"]}
        self.assertIn("package_not_approved", codes)
        self.assertIn("qa_not_passed", codes)
        self.assertIn("approval_missing", codes)
        self.assertFalse(result["executable"])

    def test_preflight_requires_explicit_visibility_and_disclosures(self) -> None:
        connector = {"status": "READY", "missing_requirements": []}
        account = {"id": "a1", "connected": True, "authorization_status": "CONNECTED",
                   "capabilities_checked_at": "now", "revision": 1}
        result = publishing.preflight(platform="youtube", package=_package(), account=account,
                                      options={"caption": "x"}, connector=connector)
        codes = {item["code"] for item in result["blocking"]}
        self.assertIn("visibility_required", codes)
        self.assertIn("disclosure_missing", codes)
        wrong = publishing.preflight(platform="youtube", package=_package(), account=account,
                                     options=_options(visibility="public_to_everyone"), connector=connector)
        self.assertIn("visibility_unsupported", {item["code"] for item in wrong["blocking"]},
                      "不得把 TikTok 的隐私取值照搬到 YouTube")

    def test_preflight_blocks_unconfigured_connector_and_account(self) -> None:
        result = publishing.preflight(
            platform="tiktok", package=_package(),
            account={"id": "a1", "connected": False, "authorization_status": "REVOKED",
                     "capabilities_checked_at": None, "revision": 1},
            options=_options(), connector={"status": "NOT_CONFIGURED", "missing_requirements": ["access_token"]})
        codes = {item["code"] for item in result["blocking"]}
        self.assertIn("connector_not_configured", codes)
        self.assertIn("account_not_connected", codes)
        self.assertIn("account_authorization", codes)

    def test_preflight_snapshot_hash_binds_fields(self) -> None:
        connector = {"status": "READY", "missing_requirements": []}
        account = {"id": "a1", "connected": True, "authorization_status": "CONNECTED",
                   "capabilities_checked_at": "now", "revision": 1}
        first = publishing.preflight(platform="tiktok", package=_package(), account=account,
                                     options=_options(), connector=connector)
        second = publishing.preflight(platform="tiktok", package=_package(), account=account,
                                      options=_options(caption="Otro"), connector=connector)
        self.assertNotEqual(first["snapshot_hash"], second["snapshot_hash"])

    def test_approval_requires_explicit_confirmations(self) -> None:
        connector = {"status": "READY", "missing_requirements": []}
        account = {"id": "a1", "connected": True, "authorization_status": "CONNECTED",
                   "capabilities_checked_at": "now", "revision": 1}
        result = publishing.preflight(platform="tiktok", package=_package(), account=account,
                                      options=_options(), connector=connector)
        with self.assertRaises(publishing.PublishError) as ctx:
            publishing.build_approval(preflight_result=result, actor="session", expires_in_seconds=600,
                                      confirmations={"package_hash": True})
        self.assertEqual(ctx.exception.code, "confirmation_required")
        approval = publishing.build_approval(
            preflight_result=result, actor="session", expires_in_seconds=600,
            confirmations={"package_hash": True, "account": True, "visibility": True, "disclosures": True})
        self.assertEqual(approval["snapshot_hash"], result["snapshot_hash"])
        self.assertEqual(approval["scope"], "single_publish")

    def test_approval_invalidates_on_any_binding_change(self) -> None:
        connector = {"status": "READY", "missing_requirements": []}
        account = {"id": "a1", "connected": True, "authorization_status": "CONNECTED",
                   "capabilities_checked_at": "now", "revision": 1}
        result = publishing.preflight(platform="tiktok", package=_package(), account=account,
                                      options=_options(), connector=connector)
        approval = publishing.build_approval(
            preflight_result=result, actor="session", expires_in_seconds=600,
            confirmations={"package_hash": True, "account": True, "visibility": True, "disclosures": True})
        same = publishing.approval_still_valid(approval_binding=approval["binding"],
                                               current_snapshot=result["snapshot"])
        self.assertTrue(same["valid"])
        changed = dict(result["snapshot"], visibility="self_only")
        invalid = publishing.approval_still_valid(approval_binding=approval["binding"], current_snapshot=changed)
        self.assertFalse(invalid["valid"])
        self.assertEqual(invalid["mismatches"][0]["field"], "visibility")

    def test_interpret_query_never_upgrades_submission_to_published(self) -> None:
        processing = publishing.interpret_query(operation_handle={"status": "PROCESSING"}, platform="tiktok")
        self.assertEqual(processing["state"], "PROCESSING")
        self.assertIn("不等于已发布", processing["note"])
        unknown = publishing.interpret_query(operation_handle={"status": "??"}, platform="tiktok")
        self.assertEqual(unknown["state"], "RECONCILING")
        none = publishing.interpret_query(operation_handle=None, platform="tiktok")
        self.assertEqual(none["state"], "RECONCILING")
        published = publishing.interpret_query(operation_handle={"status": "PUBLISHED", "external_id": "x"},
                                               platform="tiktok")
        self.assertEqual(published["state"], "PUBLISHED")
        self.assertFalse(published["fabricated_url"])
        self.assertIn("不捏造 URL", published["note"])

    def test_cancel_capability_is_explicit(self) -> None:
        self.assertFalse(publishing.cancel_capability("tiktok", "UPLOADING")["supported"])
        self.assertIn("不支持取消", publishing.cancel_capability("tiktok", "UPLOADING")["reason"])
        self.assertTrue(publishing.cancel_capability("youtube", "PROCESSING")["supported"])
        self.assertFalse(publishing.cancel_capability("youtube", "PUBLISHED")["supported"])

    def test_retry_decision_requires_reconcile_when_unknown(self) -> None:
        decision = publishing.retry_decision(state="FAILED", failure_code="upstream_5xx", submission_unknown=True)
        self.assertFalse(decision["retry"])
        self.assertEqual(decision["reason"], "submission_unknown")
        ok = publishing.retry_decision(state="FAILED", failure_code="network", submission_unknown=False)
        self.assertTrue(ok["retry"])
        auth = publishing.retry_decision(state="AUTH_REQUIRED", failure_code="auth_expired", submission_unknown=False)
        self.assertFalse(auth["retry"])


class ConnectorTests(unittest.TestCase):
    def test_all_four_connectors_are_not_configured_without_credentials(self) -> None:
        for platform in ("tiktok", "youtube", "instagram", "facebook_page"):
            status = connectors.connector_status(platform, {})
            self.assertEqual(status["status"], "NOT_CONFIGURED", platform)
            self.assertTrue(status["missing_requirements"], platform)
            self.assertTrue(status["env_vars"], platform)
            self.assertTrue(status["docs"].startswith("docs/integrations/"), platform)
            self.assertTrue(status["unconfirmed_limits"], "未确认项必须显式列出")
            self.assertIn("浏览器自动化", " ".join(status["notes"]))

    def test_overview_lists_ready_and_blocked(self) -> None:
        overview = connectors.connector_overview()
        self.assertEqual(len(overview["connectors"]), 4)
        self.assertEqual(overview["ready"], [])
        self.assertEqual(sorted(overview["blocked"]), ["facebook_page", "instagram", "tiktok", "youtube"])
        self.assertIn("不把", overview["note"])

    def test_begin_authorization_refuses_to_fake_links(self) -> None:
        verifier, challenge = connectors.pkce_pair()
        self.assertNotEqual(verifier, challenge)
        outcome = connectors.begin_authorization("tiktok", state="s1", code_challenge=challenge,
                                                 redirect_uri_value="https://example.com/cb", credentials={})
        self.assertEqual(outcome["status"], "BLOCKED")
        self.assertIsNone(outcome["url"], "未配置凭据时不得生成假授权链接")
        # TikTok 官方参数名是 client_key（不是 client_id）
        self.assertIn("client_key", outcome["parameters"])
        self.assertEqual(outcome["parameters"]["client_key"], "<未配置>")
        self.assertEqual(connectors.redirect_uri().endswith("/api/v1/publishing/oauth/callback"), True)

    def test_exchange_callback_without_credentials_is_blocked(self) -> None:
        with self.assertRaises(publishing.PublishError) as ctx:
            connectors.exchange_callback("youtube", code="c", code_verifier="v",
                                         redirect_uri="https://example.com/cb", credentials={})
        self.assertEqual(ctx.exception.code, "not_configured")
        self.assertIn("PRODUCTDIRECTOR_YOUTUBE_CLIENT_ID", ctx.exception.message)

    def test_token_exchange_against_stub_provider(self) -> None:
        """真实 HTTP 流程：把 token 端点指向本机 stub，验证换 token 与"不落明文"的返回。"""
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        captured: list[dict] = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length).decode()
                captured.append({"body": body, "auth": self.headers.get("Authorization")})
                payload = json.dumps({"access_token": "tok-123", "expires_in": 3600,
                                      "refresh_token": "ref-456", "open_id": "open-1"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            credentials = {"client_id": "cid", "client_secret": "sec",
                           "oauth_base": f"http://127.0.0.1:{server.server_address[1]}/token"}
            result = connectors.exchange_callback("tiktok", code="auth-code", code_verifier="verifier-1",
                                                  redirect_uri="https://example.com/cb", credentials=credentials)
            self.assertTrue(result["connected"])
            self.assertEqual(result["account_ref"], "open-1")
            self.assertNotIn("tok-123", json.dumps(result), "令牌明文不得回传")
            self.assertIn("client_key=cid", captured[0]["body"])
            self.assertIn("code_verifier=verifier-1", captured[0]["body"])
        finally:
            server.shutdown()
            server.server_close()

    def test_refresh_failure_returns_auth_required(self) -> None:
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"invalid_grant"}')

            def log_message(self, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            result = connectors.refresh_authorization("tiktok", credentials={
                "client_id": "cid", "client_secret": "sec", "refresh_token": "old",
                "oauth_base": f"http://127.0.0.1:{server.server_address[1]}/token"})
            self.assertFalse(result["refreshed"])
            self.assertEqual(result["state"], "AUTH_REQUIRED")
            self.assertIn("不循环重试", result["note"])
        finally:
            server.shutdown()
            server.server_close()

    def test_submit_publish_without_authorization_is_blocked(self) -> None:
        with self.assertRaises(publishing.PublishError) as ctx:
            connectors.submit_publish("tiktok", _package(), _options(), "key-1", credentials={})
        self.assertEqual(ctx.exception.code, "not_configured")
        result = connectors.submit_publish("tiktok", _package(), _options(creator_info_checked=True), "key-1",
                                           credentials={"access_token": "tok"})
        self.assertFalse(result["submitted"])
        self.assertEqual(result["state"], "BLOCKED")
        self.assertIn("不会发起真实上传", result["note"])

    def test_tiktok_creator_info_prerequisite(self) -> None:
        with self.assertRaises(publishing.PublishError) as ctx:
            connectors.submit_publish("tiktok", _package(), _options(), "key-1",
                                      credentials={"access_token": "tok"})
        self.assertEqual(ctx.exception.code, "creator_info_required")

    def test_cancel_unsupported_platform_is_explicit(self) -> None:
        with self.assertRaises(publishing.PublishError) as ctx:
            connectors.cancel_publish("instagram", "ext-1", credentials={"access_token": "t"})
        self.assertEqual(ctx.exception.code, "cancel_unsupported")
        self.assertIn("不支持取消", ctx.exception.message)

    def test_platform_limits_block_only_confirmed_values(self) -> None:
        result = connectors.validate_package("instagram", _package(duration_seconds=1000), {},
                                             _options(visibility="public"), credentials={})
        self.assertIn("duration_exceeds_platform", {item["code"] for item in result["blocking"]})
        too_long = connectors.validate_package("instagram", _package(), {},
                                              _options(caption="x" * 3000), credentials={})
        self.assertIn("caption_too_long", {item["code"] for item in too_long["blocking"]})
        hosted = connectors.validate_package("instagram", _package(), {}, _options(), credentials={})
        self.assertIn("hosted_url_required", {item["code"] for item in hosted["blocking"]})
        self.assertTrue(result["warnings"], "官方未确认的限制必须给 WARNING 而不是阻断")


class PublishingApiTests(unittest.TestCase):
    _create_asset = fixtures.JobControlAcceptanceTests._create_asset
    _create_plan = fixtures.JobControlAcceptanceTests._create_plan

    def setUp(self) -> None:
        fixtures.JobControlAcceptanceTests.setUp(self)
        self.asset = self._create_asset()
        self.plan = self._create_plan()
        self.client.post(f"/api/v1/plans/{self.plan['id']}/approve", json={"approved": True})

    def _connectors(self) -> dict:
        response = self.client.get("/api/v1/publishing/connectors")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_connectors_endpoint_is_honest(self) -> None:
        body = self._connectors()
        self.assertEqual(body["ready"], [])
        tiktok = next(item for item in body["connectors"] if item["platform"] == "tiktok")
        self.assertEqual(tiktok["status"], "NOT_CONFIGURED")
        self.assertIn("video.publish", tiktok["scopes"])
        self.assertFalse(tiktok["cancel_supported"])
        self.assertIn("oauth", body)

    def test_connect_requires_credentials_and_consumes_state_once(self) -> None:
        response = self.client.post("/api/v1/publishing/accounts/connect",
                                    json={"platform": "tiktok", "return_path": "/publish"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "BLOCKED")
        self.assertIsNone(body["url"])
        state = body["state"]
        first = self.client.post("/api/v1/publishing/oauth/tiktok/callback",
                                 json={"state": state, "code": "abcd", "code_verifier": "v"})
        self.assertEqual(first.status_code, 409, first.text)
        self.assertEqual(first.json()["detail"]["code"], "not_configured")
        replay = self.client.post("/api/v1/publishing/oauth/tiktok/callback",
                                  json={"state": state, "code": "abcd"})
        self.assertEqual(replay.status_code, 409)
        self.assertEqual(replay.json()["detail"]["code"], "state_already_used")
        unknown = self.client.post("/api/v1/publishing/oauth/tiktok/callback",
                                   json={"state": "not-a-real-state", "code": "abcd"})
        self.assertEqual(unknown.status_code, 400)
        self.assertEqual(unknown.json()["detail"]["code"], "unknown_state")

    def test_preflight_without_account_is_blocked(self) -> None:
        response = self.client.post("/api/v1/publishing/preflight",
                                    json={"package_ids": ["missing-package"], "options": {}})
        self.assertEqual(response.status_code, 404)
        with main.connect() as db:
            db.execute(
                "INSERT INTO publish_packages(id, run_id, batch_id, owner_id, project_id, profile_id, locale, "
                "version, status, content_hash, directory, zip_path, zip_sha256, approved_at, payload, "
                "payload_sha256, created_at, updated_at) VALUES (?, ?, NULL, ?, ?, ?, 'es-MX', 1, 'APPROVED', "
                "'hash-1', '/tmp/x', '', '', ?, ?, ?, ?, ?)",
                ("pkg-test-1", "run-1", main.DEFAULT_OWNER_ID, main.DEFAULT_PROJECT_ID, "tiktok-mx-9x16-esmx",
                 main.utc_now(), json.dumps({"manifest": {"files": [], "qa": {"passed": True},
                                                          "approval_ref": {"kind": "qa_report", "id": "x"},
                                                          "subtitles": {"font_license": "Bitstream Vera"},
                                                          "video": {"duration_seconds": 15}},
                                             "verification": {"failures": []}}),
                 "sha", main.utc_now(), main.utc_now()))
        response = self.client.post("/api/v1/publishing/preflight",
                                    json={"package_ids": ["pkg-test-1"], "options": {"visibility": "self_only"}})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["executable_count"], 0)
        self.assertEqual(body["blocked_count"], 1)
        self.assertIn("no_connected_account", {item["code"] for item in body["results"][0]["blocking"]})
        self.assertIsNone(body["results"][0]["snapshot_hash"])

    def test_approval_requires_preflight_and_confirmations(self) -> None:
        missing = self.client.post("/api/v1/publishing/approvals",
                                   json={"preflight_id": "nope", "confirmations": {}})
        self.assertEqual(missing.status_code, 404)
        with main.connect() as db:
            db.execute("INSERT INTO publish_preflights(id, owner_id, project_id, payload, payload_sha256, "
                       "created_at) VALUES ('pf-1', ?, ?, ?, 'sha', ?)",
                       (main.DEFAULT_OWNER_ID, main.DEFAULT_PROJECT_ID,
                        json.dumps({"results": [{"executable": False, "package_id": "p", "blocking": []}]}),
                        main.utc_now()))
        blocked = self.client.post("/api/v1/publishing/approvals",
                                   json={"preflight_id": "pf-1", "confirmations": {}})
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()["detail"]["code"], "no_executable_preflight")

    def test_publishing_scope_required_for_automation_keys(self) -> None:
        created = self.client.post("/api/v1/automation-keys",
                                   json={"name": "只读", "scopes": ["packages:read"]}).json()
        from fastapi.testclient import TestClient

        client = TestClient(main.app, headers={"Authorization": f"Bearer {created['key']}"})
        self.assertEqual(client.get("/api/v1/publishing/connectors").status_code, 200)
        blocked = client.post("/api/v1/publishing/preflight", json={"package_ids": ["x"], "options": {}})
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(blocked.json()["detail"]["code"], "insufficient_scope")
        self.assertEqual(blocked.json()["detail"]["required_scopes"], ["publish:write"])

    def test_job_creation_without_authorization_is_blocked_not_faked(self) -> None:
        with main.connect() as db:
            db.execute(
                "INSERT INTO publish_packages(id, run_id, batch_id, owner_id, project_id, profile_id, locale, "
                "version, status, content_hash, directory, zip_path, zip_sha256, approved_at, payload, "
                "payload_sha256, created_at, updated_at) VALUES ('pkg-api-1', 'run-1', NULL, ?, ?, "
                "'tiktok-mx-9x16-esmx', 'es-MX', 1, 'APPROVED', 'hash-1', '/tmp/x', '', '', ?, ?, ?, ?, ?)",
                (main.DEFAULT_OWNER_ID, main.DEFAULT_PROJECT_ID, main.utc_now(),
                 json.dumps({"manifest": {"files": [], "qa": {"passed": True},
                                          "approval_ref": {"kind": "qa_report", "id": "x"},
                                          "subtitles": {"font_license": "Bitstream Vera"},
                                          "video": {"duration_seconds": 15}},
                             "verification": {"failures": []}}), "sha", main.utc_now(), main.utc_now()))
            db.execute(
                "INSERT INTO connected_accounts(id, owner_id, project_id, platform, account_ref, display_name, "
                "credential_ref, scopes, capabilities, authorization_status, capabilities_checked_at, connected_at, "
                "disconnected_at, disconnect_reason, revision, created_at, updated_at) VALUES "
                "('acct-1', ?, ?, 'tiktok', 'acct-ref', 'TikTok 测试账号', 'env:X', 'video.publish', '{}', "
                "'CONNECTED', ?, ?, NULL, '', 1, ?, ?)",
                (main.DEFAULT_OWNER_ID, main.DEFAULT_PROJECT_ID, main.utc_now(), main.utc_now(),
                 main.utc_now(), main.utc_now()))
        preflight = self.client.post("/api/v1/publishing/preflight", json={
            "package_ids": ["pkg-api-1"], "account_ids": ["acct-1"],
            "options": {"visibility": "self_only", "caption": "Hola",
                        "disclosures": {"is_synthetic_media": True, "is_branded_content": False,
                                        "made_for_kids": False}}}).json()
        result = preflight["results"][0]
        self.assertFalse(result["executable"], "连接器未配置时预检必须阻断")
        self.assertIn("connector_not_configured", {item["code"] for item in result["blocking"]})
        approval = self.client.post("/api/v1/publishing/approvals", json={
            "preflight_id": preflight["preflight_id"],
            "confirmations": {"package_hash": True, "account": True, "visibility": True, "disclosures": True}})
        self.assertEqual(approval.status_code, 409, approval.text)
        self.assertEqual(approval.json()["detail"]["code"], "no_executable_preflight")

    def test_job_flow_with_stubbed_ready_connector(self) -> None:
        """把连接器临时置为 READY（注入凭据）验证：审批 → 202 创建 → 防重 → 对账拒绝 → 取消语义。"""
        with main.connect() as db:
            db.execute(
                "INSERT INTO publish_packages(id, run_id, batch_id, owner_id, project_id, profile_id, locale, "
                "version, status, content_hash, directory, zip_path, zip_sha256, approved_at, payload, "
                "payload_sha256, created_at, updated_at) VALUES ('pkg-api-2', 'run-1', NULL, ?, ?, "
                "'tiktok-mx-9x16-esmx', 'es-MX', 1, 'APPROVED', 'hash-1', '/tmp/x', '', '', ?, ?, ?, ?, ?)",
                (main.DEFAULT_OWNER_ID, main.DEFAULT_PROJECT_ID, main.utc_now(),
                 json.dumps({"manifest": {"files": [], "qa": {"passed": True},
                                          "approval_ref": {"kind": "qa_report", "id": "x"},
                                          "subtitles": {"font_license": "Bitstream Vera"},
                                          "video": {"duration_seconds": 15}},
                             "verification": {"failures": []}}), "sha", main.utc_now(), main.utc_now()))
            db.execute(
                "INSERT INTO connected_accounts(id, owner_id, project_id, platform, account_ref, display_name, "
                "credential_ref, scopes, capabilities, authorization_status, capabilities_checked_at, connected_at, "
                "disconnected_at, disconnect_reason, revision, created_at, updated_at) VALUES "
                "('acct-2', ?, ?, 'tiktok', 'acct-ref', 'TikTok 测试账号', 'env:X', 'video.publish', '{}', "
                "'CONNECTED', ?, ?, NULL, '', 1, ?, ?)",
                (main.DEFAULT_OWNER_ID, main.DEFAULT_PROJECT_ID, main.utc_now(), main.utc_now(),
                 main.utc_now(), main.utc_now()))
        ready = {"status": "READY", "missing_requirements": [], "limits": {}, "display_name": "TikTok"}
        with patch.object(main.publishing_connectors, "connector_status", return_value=ready), \
                patch.object(main, "_connector_credentials", return_value={"access_token": "tok"}):
            preflight = self.client.post("/api/v1/publishing/preflight", json={
                "package_ids": ["pkg-api-2"], "account_ids": ["acct-2"],
                "options": {"visibility": "self_only", "caption": "Hola",
                            "disclosures": {"is_synthetic_media": True, "is_branded_content": False,
                                            "made_for_kids": False}}}).json()
            self.assertEqual(preflight["executable_count"], 1, preflight)
            approval = self.client.post("/api/v1/publishing/approvals", json={
                "preflight_id": preflight["preflight_id"],
                "confirmations": {"package_hash": True, "account": True, "visibility": True, "disclosures": True}})
            self.assertEqual(approval.status_code, 201, approval.text)
            approval_id = approval.json()["id"]
            # 提交必须带与审批完全一致的选项：绑定字段变化会使审批失效（这里就是要验证严格性）
            approved_options = {"visibility": "self_only", "caption": "Hola",
                                "disclosures": {"is_synthetic_media": True, "is_branded_content": False,
                                                "made_for_kids": False}}
            body = {"approval_id": approval_id, "package_id": "pkg-api-2", "account_id": "acct-2",
                    "publish_intent_id": "intent-1", "options": approved_options}
            mismatched = self.client.post("/api/v1/publishing/jobs", json={
                **body, "publish_intent_id": "intent-0",
                "options": {**approved_options, "caption": "Otro texto"}})
            self.assertEqual(mismatched.status_code, 202)
            self.assertEqual(mismatched.json()["job"]["state"], "WAITING_APPROVAL",
                             "选项与审批不一致时不得进入上传队列")
            self.assertFalse(mismatched.json()["approval_valid"]["valid"])
            created = self.client.post("/api/v1/publishing/jobs", json=body)
            self.assertEqual(created.status_code, 202, created.text)
            job = created.json()["job"]
            self.assertEqual(job["state"], "QUEUED", "连接器 READY 且审批有效时进入上传队列")
            self.assertIn("提交成功只是进入上传队列", created.json()["submit_plan"]["note"])
            replay = self.client.post("/api/v1/publishing/jobs", json=body)
            self.assertTrue(replay.json()["reused"])
            self.assertEqual(replay.json()["job"]["id"], job["id"])
            new_intent = self.client.post("/api/v1/publishing/jobs", json={**body, "publish_intent_id": "intent-2"})
            self.assertEqual(new_intent.status_code, 202)
            self.assertNotEqual(new_intent.json()["job"]["id"], job["id"], "同一包再次发布必须新建 intent")
            fetched = self.client.get(f"/api/v1/publishing/jobs/{job['id']}")
            self.assertEqual(fetched.status_code, 200)
            self.assertEqual(fetched.json()["state"], "QUEUED")
            # 对账：连接器 READY 但上游查询返回 UNKNOWN → RECONCILING（不宣称已发布）
            reconciled = self.client.post(f"/api/v1/publishing/jobs/{job['id']}/reconcile")
            self.assertEqual(reconciled.status_code, 200, reconciled.text)
            self.assertEqual(reconciled.json()["state"], "RECONCILING")
            # TikTok 不支持取消 → 如实返回不支持
            cancelled = self.client.post(f"/api/v1/publishing/jobs/{job['id']}/cancel", json={"reason": "测试"})
            self.assertEqual(cancelled.status_code, 200)
            self.assertFalse(cancelled.json()["cancelled"])
            self.assertIn("不支持取消", cancelled.json()["note"])

    def test_retry_requires_safe_failure(self) -> None:
        with main.connect() as db:
            db.execute("INSERT INTO publish_jobs(id, owner_id, project_id, platform, account_id, package_id, "
                       "package_version_id, publish_intent_id, approval_id, dedupe_key, state, external_publish_id, "
                       "permalink_url, last_error, attempt_count, payload, payload_sha256, created_at, updated_at) "
                       "VALUES ('job-x', ?, ?, 'tiktok', 'a', 'p', 'p:v1', 'i', 'ap', 'dk-x', 'FAILED', NULL, NULL, "
                       "'boom', 0, ?, 'sha', ?, ?)",
                       (main.DEFAULT_OWNER_ID, main.DEFAULT_PROJECT_ID,
                        json.dumps({"failure_code": "network"}), main.utc_now(), main.utc_now()))
            db.execute("INSERT INTO publish_jobs(id, owner_id, project_id, platform, account_id, package_id, "
                       "package_version_id, publish_intent_id, approval_id, dedupe_key, state, external_publish_id, "
                       "permalink_url, last_error, attempt_count, payload, payload_sha256, created_at, updated_at) "
                       "VALUES ('job-y', ?, ?, 'tiktok', 'a', 'p', 'p:v1', 'i2', 'ap', 'dk-y', 'FAILED', NULL, NULL, "
                       "'unknown', 0, ?, 'sha', ?, ?)",
                       (main.DEFAULT_OWNER_ID, main.DEFAULT_PROJECT_ID,
                        json.dumps({"failure_code": "upstream_5xx", "submission_unknown": True}),
                        main.utc_now(), main.utc_now()))
        safe = self.client.post("/api/v1/publishing/jobs/job-x/retry", json={"reason": "网络已恢复"})
        self.assertEqual(safe.status_code, 200, safe.text)
        self.assertEqual(safe.json()["state"], "QUEUED")
        unsafe = self.client.post("/api/v1/publishing/jobs/job-y/retry", json={"reason": "试试"})
        self.assertEqual(unsafe.status_code, 409)
        self.assertEqual(unsafe.json()["detail"]["code"], "submission_unknown")

    def test_disconnect_is_idempotent_and_blocks_active_jobs(self) -> None:
        with main.connect() as db:
            db.execute(
                "INSERT INTO connected_accounts(id, owner_id, project_id, platform, account_ref, display_name, "
                "credential_ref, scopes, capabilities, authorization_status, capabilities_checked_at, connected_at, "
                "disconnected_at, disconnect_reason, revision, created_at, updated_at) VALUES "
                "('acct-3', ?, ?, 'youtube', 'yt-ref', 'YouTube', 'env:Y', 'youtube.upload', '{}', 'CONNECTED', NULL, "
                "?, NULL, '', 1, ?, ?)",
                (main.DEFAULT_OWNER_ID, main.DEFAULT_PROJECT_ID, main.utc_now(), main.utc_now(), main.utc_now()))
            db.execute("INSERT INTO publish_jobs(id, owner_id, project_id, platform, account_id, package_id, "
                       "package_version_id, publish_intent_id, approval_id, dedupe_key, state, external_publish_id, "
                       "permalink_url, last_error, attempt_count, payload, payload_sha256, created_at, updated_at) "
                       "VALUES ('job-z', ?, ?, 'youtube', 'acct-3', 'p', 'p:v1', 'i', 'ap', 'dk-z', 'UPLOADING', NULL, "
                       "NULL, NULL, 0, '{}', 'sha', ?, ?)",
                       (main.DEFAULT_OWNER_ID, main.DEFAULT_PROJECT_ID, main.utc_now(), main.utc_now()))
        first = self.client.request("DELETE", "/api/v1/publishing/accounts/acct-3", json={"reason": "轮换"})
        self.assertEqual(first.status_code, 200, first.text)
        self.assertTrue(first.json()["disconnected"])
        self.assertEqual(len(first.json()["blocked_active_jobs"]), 1)
        job = self.client.get("/api/v1/publishing/jobs/job-z").json()
        self.assertEqual(job["state"], "BLOCKED")
        self.assertIn("账号已断开", job["last_error"])
        again = self.client.request("DELETE", "/api/v1/publishing/accounts/acct-3", json={})
        self.assertEqual(again.status_code, 200)
        self.assertIn("早已断开", again.json()["note"])


if __name__ == "__main__":
    unittest.main()
