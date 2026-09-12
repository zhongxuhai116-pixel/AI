"""A06 资源与磁盘防护：损坏 GLB、外部资源引用、缺网格与磁盘不足。"""
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

main = fixtures.main
FIXTURE = PROJECT / "tests" / "fixtures" / "generic-product.glb"


def build_glb(document: dict) -> bytes:
    payload = json.dumps(document).encode("utf-8")
    payload += b" " * ((4 - len(payload) % 4) % 4)
    chunks = struct.pack("<II", len(payload), 0x4E4F534A) + payload
    return struct.pack("<4sII", b"glTF", 2, 12 + len(chunks)) + chunks


class AssetGuardTests(unittest.TestCase):
    _create_asset = fixtures.JobControlAcceptanceTests._create_asset
    _create_plan = fixtures.JobControlAcceptanceTests._create_plan

    def setUp(self) -> None:
        fixtures.JobControlAcceptanceTests.setUp(self)

    tearDown = fixtures.JobControlAcceptanceTests.tearDown

    def _upload(self, name: str, data: bytes, mime: str = "model/gltf-binary"):
        return self.client.post("/api/v1/assets", files={"file": (name, data, mime)})

    def test_corrupt_glb_is_rejected_at_upload(self) -> None:
        response = self._upload("broken.glb", b"not-a-glb-at-all")
        self.assertEqual(response.status_code, 422)
        self.assertIn("GLB", response.json()["detail"])

    def test_glb_with_wrong_declared_length_is_rejected(self) -> None:
        data = bytearray(FIXTURE.read_bytes())
        struct.pack_into("<I", data, 8, 999999)
        response = self._upload("mismatch.glb", bytes(data))
        self.assertEqual(response.status_code, 422)
        self.assertIn("不一致", response.json()["detail"])

    def test_glb_with_external_texture_is_rejected(self) -> None:
        document = {
            "asset": {"version": "2.0"},
            "meshes": [{"name": "m"}],
            "images": [{"uri": "texture.png"}],
        }
        response = self._upload("external-texture.glb", build_glb(document))
        self.assertEqual(response.status_code, 422)
        self.assertIn("外部", response.json()["detail"])

    def test_glb_with_external_buffer_is_rejected(self) -> None:
        document = {
            "asset": {"version": "2.0"},
            "meshes": [{"name": "m"}],
            "buffers": [{"uri": "mesh.bin", "byteLength": 4}],
        }
        response = self._upload("external-buffer.glb", build_glb(document))
        self.assertEqual(response.status_code, 422)
        self.assertIn("外部", response.json()["detail"])

    def test_glb_without_meshes_is_rejected(self) -> None:
        response = self._upload("empty.glb", build_glb({"asset": {"version": "2.0"}, "meshes": []}))
        self.assertEqual(response.status_code, 422)
        self.assertIn("网格", response.json()["detail"])

    def test_self_contained_fixture_reports_its_structure(self) -> None:
        response = self._upload("generic-product.glb", FIXTURE.read_bytes())
        self.assertEqual(response.status_code, 201)
        info = response.json()["glb"]
        self.assertEqual(info["version"], 2)
        self.assertGreaterEqual(info["meshes"], 1)
        self.assertEqual(info["images"], 0)
        self.assertFalse(info["has_textures"])

    def test_render_fails_fast_when_disk_space_is_low(self) -> None:
        plan = self._create_plan()
        self.client.post(f"/api/v1/plans/{plan['id']}/approve", json={"approved": True})
        with patch("productdirector_api.main.execute_job"):
            created = self.client.post("/api/v1/runs", json={"plan_id": plan["id"], "idempotency_key": "a06-disk"})
        job_id = created.json()["job_id"]

        with patch.object(main, "MIN_FREE_DISK_MB", 10**9):
            result = main.run_worker_once("worker-disk-guard")

        self.assertTrue(result["claimed"])
        job = self.client.get(f"/api/v1/jobs/{job_id}").json()
        self.assertEqual(job["status"], "FAILED")
        self.assertIn("磁盘空间不足", job["error"])

    def test_disk_guard_allows_render_when_space_is_available(self) -> None:
        self.assertGreater(main.ensure_disk_space(main.RUNS), 0)


if __name__ == "__main__":
    unittest.main()
