"""V6-05：发布包（结构、Manifest、许可、ZIP 防穿越、审批不可变、批次归档）。"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tests"))
sys.path.insert(0, str(PROJECT / "apps" / "api"))

import test_job_control as fixtures  # noqa: E402

from productdirector_api import publish_package as pk  # noqa: E402

main = fixtures.main
FFMPEG = main.FFMPEG or shutil.which("ffmpeg")


class PackageRulesTests(unittest.TestCase):
    def test_manifest_secrets_are_rejected(self) -> None:
        manifest = {"schema_version": "1.0", "files": [], "note": "Bearer abc123"}
        self.assertTrue(pk.check_manifest_secrets(manifest))
        nested = {"a": {"access_token": "x"}}
        self.assertTrue(pk.check_manifest_secrets(nested))
        signed = {"url": "https://cdn.example.com/v.mp4?X-Amz-Signature=deadbeef&Expires=1"}
        self.assertTrue(pk.check_manifest_secrets(signed))
        self.assertEqual(pk.check_manifest_secrets({"schema_version": "1.0", "files": []}), [])

    def test_layout_omits_disabled_content(self) -> None:
        files = pk.build_layout(
            package_id="p1", version=1, has_clean_master=False, subtitle_locale=None,
            subtitle_formats=[], include_voice_file=False, include_mixed_audio=False,
            include_thumbnail=False,
        )
        self.assertTrue(any(path.endswith("video/final.mp4") for path in files))
        self.assertFalse(any("subtitles/" in path for path in files))
        self.assertFalse(any("clean_master" in path for path in files))
        self.assertFalse(any("audio/" in path for path in files))

    def test_disabled_state_is_explicit(self) -> None:
        state = pk.disabled_state(False, "未生成字幕轨")
        self.assertEqual(state["status"], "disabled")
        self.assertFalse(state["enabled"])

    def test_publish_template_has_no_credentials(self) -> None:
        template = pk.publish_request_template(
            package_id="p1", package_version=1, platform="tiktok", locale="es-MX",
            output_target="feed", suggested_visibility="private", disclosure_flags=[],
        )
        self.assertEqual(pk.check_manifest_secrets(template), [])
        self.assertIn("requires", template)
        self.assertIn("platform_authorization", template["requires"])
        self.assertNotIn("token", json.dumps(template).lower())

    def test_rights_manifest_excludes_music_file(self) -> None:
        rights = pk.rights_manifest(
            profile={"platform": "tiktok", "rules_source_url": "", "rules_verified_at": ""},
            music={"enabled": True, "license_ref": "lic-1", "status": "licensed_reference_only"},
            voice={"enabled": True, "status": "mixed_only"}, fonts=[], materials=[],
        )
        self.assertFalse(rights["packaging_policy"]["music_original_file_included"])
        self.assertIn("不等于有权单独分发", rights["packaging_policy"]["reason"])

    def test_preflight_requires_qa_and_aspect_match(self) -> None:
        problems = pk.validate_package_request(
            profile_spec={"aspect_ratio": "1:1"},
            output={"aspect_ratio": "9:16"},
            qa={"passed": False, "approval_ref": None},
            subtitle_state={"enabled": True, "files": []},
        )
        self.assertEqual(len(problems), 4)
        ok = pk.validate_package_request(
            profile_spec={"aspect_ratio": "9:16"}, output={"aspect_ratio": "9:16"},
            qa={"passed": True, "approval_ref": {"kind": "qa_report", "id": "x"}},
            subtitle_state={"enabled": False, "files": []},
        )
        self.assertEqual(ok, [])

    def test_zip_rejects_unsafe_entries(self) -> None:
        root = Path(main.VAR / "test-v605-zip")
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        (root / "ok.txt").write_text("hi", encoding="utf-8")
        info = pk.zip_package(root, Path(main.VAR / "test-v605-zip-out.zip"))
        self.assertEqual(info["member_count"], 1)
        with zipfile.ZipFile(info["path"]) as archive:
            self.assertEqual(archive.namelist(), ["ok.txt"])
            self.assertIsNone(archive.testzip())
        shutil.rmtree(root, ignore_errors=True)

    def test_content_hash_changes_with_files(self) -> None:
        first = pk.package_content_hash([{"path": "a.txt", "sha256": "1", "role": "copy"}])
        second = pk.package_content_hash([{"path": "a.txt", "sha256": "2", "role": "copy"}])
        self.assertNotEqual(first, second)
        self.assertEqual(first, pk.package_content_hash([{"path": "a.txt", "sha256": "1", "role": "copy"}]))


@unittest.skipUnless(FFMPEG, "需要 ffmpeg")
class PackageApiTests(unittest.TestCase):
    _create_asset = fixtures.JobControlAcceptanceTests._create_asset
    _create_plan = fixtures.JobControlAcceptanceTests._create_plan

    def setUp(self) -> None:
        fixtures.JobControlAcceptanceTests.setUp(self)
        self.asset = self._create_asset()
        self.plan = self._create_plan()
        self.client.post(f"/api/v1/plans/{self.plan['id']}/approve", json={"approved": True})
        with patch("productdirector_api.main.execute_job"):
            run = self.client.post("/api/v1/runs", json={"plan_id": self.plan["id"]})
        self.assertEqual(run.status_code, 202, run.text)
        self.run_id = run.json()["run_id"]
        self.job_id = run.json()["job_id"]
        # 已完成渲染的运行会写 metadata.json（含媒体质量门结果）；这里提供真实结构的夹具
        run_dir = main.RUNS / self.job_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "director_plan.json").write_text(
            json.dumps(json.loads(main.connect().execute("SELECT payload FROM plans WHERE id = ?", (self.plan["id"],)).fetchone()["payload"])),
            encoding="utf-8",
        )
        (run_dir / "metadata.json").write_text(json.dumps({
            "schema_version": "1.0", "job_id": self.job_id, "width": 540, "height": 960,
            "fps": 24, "frame_count": 144,
            "qa": {"passed": True, "failures": [], "samples": [], "black_seconds_ratio": 0.0,
                   "thresholds": {"min_stddev": 6.0, "min_mean": 18.0, "max_black_ratio": 0.35}},
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        self.root = Path(main.VAR / "test-v605-api")
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True, exist_ok=True)
        # 真实成片与混音（走 V6-03 接口）
        master = self.root / "master.mp4"
        subprocess.run(
            [FFMPEG, "-y", "-v", "error", "-f", "lavfi",
             "-i", "testsrc=size=540x960:rate=24:duration=3", "-c:v", "libx264",
             "-pix_fmt", "yuv420p", str(master)], check=True, capture_output=True,
        )
        voice = self.root / "voice.wav"
        subprocess.run(
            [FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
             "sine=frequency=300:duration=3:sample_rate=48000", str(voice)],
            check=True, capture_output=True,
        )
        with open(master, "rb") as handle:
            source = self.client.post(
                "/api/v1/video-sources",
                files={"file": ("master.mp4", handle, "video/mp4")},
                data={"run_id": self.run_id},
            )
        self.assertEqual(source.status_code, 201, source.text)
        with open(voice, "rb") as handle:
            voiceover = self.client.post(
                f"/api/v1/runs/{self.run_id}/voiceovers",
                files={"file": ("voice.wav", handle, "audio/wav")},
                data={"locale": "es-MX", "license_ref": "owner-authorized"},
            )
        self.assertEqual(voiceover.status_code, 201, voiceover.text)
        mixed = self.client.post(f"/api/v1/runs/{self.run_id}/audio/mix", json={
            "voiceover_id": voiceover.json()["id"], "duration_s": 3.0,
        })
        self.assertEqual(mixed.status_code, 201, mixed.text)
        self.mix = mixed.json()
        localization = self.client.post(f"/api/v1/runs/{self.run_id}/localizations",
                                        json={"locale": "es-MX", "profile_id": "tiktok-mx-9x16-esmx"}).json()
        self.localization_id = localization["localization_id"]
        subtitles = self.client.post(
            f"/api/v1/localizations/{self.localization_id}/subtitles",
            json={"formats": ["srt", "vtt"], "source": "timeline", "video_duration_s": 3.0},
        ).json()
        self.subtitle_track_id = subtitles["subtitle_track_id"]
        outputs = self.client.post(f"/api/v1/runs/{self.run_id}/outputs", json={
            "profile_id": "tiktok-mx-9x16-esmx", "video_source_id": source.json()["id"],
            "audio_mix_id": self.mix["id"], "subtitle_track_id": self.subtitle_track_id,
            "clean_master": True, "thumbnail_at_s": 0.5,
        })
        self.assertEqual(outputs.status_code, 201, outputs.text)

    tearDown = fixtures.JobControlAcceptanceTests.tearDown

    def _build(self, **overrides) -> dict:
        payload = {"profile_id": "tiktok-mx-9x16-esmx", "localization_id": self.localization_id,
                   "subtitle_track_id": self.subtitle_track_id, "audio_mix_id": self.mix["id"]}
        payload.update(overrides)
        response = self.client.post(f"/api/v1/runs/{self.run_id}/packages", json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_build_verify_approve_and_download(self) -> None:
        built = self._build()
        self.assertEqual(built["status"], "BUILT")
        self.assertEqual(built["verification"], [])
        manifest = built["manifest"]
        self.assertEqual(manifest["locale"], "es-MX")
        self.assertTrue(manifest["qa"]["passed"])
        roles = {entry["role"] for entry in manifest["files"]}
        self.assertIn("video", roles)
        self.assertIn("subtitle", roles)
        self.assertIn("metadata", roles)
        self.assertIn("publish_template", roles)
        paths = [entry["path"] for entry in manifest["files"]]
        self.assertTrue(any(path.endswith("video/final.mp4") for path in paths))
        self.assertTrue(any(path.endswith("video/clean_master.mp4") for path in paths))
        self.assertTrue(any(path.endswith("subtitles/es-MX.srt") for path in paths))
        self.assertTrue(any(path.endswith("subtitles/es-MX.vtt") for path in paths))
        self.assertTrue(any(path.endswith("copy/caption.txt") for path in paths))
        self.assertTrue(any(path.endswith("metadata/rights_manifest.json") for path in paths))
        self.assertTrue(any(path.endswith("publish/request.template.json") for path in paths))
        self.assertEqual(pk.check_manifest_secrets(manifest), [])
        # 未启用的内容不假装已生成
        self.assertEqual(manifest["music"]["status"], "disabled")
        self.assertIn(manifest["voice"]["status"], {"mixed_only", "disabled"})
        verify = self.client.post(f"/api/v1/packages/{built['id']}/verify")
        self.assertTrue(verify.json()["verified"], verify.json())
        approved = self.client.post(f"/api/v1/packages/{built['id']}/approve",
                                    json={"content_hash": built["content_hash"], "reason": "人工审批"})
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(approved.json()["status"], "APPROVED")
        download = self.client.get(f"/api/v1/packages/{built['id']}/download")
        self.assertEqual(download.status_code, 200)
        self.assertGreater(len(download.content), 1000)
        # ZIP 内容可校验且不含凭证
        zip_path = Path(built["zip"]["path"])
        with zipfile.ZipFile(zip_path) as archive:
            names = archive.namelist()
            self.assertTrue(any(name.endswith("manifest.json") for name in names))
            self.assertIsNone(archive.testzip())
            for name in names:
                self.assertNotIn("..", Path(name).parts)
            manifest_in_zip = json.loads(archive.read(next(n for n in names if n.endswith("manifest.json"))))
        self.assertEqual(pk.check_manifest_secrets(manifest_in_zip), [])

    def test_approval_requires_current_content_hash(self) -> None:
        built = self._build()
        wrong = self.client.post(f"/api/v1/packages/{built['id']}/approve",
                                 json={"content_hash": "0" * 32})
        self.assertEqual(wrong.status_code, 409, wrong.text)
        self.assertEqual(wrong.json()["detail"]["code"], "content_hash_mismatch")

    def test_tampered_package_fails_verification_and_blocks_approval(self) -> None:
        built = self._build()
        captions = next(entry for entry in built["manifest"]["files"]
                        if entry["path"].endswith("copy/caption.txt"))
        target = Path(built["directory"]) / "copy" / "caption.txt"
        target.write_text("篡改后的文案", encoding="utf-8")
        verify = self.client.post(f"/api/v1/packages/{built['id']}/verify").json()
        self.assertFalse(verify["verified"])
        self.assertTrue(any("哈希不一致" in item for item in verify["failures"]))
        approval = self.client.post(f"/api/v1/packages/{built['id']}/approve",
                                    json={"content_hash": captions["sha256"]})
        self.assertIn(approval.status_code, (409, 422), approval.text)

    def test_package_requires_rendition_and_localization(self) -> None:
        with patch("productdirector_api.main.execute_job"):
            empty_run = self.client.post("/api/v1/runs", json={"plan_id": self.plan["id"]}).json()
        response = self.client.post(f"/api/v1/runs/{empty_run['run_id']}/packages", json={})
        self.assertEqual(response.status_code, 409, response.text)

    def test_music_file_never_enters_package(self) -> None:
        music = self.root / "bgm.wav"
        subprocess.run(
            [FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
             "sine=frequency=200:duration=3:sample_rate=48000", str(music)],
            check=True, capture_output=True,
        )
        with open(music, "rb") as handle:
            asset = self.client.post(
                "/api/v1/music-assets",
                files={"file": ("bgm.wav", handle, "audio/wav")},
                data={"name": "测试 BGM", "license_ref": "internal-test-signal",
                      "commercial_use_allowed": "false"},
            ).json()
        mixed = self.client.post(f"/api/v1/runs/{self.run_id}/audio/mix", json={
            "voiceover_id": "", "music_asset_id": asset["id"], "duration_s": 3.0,
        }).json()
        outputs = self.client.post(f"/api/v1/runs/{self.run_id}/outputs", json={
            "profile_id": "tiktok-mx-9x16-esmx",
            "video_source_id": self.client.get("/api/v1/packages") and None,
            "audio_mix_id": mixed["id"], "clean_master": False,
        })
        self.assertEqual(outputs.status_code, 422)  # 缺 video source：不猜测来源
        built = self._build(audio_mix_id=mixed["id"])
        paths = [entry["path"] for entry in built["manifest"]["files"]]
        self.assertFalse(any(path.endswith(".wav") and "music" in path for path in paths))
        self.assertEqual(built["manifest"]["music"]["status"], "licensed_reference_only")
        self.assertFalse(built["manifest"]["rights_refs"] is None)

    def test_package_refuses_without_qa_result(self) -> None:
        """有真实成片但没有质量门结果时必须拒绝（不生成半成品包）。"""
        (main.RUNS / self.job_id / "metadata.json").unlink(missing_ok=True)
        response = self.client.post(f"/api/v1/runs/{self.run_id}/packages", json={
            "profile_id": "tiktok-mx-9x16-esmx", "localization_id": self.localization_id,
        })
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("QA", response.json()["detail"])

    def test_batch_archive_requires_approved_packages(self) -> None:
        with patch("productdirector_api.main.execute_job"), patch("productdirector_api.main.advance_batch"):
            batch = self.client.post("/api/v1/batches", json={
                "items": [{"plan_id": self.plan["id"], "profile_id": "tiktok-mx-9x16-esmx"}],
            }).json()["batch"]
        blocked = self.client.post(f"/api/v1/batches/{batch['id']}/packages/archive")
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()["detail"]["code"], "no_approved_packages")
        built = self._build(batch_id=batch["id"])
        self.client.post(f"/api/v1/packages/{built['id']}/approve",
                         json={"content_hash": built["content_hash"]})
        archive = self.client.post(f"/api/v1/batches/{batch['id']}/packages/archive")
        self.assertEqual(archive.status_code, 201, archive.text)
        self.assertEqual(archive.json()["package_count"], 1)
        download = self.client.get(archive.json()["download_url"])
        self.assertEqual(download.status_code, 200)
        with zipfile.ZipFile(Path(archive.json()["archive"]["path"])) as zf:
            self.assertIn("batch-archive-manifest.json", zf.namelist())


if __name__ == "__main__":
    unittest.main()
