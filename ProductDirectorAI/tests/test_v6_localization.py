"""V6-02：本地化文案、字幕生成/对齐与离线 TTS 试听。"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tests"))
sys.path.insert(0, str(PROJECT / "apps" / "api"))

import test_job_control as fixtures  # noqa: E402

from productdirector_api import localization  # noqa: E402

main = fixtures.main
FFMPEG = main.FFMPEG or shutil.which("ffmpeg")
ESPEAK = shutil.which("espeak-ng")


class CopyGenerationTests(unittest.TestCase):
    def test_es_mx_copy_separates_facts_and_generated_text(self) -> None:
        payload = localization.build_copy(
            locale="es-MX", product_name="Cámara Demo",
            facts={"verified_dimensions": {"width": 164.0}, "name": "Cámara Demo"},
            hashtag_max_count=3,
        )
        self.assertIn("Cámara Demo", payload["headline"])
        self.assertEqual(payload["facts"]["verified_dimensions"]["width"], 164.0)
        self.assertTrue(payload["generated"]["headline"])
        self.assertEqual(payload["claims_scan"], [])
        self.assertLessEqual(len(payload["hashtags"]), 3)

    def test_prohibited_claims_are_detected_in_spanish_and_english(self) -> None:
        self.assertTrue(localization.find_prohibited_claims("Este producto cura el dolor de espalda"))
        self.assertTrue(localization.find_prohibited_claims("100% garantizado para siempre"))
        self.assertTrue(localization.find_prohibited_claims("FDA approved device"))
        self.assertEqual(localization.find_prohibited_claims("Diseño compacto para uso diario"), [])

    def test_hashtag_validation(self) -> None:
        self.assertEqual(localization.validate_hashtags(["#Producto", "#Diseño"], max_count=5), [])
        problems = localization.validate_hashtags(["#Producto", "#producto", "bad tag", "#ok!"], max_count=3)
        codes = {item["code"] for item in problems}
        self.assertIn("hashtag_duplicate", codes)
        self.assertIn("hashtag_format", codes)
        self.assertIn("hashtag_count", codes)

    def test_unsupported_locale_rejected(self) -> None:
        with self.assertRaises(ValueError):
            localization.build_copy(locale="fr-FR", product_name="X")


class SubtitleTests(unittest.TestCase):
    def test_timeline_cues_are_legal_and_cover_the_video(self) -> None:
        chunks = localization.split_copy_into_chunks(
            "Cámara Demo con acabado mate. Diseño compacto para el uso diario. Descubre más detalles."
        )
        self.assertGreaterEqual(len(chunks), 2)
        cues = localization.cues_from_timeline(chunks, duration_seconds=6.0)
        self.assertAlmostEqual(cues[0]["start_s"], 0.0, places=3)
        self.assertLessEqual(cues[-1]["end_s"], 6.0 + 1e-6)
        self.assertEqual(localization.validate_cues(cues, video_duration_s=6.0), [])

    def test_srt_and_vtt_roundtrip_with_accents(self) -> None:
        cues = [{
            "index": 1, "start_s": 0.5, "end_s": 2.25,
            "text": "Diseño práctico", "lines": ["Diseño práctico"],
        }, {
            "index": 2, "start_s": 2.25, "end_s": 4.0,
            "text": "Uso diario", "lines": ["Uso diario"],
        }]
        srt = localization.render_srt(cues)
        self.assertIn("00:00:00,500 --> 00:00:02,250", srt)
        self.assertIn("Diseño práctico", srt)
        parsed = localization.parse_srt(srt)
        self.assertEqual(len(parsed), 2)
        self.assertAlmostEqual(parsed[1]["start_s"], 2.25, places=3)
        vtt = localization.render_vtt(cues)
        self.assertTrue(vtt.startswith("WEBVTT"))
        self.assertIn("00:00:00.500 --> 00:00:02.250", vtt)

    def test_invalid_cues_are_reported(self) -> None:
        cues = [
            {"index": 1, "start_s": 0.0, "end_s": 3.0, "text": "uno", "lines": ["uno"]},
            {"index": 2, "start_s": 2.0, "end_s": 3.2, "text": "dos", "lines": ["dos"]},      # 重叠
            {"index": 3, "start_s": 3.2, "end_s": 9.0, "text": "tres", "lines": ["tres"]},    # 越界
            {"index": 4, "start_s": 3.3, "end_s": 3.5, "text": "cuatro", "lines": ["cuatro"]},  # 过短
            {"index": 5, "start_s": 3.5, "end_s": 5.0, "text": "cinco",
             "lines": ["línea uno", "línea dos", "línea tres"]},                                # 行数超限
        ]
        codes = {item["code"] for item in localization.validate_cues(cues, video_duration_s=6.0, max_lines=2)}
        self.assertEqual(
            codes, {"cue_overlap", "cue_beyond_video", "cue_too_short", "cue_too_many_lines"},
        )

    def test_wrap_text_respects_line_budget(self) -> None:
        lines = localization.wrap_text(
            "Cámara Demo con acabado mate y líneas limpias para el uso diario",
            max_chars_per_line=20, max_lines=2,
        )
        self.assertLessEqual(len(lines), 2)

    def test_short_speech_cues_are_padded_to_minimum_readable_duration(self) -> None:
        speech = {"available": True, "segments": [(0.0, 0.3), (1.0, 2.0)], "method": "test"}
        cues, report = localization.cues_from_speech(["Uno", "Dos"], speech, duration_seconds=4.0)
        self.assertEqual(report["status"], "ALIGNED")
        self.assertEqual(len(cues), 2)
        self.assertGreaterEqual(cues[0]["end_s"] - cues[0]["start_s"], localization.DEFAULT_MIN_CUE_SECONDS - 1e-6)
        self.assertLessEqual(cues[0]["end_s"], cues[1]["start_s"])  # 不越过下一条
        self.assertEqual(report["padded_short_cues"][0]["cue"], 1)
        self.assertEqual(localization.validate_cues(cues, video_duration_s=4.0), [])

    def test_long_speech_is_not_clamped_to_video_duration(self) -> None:
        speech = {"available": True, "segments": [(0.0, 1.0), (1.2, 3.5)], "method": "test"}
        cues, _ = localization.cues_from_speech(["Uno", "Dos"], speech, duration_seconds=2.0)
        self.assertGreater(cues[-1]["end_s"], 2.0)  # 保持真实音频时间，交由调用方显式报告过长

    @unittest.skipUnless(FFMPEG, "需要 ffmpeg 才能生成真实音频")
    def test_speech_alignment_uses_real_audio_and_reports_fallback(self) -> None:
        audio = main.VAR / "test-voice-alignment.wav"
        subprocess.run(
            [FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
             "sine=frequency=440:duration=0.7", "-f", "lavfi", "-i",
             "anullsrc=r=24000:cl=mono:d=0.6", "-f", "lavfi", "-i",
             "sine=frequency=520:duration=0.7", "-f", "lavfi", "-i",
             "anullsrc=r=24000:cl=mono:d=0.4", "-f", "lavfi", "-i",
             "sine=frequency=600:duration=0.8",
             "-filter_complex", "[0:a][1:a][2:a][3:a][4:a]concat=n=5:v=0:a=1[out]",
             "-map", "[out]", str(audio)],
            check=True, capture_output=True,
        )
        speech = localization.detect_speech_segments(audio, noise_db=-40.0, min_silence_s=0.3)
        self.assertTrue(speech["available"], speech)
        self.assertGreaterEqual(len(speech["segments"]), 2)
        chunks = ["Uno", "Dos", "Tres"]
        cues, report = localization.cues_from_speech(chunks, speech, duration_seconds=3.5)
        self.assertEqual(report["status"], "ALIGNED")
        self.assertEqual(len(cues), 3)
        self.assertEqual(localization.validate_cues(cues, video_duration_s=3.5, min_cue_seconds=0.2), [])
        # 语音段不足时必须回退并说明原因，不能假装已对齐
        cues2, report2 = localization.cues_from_speech(chunks, {"available": True, "segments": [(0.0, 1.0)]},
                                                      duration_seconds=3.5)
        self.assertEqual(cues2, [])
        self.assertEqual(report2["status"], "FALLBACK")


class TtsTests(unittest.TestCase):
    def test_capability_reports_not_configured_when_engine_missing(self) -> None:
        with patch.object(localization.shutil, "which", return_value=None):
            capability = localization.tts_capability()
        self.assertEqual(capability["status"], "NOT_CONFIGURED")
        self.assertIn("授权配音", capability["reason"])

    @unittest.skipUnless(ESPEAK, "需要 espeak-ng 才能合成真实配音")
    def test_synthesize_real_spanish_audio_with_pronunciation_dictionary(self) -> None:
        out = main.VAR / "test-tts-preview.wav"
        result = localization.synthesize(
            "Cámara Demo con diseño práctico", locale="es-MX", out_path=out, rate=1.0,
            pronunciation_dictionary=[{"term": "Demo", "say_as": "Demo"}],
        )
        self.assertTrue(out.exists())
        self.assertGreater(result["duration_s"], 0.3)
        self.assertEqual(result["engine"], "espeak-ng")
        self.assertEqual(result["voice"], "es-419")
        self.assertEqual(result["engine_kind"], "offline_preview")

    def test_pronunciation_dictionary_substitution(self) -> None:
        text, applied = localization.apply_pronunciation(
            "Modelo AAA104 disponible", [{"term": "AAA104", "say_as": "A A A uno cero cuatro"}],
        )
        self.assertIn("A A A uno cero cuatro", text)
        self.assertEqual(applied[0]["term"], "AAA104")


class LocalizationApiTests(unittest.TestCase):
    _create_asset = fixtures.JobControlAcceptanceTests._create_asset
    _create_plan = fixtures.JobControlAcceptanceTests._create_plan

    def setUp(self) -> None:
        fixtures.JobControlAcceptanceTests.setUp(self)
        self.asset = self._create_asset()
        self.plan = self._create_plan()
        self.approved = self.client.post(
            f"/api/v1/plans/{self.plan['id']}/approve", json={"approved": True}
        ).json()
        with patch("productdirector_api.main.execute_job"):
            run = self.client.post(
                "/api/v1/runs",
                json={"plan_id": self.plan["id"], "require_fidelity_snapshot": False},
            )
        self.assertEqual(run.status_code, 202, run.text)
        self.run_id = run.json()["run_id"]

    tearDown = fixtures.JobControlAcceptanceTests.tearDown

    def _create_localization(self, **overrides) -> dict:
        body = {"locale": "es-MX", "profile_id": "tiktok-mx-9x16-esmx", **overrides}
        response = self.client.post(f"/api/v1/runs/{self.run_id}/localizations", json=body)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_localization_creation_and_revision_edit(self) -> None:
        created = self._create_localization()
        localization_id = created["localization_id"]
        self.assertEqual(created["revision"]["revision"], 1)
        self.assertEqual(created["issues"]["claims"], [])
        self.assertFalse(created["revision"]["downstream_invalidated"])
        self.assertIn("local-copy", created["revision"]["generator"])
        self.assertIn("不调用付费 API", created["revision"]["generator"])
        # 事实字段与生成式内容分离
        self.assertIn("facts", created["revision"])
        self.assertTrue(created["revision"]["generated"]["headline"])
        edited = self.client.patch(
            f"/api/v1/localizations/{localization_id}",
            json={"headline": "Cámara Demo · edición humana", "hashtags": ["#Producto", "#Demo"]},
        )
        self.assertEqual(edited.status_code, 200, edited.text)
        body = edited.json()
        self.assertEqual(body["revision"]["revision"], 2)
        self.assertEqual(body["revision"]["edited_from_revision"], 1)
        self.assertTrue(body["downstream_invalidated"])
        detail = self.client.get(f"/api/v1/localizations/{localization_id}").json()
        self.assertEqual([item["revision"] for item in detail["revisions"]], [1, 2])
        self.assertEqual(detail["revisions"][0]["headline"], created["revision"]["headline"])  # 旧版本保留
        self.assertEqual(detail["latest"]["headline"], "Cámara Demo · edición humana")

    def test_claims_scan_flags_edited_copy(self) -> None:
        created = self._create_localization()
        localization_id = created["localization_id"]
        edited = self.client.patch(
            f"/api/v1/localizations/{localization_id}",
            json={"body": "Este producto cura el dolor y está 100% garantizado."},
        ).json()
        categories = {item["category"] for item in edited["issues"]["claims"]}
        self.assertIn("未经证实的健康/医疗功效", categories)
        self.assertIn("未经证实的性能或安全承诺", categories)

    def test_subtitles_timeline_alignment_is_labelled_honestly(self) -> None:
        created = self._create_localization()
        response = self.client.post(
            f"/api/v1/localizations/{created['localization_id']}/subtitles",
            json={"formats": ["srt", "vtt"], "source": "auto"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["alignment"]["status"], "TIMELINE")
        self.assertIn("不是语音精确同步", body["alignment"]["reason"])
        self.assertEqual(body["problems"], [])
        formats = {item["format"] for item in body["files"]}
        self.assertEqual(formats, {"srt", "vtt"})
        srt_file = next(item for item in body["files"] if item["format"] == "srt")
        content = Path(srt_file["path"]).read_text(encoding="utf-8")
        self.assertIn("-->", content)
        self.assertGreater(len(body["cues"]), 0)

    def test_audio_engine_capability_and_preview(self) -> None:
        capability = self.client.get("/api/v1/audio/engines").json()
        self.assertIn(capability["status"], {"AVAILABLE", "NOT_CONFIGURED"})
        self.assertIn("fallbacks", capability)
        created = self._create_localization()
        response = self.client.post(
            "/api/v1/audio/previews",
            json={"localization_id": created["localization_id"], "locale": "es-MX", "rate": 1.0},
        )
        if capability["status"] == "NOT_CONFIGURED":
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["detail"]["code"], "NOT_CONFIGURED")
            return
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertGreater(body["duration_s"], 0.2)
        self.assertEqual(body["engine"], "espeak-ng")
        content = self.client.get(body["download_url"])
        self.assertEqual(content.status_code, 200)
        self.assertGreater(len(content.content), 1000)

    def test_preview_over_length_is_rejected_without_truncation(self) -> None:
        capability = self.client.get("/api/v1/audio/engines").json()
        if capability["status"] != "AVAILABLE":
            self.skipTest("本机无 TTS 引擎")
        response = self.client.post(
            "/api/v1/audio/previews",
            json={"text": "Cámara Demo con diseño práctico para el uso diario", "locale": "es-MX",
                  "max_seconds": 0.5},
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["detail"]["code"], "PREVIEW_TOO_LONG")

    def test_voiceover_upload_then_voice_aligned_subtitles(self) -> None:
        if not FFMPEG:
            self.skipTest("需要 ffmpeg")
        wav = main.VAR / "test-voiceover.wav"
        subprocess.run(
            [FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1.0",
             "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono:d=0.4",
             "-f", "lavfi", "-i", "sine=frequency=520:duration=1.0",
             "-filter_complex", "[0:a][1:a][2:a]concat=n=3:v=0:a=1[out]", "-map", "[out]", str(wav)],
            check=True, capture_output=True,
        )
        with open(wav, "rb") as handle:
            uploaded = self.client.post(
                f"/api/v1/runs/{self.run_id}/voiceovers",
                files={"file": ("voice.wav", handle, "audio/wav")},
                data={"locale": "es-MX", "license_ref": "owner-authorized-2026"},
            )
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        voiceover = uploaded.json()
        self.assertEqual(voiceover["origin"], "owner_upload")
        self.assertGreater(voiceover["duration_s"], 1.5)
        created = self._create_localization()
        subtitles = self.client.post(
            f"/api/v1/localizations/{created['localization_id']}/subtitles",
            json={"formats": ["srt"], "source": "voice", "voiceover_id": voiceover["id"]},
        )
        self.assertEqual(subtitles.status_code, 201, subtitles.text)
        body = subtitles.json()
        self.assertIn(body["alignment"]["status"], {"ALIGNED", "TIMELINE"})
        if body["alignment"]["status"] == "ALIGNED":
            self.assertEqual(body["alignment"]["method"].split("(")[0], "ffmpeg silencedetect")
        self.assertEqual(body["problems"], [])

    def test_localization_rejects_unknown_profile_and_foreign_run(self) -> None:
        response = self.client.post(
            f"/api/v1/runs/{self.run_id}/localizations",
            json={"locale": "es-MX", "profile_id": "no-such-profile"},
        )
        self.assertEqual(response.status_code, 404)
        missing = self.client.get("/api/v1/localizations/does-not-exist")
        self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
