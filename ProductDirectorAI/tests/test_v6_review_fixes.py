"""V6-16 独立复核修复的回归测试（BUG-01/03/04/05/06/07 的服务端部分）。

对应文档：docs/reports/V6_UI_RENDER_BUG_AUDIT_2026-09-13.md
"""
from __future__ import annotations

import json
import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tests"))
sys.path.insert(0, str(PROJECT / "apps" / "api"))

import test_job_control as fixtures  # noqa: E402

from productdirector_api import director_plan  # noqa: E402

main = fixtures.main


def glb_bytes(base_color=(0.22, 0.22, 0.22, 1.0), *, textures=False, uv=False, vertex_color=False,
              materials=1) -> bytes:
    """构造最小 GLB：用于外观覆盖分析（BUG-04）。"""
    primitives = [{"attributes": {"POSITION": 0}}]
    if uv:
        primitives[0]["attributes"]["TEXCOORD_0"] = 1
    if vertex_color:
        primitives[0]["attributes"]["COLOR_0"] = 2
    document = {
        "asset": {"version": "2.0"},
        "meshes": [{"primitives": primitives}],
        "materials": [{"pbrMetallicRoughness": {"baseColorFactor": list(base_color)}} for _ in range(materials)],
        "buffers": [{"byteLength": 4}],
    }
    if textures:
        document["images"] = [{"uri": "data:image/png;base64,AAAA"}]
        document["textures"] = [{"source": 0}]
        # 贴图必须被材质引用才会真正被渲染使用
        document["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"] = {"index": 0}
    json_chunk = json.dumps(document).encode("utf-8")
    json_chunk += b" " * ((4 - len(json_chunk) % 4) % 4)
    total = 12 + 8 + len(json_chunk)
    header = struct.pack("<4sII", b"glTF", 2, total)
    return header + struct.pack("<II", len(json_chunk), 0x4E4F534A) + json_chunk


def document_of(data: bytes) -> dict:
    """从 GLB 字节中取出 JSON 块（供外观分析测试复用）。"""
    length = struct.unpack_from("<I", data, 12)[0]
    return json.loads(data[20:20 + length].decode("utf-8").rstrip("\x00 "))


class AppearanceAnalysisTests(unittest.TestCase):
    def test_single_flat_material_is_reported_as_unverified(self) -> None:
        info = main.analyze_glb_appearance(document_of(glb_bytes()))
        self.assertEqual(info["appearance_coverage"], "SINGLE_FLAT_MATERIAL")
        self.assertEqual(info["appearance_level"], "APPEARANCE_UNVERIFIED")
        self.assertFalse(info["has_textures"])
        self.assertIn("不能", info["appearance_note"])

    def test_textured_or_vertex_colored_models_are_verified_appearance(self) -> None:
        textured = main.analyze_glb_appearance(document_of(glb_bytes(textures=True, uv=True)))
        self.assertEqual(textured["appearance_level"], "VERIFIED_APPEARANCE")
        self.assertTrue(textured["has_uv"])
        colored = main.analyze_glb_appearance(document_of(glb_bytes(vertex_color=True)))
        self.assertEqual(colored["appearance_coverage"], "VERTEX_COLOR")
        self.assertEqual(colored["appearance_level"], "VERIFIED_APPEARANCE")

    def test_multi_material_colors_are_partial(self) -> None:
        document = document_of(glb_bytes(base_color=(0.8, 0.8, 0.9, 1.0), materials=2))
        info = main.analyze_glb_appearance(document)
        self.assertEqual(info["distinct_material_colors"], 1)
        # 两个不同颜色的材质 → 部分覆盖
        document["materials"][1]["pbrMetallicRoughness"]["baseColorFactor"] = [0.1, 0.1, 0.12, 1.0]
        distinct = main.analyze_glb_appearance(document)
        self.assertEqual(distinct["appearance_level"], "PARTIAL_APPEARANCE")
        self.assertEqual(distinct["distinct_material_colors"], 2)

    def test_inspect_glb_includes_appearance_fields(self) -> None:
        info = main.inspect_glb(glb_bytes())
        for key in ("appearance_coverage", "appearance_level", "has_uv", "has_vertex_color",
                    "distinct_material_colors", "honest_boundary"):
            self.assertIn(key, info)


