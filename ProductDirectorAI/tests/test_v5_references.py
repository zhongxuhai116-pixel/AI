"""V5-01：参考视频上传、代理与时间戳映射、URL 获取约束。"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tests"))
sys.path.insert(0, str(PROJECT / "apps" / "api"))

import test_job_control as fixtures  # noqa: E402

main = fixtures.main
FFMPEG = main.FFMPEG or shutil.which("ffmpeg")


def _make_test_video(path: Path, seconds: int = 2, fps: int = 24, size: str = "320x240") -> None:
    subprocess.run(
        [FFMPEG, "-y", "-v", "error", "-f", "lavfi",
         "-i", f"testsrc=duration={seconds}:size={size}:rate={fps}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True,
    )


@unittest.skipUnless(FFMPEG, "需要 FFmpeg 才能生成真实参考视频")
class V5ReferenceTests(unittest.TestCase):
    _create_asset = fixtures.JobControlAcceptanceTests._create_asset

    def setUp(self) -> None:
        fixtures.JobControlAcceptanceTests.setUp(self)
        self.video = main.VAR / "test-reference-source.mp4"
        _make_test_video(self.video)

    tearDown = fixtures.JobControlAcceptanceTests.tearDown

    def _upload_video_reference(self, path: Path = None) -> dict:
        source = path or self.video
        with open(source, "rb") as handle:
            response = self.client.post(
                "/api/v1/references/upload",
                files={"file": ("reference.mp4", handle, "video/mp4")},
            )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_ingest_uploaded_reference(self) -> None:
        ref = self._upload_video_reference()
        self.assertEqual(ref["status"], "READY", ref)
        self.assertEqual(ref["source_kind"], "uploaded_file")
        self.assertAlmostEqual(ref["payload"]["timebase"]["fps"], 24.0, places=1)
        self.assertEqual(ref["payload"]["timebase"]["estimated_frames"], 48)
        self.assertEqual(ref["source_hash"], hashlib.sha256(self.video.read_bytes()).hexdigest())
        self.assertTrue(ref["proxy_hash"])
        self.assertIn("source_to_proxy_map", ref["payload"])
        proxy = self.client.get(f"/api/v1/references/{ref['id']}/proxy")
        self.assertEqual(proxy.status_code, 200, proxy.text)
        self.assertEqual(proxy.headers["content-type"], "video/mp4")
        listed = self.client.get("/api/v1/references")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()), 1)
        detail = self.client.get(f"/api/v1/references/{ref['id']}")
        self.assertEqual(detail.status_code, 200)

    def test_corrupt_reference_fails(self) -> None:
        corrupt = main.VAR / "corrupt.mp4"
        corrupt.write_bytes(b"not a real video file")
        ref = self._upload_video_reference(corrupt)
        self.assertEqual(ref["status"], "FAILED")
        self.assertIn("无法解析媒体", ref["error"])

    def test_non_video_upload_rejected(self) -> None:
        response = self.client.post(
            "/api/v1/references/upload",
            files={"file": ("notes.txt", b"hello", "text/plain")},
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_source_url_constraints(self) -> None:
        private = self.client.post("/api/v1/references", json={"source_url": "http://127.0.0.1/secret.mp4"})
        self.assertEqual(private.status_code, 201, private.text)
        self.assertEqual(private.json()["status"], "BLOCKED")
        self.assertIn("内网/本机", private.json()["error"])
        bad_scheme = self.client.post("/api/v1/references", json={"source_url": "ftp://example.com/x.mp4"})
        self.assertEqual(bad_scheme.status_code, 201, bad_scheme.text)
        self.assertEqual(bad_scheme.json()["status"], "BLOCKED")
        both = self.client.post(
            "/api/v1/references", json={"uploaded_asset_id": "x", "source_url": "http://example.com/x.mp4"}
        )
        self.assertEqual(both.status_code, 422, both.text)
        neither = self.client.post("/api/v1/references", json={})
        self.assertEqual(neither.status_code, 422, neither.text)


if __name__ == "__main__":
    unittest.main()
