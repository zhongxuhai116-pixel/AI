"""V6-16：成员与角色权限（Owner / Editor / Reviewer / Publisher）与职责分离。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tests"))
sys.path.insert(0, str(PROJECT / "apps" / "api"))

import test_job_control as fixtures  # noqa: E402

from productdirector_api import roles  # noqa: E402

main = fixtures.main


class RoleRuleTests(unittest.TestCase):
    def test_matrix_has_four_roles_and_separation_of_duties(self) -> None:
        matrix = roles.matrix()
        self.assertEqual(matrix["roles"], ["owner", "editor", "reviewer", "publisher"])
        self.assertIn("publish:write", matrix["permissions"]["publisher"])
        self.assertNotIn("publish:write", matrix["permissions"]["reviewer"],
                         "审核与发布必须分离")
        self.assertNotIn("package:approve", matrix["permissions"]["publisher"],
                         "发布者不能自己审批")
        self.assertNotIn("package:approve", matrix["permissions"]["editor"])
        self.assertIn("member:manage", matrix["permissions"]["owner"])
        self.assertIn("separation_of_duties", matrix)

    def test_editor_can_build_but_not_approve_or_publish(self) -> None:
        roles.check_permission("editor", "POST", "/api/v1/batches")
        roles.check_permission("editor", "POST", "/api/v1/runs")
        roles.check_permission("editor", "POST", "/api/v1/runs/abc/packages")
        for method, path, permission in (("POST", "/api/v1/packages/p/approve", "package:approve"),
                                         ("POST", "/api/v1/publishing/jobs", "publish:write"),
                                         ("POST", "/api/v1/members", "member:manage")):
            with self.assertRaises(roles.RoleError) as ctx:
                roles.check_permission("editor", method, path)
            self.assertEqual(ctx.exception.code, "permission_denied")
            self.assertEqual(ctx.exception.detail["required_permission"], permission)

    def test_reviewer_can_approve_but_not_publish_or_edit(self) -> None:
        roles.check_permission("reviewer", "POST", "/api/v1/packages/p/approve")
        # 构建发布包属于编辑职责（package:build），审核者只负责审批
        for method, path in (("POST", "/api/v1/runs/abc/packages"), ("POST", "/api/v1/publishing/jobs"),
                             ("POST", "/api/v1/batches"), ("PATCH", "/api/v1/plans/plan-1")):
            with self.assertRaises(roles.RoleError):
                roles.check_permission("reviewer", method, path)

    def test_publisher_can_publish_but_not_approve_or_edit(self) -> None:
        roles.check_permission("publisher", "POST", "/api/v1/publishing/jobs")
        roles.check_permission("publisher", "POST", "/api/v1/publishing/preflight")
        for method, path in (("POST", "/api/v1/packages/p/approve"), ("POST", "/api/v1/batches"),
                             ("POST", "/api/v1/runs")):
            with self.assertRaises(roles.RoleError):
                roles.check_permission("publisher", method, path)

    def test_read_access_is_shared_but_unregistered_writes_are_denied(self) -> None:
        for role in ("editor", "reviewer", "publisher"):
            roles.check_permission(role, "GET", "/api/v1/health")
            roles.check_permission(role, "GET", "/api/v1/packages")
            roles.check_permission(role, "GET", "/api/v1/console/overview")
        with self.assertRaises(roles.RoleError) as ctx:
            roles.check_permission("editor", "POST", "/api/v1/settings/provider")
        self.assertIn("默认拒绝", ctx.exception.message)

    def test_token_generation_and_parsing(self) -> None:
        member = roles.generate_member_token()
        self.assertTrue(member["plaintext"].startswith("pdm_"))
        parsed = roles.parse_token(member["plaintext"])
        self.assertIsNotNone(parsed)
        member_id, secret = parsed
        self.assertEqual(member_id, member["member_id"])
        self.assertTrue(roles.token_matches(secret, member["token_hash"]))
        self.assertFalse(roles.token_matches("nope", member["token_hash"]))
        self.assertIsNone(roles.parse_token("pdm_short"))
        self.assertIsNone(roles.parse_token("token"))


class MemberApiTests(unittest.TestCase):
    def setUp(self) -> None:
        fixtures.JobControlAcceptanceTests.setUp(self)

    def _member(self, role: str, name: str = "成员") -> dict:
        response = self.client.post("/api/v1/members", json={"name": name, "role": role})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def _client_for(self, token: str):
        from fastapi.testclient import TestClient

        return TestClient(main.app, headers={"Authorization": f"Bearer {token}"})

    def test_member_token_shown_once_and_listed_without_it(self) -> None:
        created = self._member("reviewer", "审核员")
        self.assertTrue(created["token"].startswith("pdm_"))
        self.assertIn("package:approve", created["permissions"])
        listed = self.client.get("/api/v1/members").json()
        entry = next(item for item in listed if item["id"] == created["id"])
        self.assertIsNone(entry["token"])
        self.assertNotIn("token_hash", entry)
        self.assertEqual(entry["token_hint"], created["token_hint"])

    def test_role_matrix_endpoint(self) -> None:
        body = self.client.get("/api/v1/roles/matrix").json()
        self.assertEqual(body["roles"], ["owner", "editor", "reviewer", "publisher"])

    def test_member_reads_allowed_and_writes_denied_by_role(self) -> None:
        editor = self._member("editor")
        client = self._client_for(editor["token"])
        self.assertEqual(client.get("/api/v1/packages").status_code, 200)
        denied = client.post("/api/v1/packages/whatever/approve", json={"content_hash": "x", "reason": "y"})
        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(denied.json()["detail"]["code"], "permission_denied")
        self.assertEqual(denied.json()["detail"]["required_permission"], "package:approve")
        reviewer = self._member("reviewer")
        reviewer_client = self._client_for(reviewer["token"])
        blocked = reviewer_client.post("/api/v1/batches", json={"items": []})
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(blocked.json()["detail"]["required_permission"], "batch:write")
        publisher = self._member("publisher")
        publisher_client = self._client_for(publisher["token"])
        self.assertEqual(publisher_client.get("/api/v1/publishing/connectors").status_code, 200)
        self.assertEqual(publisher_client.post("/api/v1/runs", json={}).status_code, 403)

    def test_member_cannot_manage_members(self) -> None:
        member = self._member("publisher")
        client = self._client_for(member["token"])
        response = client.get("/api/v1/members")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"]["required_permission"], "member:manage")

    def test_revoked_member_is_rejected(self) -> None:
        member = self._member("editor")
        client = self._client_for(member["token"])
        self.assertEqual(client.get("/api/v1/packages").status_code, 200)
        revoked = self.client.request("DELETE", f"/api/v1/members/{member['id']}", json={"reason": "离职"})
        self.assertEqual(revoked.status_code, 200, revoked.text)
        self.assertTrue(revoked.json()["revoked"])
        after = client.get("/api/v1/packages")
        self.assertEqual(after.status_code, 401, after.text)
        again = self.client.request("DELETE", f"/api/v1/members/{member['id']}", json={})
        self.assertEqual(again.status_code, 200)
        self.assertIn("幂等", again.json()["note"])

    def test_invalid_member_token_is_rejected(self) -> None:
        self.assertEqual(self._client_for("pdm_deadbeef_wrong").get("/api/v1/packages").status_code, 401)
        created = self._member("editor")
        tampered = created["token"].rsplit("_", 1)[0] + "_tampered"
        self.assertEqual(self._client_for(tampered).get("/api/v1/packages").status_code, 401)

    def test_unknown_role_rejected(self) -> None:
        response = self.client.post("/api/v1/members", json={"name": "x", "role": "admin"})
        self.assertEqual(response.status_code, 422)

    def test_owner_session_keeps_full_access(self) -> None:
        self.assertEqual(self.client.get("/api/v1/members").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/roles/matrix").status_code, 200)

    def test_member_usage_tracked(self) -> None:
        member = self._member("reviewer")
        client = self._client_for(member["token"])
        client.get("/api/v1/packages")
        listed = next(item for item in self.client.get("/api/v1/members").json() if item["id"] == member["id"])
        self.assertGreaterEqual(listed["call_count"], 1)
        self.assertIsNotNone(listed["last_used_at"])

    def test_publisher_can_create_publishing_job_scope_only(self) -> None:
        """发布者能进入发布面，但预检仍受连接器与包状态约束（权限不是绕过审批的通行证）。"""
        publisher = self._member("publisher")
        client = self._client_for(publisher["token"])
        response = client.post("/api/v1/publishing/preflight", json={"package_ids": ["missing"], "options": {}})
        self.assertEqual(response.status_code, 404, "权限通过后仍受业务校验约束（包不存在）")


if __name__ == "__main__":
    unittest.main()