class FidelityReportingTests(unittest.TestCase):
    def _snapshot(self) -> dict:
        return {
            "schema_version": "1.0", "intent": "复核",
            "output": {"width": 540, "height": 960, "fps": 24, "duration_seconds": 6},
            "fidelity_mode": "STRICT_REQUESTED", "product_pose": {"scale": 1},
            "shots": [{"id": "shot_01", "name": "A", "duration_frames": 48, "camera": "static",
                       "focal_length_mm": 35},
                      {"id": "shot_02", "name": "B", "duration_frames": 48, "camera": "side_track",
                       "focal_length_mm": 35},
                      {"id": "shot_03", "name": "C", "duration_frames": 48, "camera": "dolly_in",
                       "focal_length_mm": 35}],
        }

    def test_target_document_reports_executed_mode_not_requested(self) -> None:
        """BUG-05：请求了 STRICT 但没有严格链路时，导出必须是 CONTROLLED + NOT_VERIFIED。"""
        document = director_plan.to_target_document(self._snapshot())
        self.assertEqual(document["schema_version"], "1.1")
        self.assertEqual(document["fidelity"]["requested_mode"], "STRICT_REQUESTED")
        self.assertEqual(document["fidelity"]["executed_mode"], "CONTROLLED")
        self.assertEqual(document["fidelity_mode"], "CONTROLLED")
        self.assertEqual(document["fidelity"]["verification_status"], "NOT_VERIFIED")

    def test_strict_chain_reports_strict_and_verified(self) -> None:
        document = director_plan.to_target_document(self._snapshot(), executed_mode="STRICT",
                                                    verification_status="VERIFIED",
                                                    product_version_id="11111111-1111-4111-8111-111111111111")
        self.assertEqual(document["fidelity_mode"], "STRICT")
        self.assertEqual(document["fidelity"]["verification_status"], "VERIFIED")
        self.assertEqual(document["product_version_binding"], "VERSIONED")

    def test_missing_version_is_null_not_placeholder_uuid(self) -> None:
        document = director_plan.to_target_document(self._snapshot())
        self.assertIsNone(document["product_version_id"], "不得再用占位 UUID")
        self.assertEqual(document["product_version_binding"], "UNKNOWN")
        self.assertNotIn("33333333-3333-4333-8333-333333333333", json.dumps(document))

    def test_v11_schema_validates_new_documents_and_allows_two_to_eight_shots(self) -> None:
        try:
            from jsonschema import Draft202012Validator
        except ImportError:  # pragma: no cover
            self.skipTest("需要 jsonschema")
        validator = Draft202012Validator(director_plan.load_target_schema("1.1"))
        document = director_plan.to_target_document(self._snapshot())
        self.assertEqual(list(validator.iter_errors(document)), [])
        five = self._snapshot()
        five["shots"] = five["shots"] + [
            {"id": "shot_04", "name": "D", "duration_frames": 48, "camera": "static", "focal_length_mm": 35},
            {"id": "shot_05", "name": "E", "duration_frames": 48, "camera": "static", "focal_length_mm": 35},
        ]
        self.assertEqual(list(validator.iter_errors(director_plan.to_target_document(five))), [])
        legacy = Draft202012Validator(director_plan.load_target_schema("1.0"))
        self.assertNotEqual(list(legacy.iter_errors(document)), [], "v1.0 仍拒绝新形状（历史合同）")


class PlanOutputTests(unittest.TestCase):
    _create_asset = fixtures.JobControlAcceptanceTests._create_asset

    def setUp(self) -> None:
        fixtures.JobControlAcceptanceTests.setUp(self)
        self.asset = self._create_asset()
        created = self.client.post("/api/v1/plans/template", json={
            "product_asset_id": self.asset["id"], "intent": "复核输出规格",
            "duration_seconds": 6, "output": {"width": 540, "height": 960, "fps": 24, "duration_seconds": 6},
        })
        self.assertEqual(created.status_code, 201, created.text)
        self.plan = created.json()

    def test_duration_is_respected_at_creation(self) -> None:
        created = self.client.post("/api/v1/plans/template", json={
            "product_asset_id": self.asset["id"], "intent": "5 秒",
            "duration_seconds": 5, "output": {"width": 540, "height": 960, "fps": 24, "duration_seconds": 5},
        })
        self.assertEqual(created.status_code, 201, created.text)
        body = created.json()
        self.assertEqual(sum(shot["duration_frames"] for shot in body["shots"]), 120)
        self.assertEqual(body["output"]["duration_seconds"], 5)

    def test_output_change_is_persisted_and_invalidates_approval(self) -> None:
        """BUG-03：分辨率改动必须写进冻结合同并使审批失效。"""
        self.client.post(f"/api/v1/plans/{self.plan['id']}/approve", json={"approved": True})
        updated = self.client.patch(f"/api/v1/plans/{self.plan['id']}", json={
            "intent": self.plan["intent"], "duration_seconds": 6,
            "shots": self.plan["shots"], "crop_anchor": "center",
            "output": {"width": 1080, "height": 1920, "fps": 24, "duration_seconds": 6},
        })
        self.assertEqual(updated.status_code, 200, updated.text)
        body = updated.json()
        self.assertEqual(body["output"]["width"], 1080)
        self.assertEqual(body["output"]["height"], 1920)
        self.assertTrue(body["output_changed"])
        self.assertFalse(body["approved"], "输出规格变化必须使审批失效")
        detail = self.client.get(f"/api/v1/plans/{self.plan['id']}").json()
        self.assertEqual(detail["payload"]["output"]["width"], 1080)

    def test_output_duration_mismatch_is_rejected(self) -> None:
        rejected = self.client.patch(f"/api/v1/plans/{self.plan['id']}", json={
            "intent": self.plan["intent"], "duration_seconds": 5, "shots": self.plan["shots"],
            "output": {"width": 540, "height": 960, "fps": 24, "duration_seconds": 6},
            "crop_anchor": "center",
        })
        self.assertEqual(rejected.status_code, 422, rejected.text)


class BatchPreviewBindingTests(unittest.TestCase):
    _create_asset = fixtures.JobControlAcceptanceTests._create_asset
    _create_plan = fixtures.JobControlAcceptanceTests._create_plan

    def setUp(self) -> None:
        fixtures.JobControlAcceptanceTests.setUp(self)
        self.asset = self._create_asset()
        self.plan = self._create_plan()
        approved = self.client.post(f"/api/v1/plans/{self.plan['id']}/approve", json={"approved": True})
        self.version_id = approved.json()["product_version_id"]

    def _matrix(self, variations: int = 2) -> dict:
        return {"product_version_ids": [self.version_id], "plan_ids": [self.plan["id"]],
                "profile_ids": ["tiktok-mx-9x16-esmx"], "variations_per_combination": variations}

    def test_preview_returns_hash_and_matching_create_succeeds(self) -> None:
        preview = self.client.post("/api/v1/batches/preview", json={"matrix": self._matrix(), "max_concurrent": 2})
        body = preview.json()
        self.assertIn("preview_hash", body)
        with patch("productdirector_api.main.advance_batch"):
            created = self.client.post("/api/v1/batches", json={
                "matrix": self._matrix(), "max_concurrent": 2, "preview_hash": body["preview_hash"]})
        self.assertEqual(created.status_code, 202, created.text)
        self.assertEqual(created.json()["batch"]["summary"]["total"], 2)

    def test_stale_preview_hash_is_rejected(self) -> None:
        """BUG-06：改了变体数还用旧预览创建 → 409 preview_stale。"""
        preview = self.client.post("/api/v1/batches/preview", json={"matrix": self._matrix(2), "max_concurrent": 2})
        stale_hash = preview.json()["preview_hash"]
        with patch("productdirector_api.main.advance_batch"):
            rejected = self.client.post("/api/v1/batches", json={
                "matrix": self._matrix(3), "max_concurrent": 2, "preview_hash": stale_hash})
        self.assertEqual(rejected.status_code, 409, rejected.text)
        self.assertEqual(rejected.json()["detail"]["code"], "preview_stale")
        self.assertNotEqual(rejected.json()["detail"]["expected_preview_hash"], stale_hash)

    def test_concurrency_change_also_invalidates_preview(self) -> None:
        preview = self.client.post("/api/v1/batches/preview", json={"matrix": self._matrix(), "max_concurrent": 2})
        with patch("productdirector_api.main.advance_batch"):
            rejected = self.client.post("/api/v1/batches", json={
                "matrix": self._matrix(), "max_concurrent": 4,
                "preview_hash": preview.json()["preview_hash"]})
        self.assertEqual(rejected.status_code, 409, rejected.text)

    def test_preview_hash_is_recorded_on_batch_payload(self) -> None:
        preview = self.client.post("/api/v1/batches/preview", json={"matrix": self._matrix(), "max_concurrent": 2})
        with patch("productdirector_api.main.advance_batch"):
            created = self.client.post("/api/v1/batches", json={
                "matrix": self._matrix(), "max_concurrent": 2,
                "preview_hash": preview.json()["preview_hash"]})
        batch_id = created.json()["batch"]["id"]
        with main.connect() as db:
            payload = json.loads(db.execute("SELECT payload FROM batches WHERE id = ?", (batch_id,)).fetchone()["payload"])
        self.assertEqual(payload["preview_hash"], preview.json()["preview_hash"])


class ProductionPlanAppearanceGateTests(unittest.TestCase):
    _create_asset = fixtures.JobControlAcceptanceTests._create_asset

    def setUp(self) -> None:
        fixtures.JobControlAcceptanceTests.setUp(self)
        # 直接落一个真实 GLB（单材质灰模），用于外观门
        self.glb = glb_bytes()
        asset_id = "glb-appearance-1"
        stored = main.UPLOADS / f"{asset_id}.glb"
        stored.write_bytes(self.glb)
        with main.connect() as db:
            db.execute(
                "INSERT INTO assets (id, name, kind, mime, size_bytes, sha256, path, created_at, owner_id) "
                "VALUES (?, 'astronaut.glb', 'model', 'model/gltf-binary', ?, 'sha', ?, ?, ?)",
                (asset_id, len(self.glb), stored.name, main.utc_now(), main.DEFAULT_OWNER_ID))
        self.asset_id = asset_id
        self.shots = [{"name": "场景 1", "camera": "dolly_in", "duration_frames": 36},
                      {"name": "场景 2", "camera": "static", "duration_frames": 36}]

    def _body(self, **overrides) -> dict:
        body = {"product_asset_id": self.asset_id, "profile_id": "tiktok-mx-9x16-esmx",
                "intent": "宇航员台灯 3 秒预演", "shots": self.shots, "locale": "es-MX"}
        body.update(overrides)
        return body

    def test_unverified_appearance_blocks_production_plan_without_explicit_acceptance(self) -> None:
        """BUG-04：外观未核验的模型不能悄悄进入正式生产计划。"""
        response = self.client.post("/api/v1/plans/production", json=self._body())
        self.assertEqual(response.status_code, 409, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "appearance_unverified")
        self.assertEqual(detail["appearance"]["appearance_coverage"], "SINGLE_FLAT_MATERIAL")
        self.assertIn("几何预演", detail["detail"])

    def test_explicit_acceptance_records_status_in_frozen_plan(self) -> None:
        response = self.client.post("/api/v1/plans/production",
                                    json=self._body(accept_unverified_appearance=True))
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["shots"].__len__(), 2)
        self.assertEqual(body["total_frames"], 72)
        self.assertEqual(body["duration_seconds"], 3.0)
        with main.connect() as db:
            payload = json.loads(db.execute("SELECT payload FROM plans WHERE id = ?", (body["id"],)).fetchone()["payload"])
        self.assertEqual(payload["appearance"]["status"], "UNVERIFIED_ACCEPTED")
        self.assertTrue(payload["appearance"]["accepted_by_operator"])

    def test_appearance_endpoint_reports_checks_and_next_steps(self) -> None:
        response = self.client.get(f"/api/v1/assets/{self.asset_id}/appearance")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["appearance_level"], "APPEARANCE_UNVERIFIED")
        self.assertTrue(any("贴图" in item["item"] for item in body["checks"]))
        self.assertTrue(body["next_steps"])
        other_asset = self._create_asset()
        other = self.client.get(f"/api/v1/assets/{other_asset['id']}/appearance").json()
        if other_asset["kind"] == "model":
            self.assertIn(other["appearance_level"],
                          ("APPEARANCE_UNVERIFIED", "PARTIAL_APPEARANCE", "VERIFIED_APPEARANCE"))
        else:
            self.assertEqual(other["appearance_level"], "NOT_APPLICABLE")
            self.assertEqual(other["appearance_coverage"], "IMAGE_SOURCE")


if __name__ == "__main__":
    unittest.main()
