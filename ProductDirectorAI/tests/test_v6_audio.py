"""V6-03：BGM、ducking 混音、响度测量、时长适配与编码输出。"""
from __future__ import annotations

import copy
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

from productdirector_api import audio_post  # noqa: E402

main = fixtures.main
FFMPEG = main.FFMPEG or shutil.which("ffmpeg")


def make_tone(path: Path, *, frequency: int, seconds: float, volume: float = 0.4) -> None:
    subprocess.run(
        [FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
         f"sine=frequency={frequency}:duration={seconds}:sample_rate=48000",
         "-af", f"volume={volume}", str(path)],
        check=True, capture_output=True,
    )


def make_speech_like(path: Path, *, speech_seconds=(0.6, 1.6), total_seconds: float = 4.0) -> None:
    """构造"语音段 + 静音"的测试信号（用于验证 ducking 只在语音段压低音乐）。"""
    subprocess.run(
        [FFMPEG, "-y", "-v", "error",
         "-f", "lavfi", "-i", f"sine=frequency=300:duration={speech_seconds[1] - speech_seconds[0]}:sample_rate=48000",
         "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=mono:d={total_seconds}",
         "-filter_complex",
         f"[0:a]adelay={int(speech_seconds[0] * 1000)}|{int(speech_seconds[0] * 1000)},"
         f"apad=whole_dur={total_seconds}[speech];[1:a][speech]amix=inputs=2:duration=first:normalize=0[out]",
         "-map", "[out]", str(path)],
        check=True, capture_output=True,
    )


@unittest.skipUnless(FFMPEG, "需要 ffmpeg")
class AudioPostTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path(main.VAR / "test-v603")
        if self._tmp.exists():
            shutil.rmtree(self._tmp)
        self._tmp.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_loudness_measurement_and_verdict(self) -> None:
        quiet = self._tmp / "quiet.wav"
        make_tone(quiet, frequency=440, seconds=3.0, volume=0.05)
        measurement = audio_post.measure_loudness(quiet)
        self.assertTrue(measurement["available"], measurement)
        self.assertLess(measurement["integrated_lufs"], -20)
        verdict = audio_post.loudness_verdict(measurement)
        self.assertFalse(verdict["passed"])
        self.assertTrue(any("综合响度" in item for item in verdict["failures"]))

        loud = self._tmp / "loud.wav"
        make_tone(loud, frequency=440, seconds=3.0, volume=0.9)
        loud_verdict = audio_post.loudness_verdict(audio_post.measure_loudness(loud))
        self.assertFalse(loud_verdict["passed"])

    def test_mix_reaches_loudness_target_and_peak_limit(self) -> None:
        voice = self._tmp / "voice.wav"
        music = self._tmp / "music.wav"
        make_tone(voice, frequency=320, seconds=5.0, volume=0.5)
        make_tone(music, frequency=180, seconds=2.0, volume=0.5)  # 短于视频 → 需要循环
        out = self._tmp / "mix.wav"
        result = audio_post.mix_tracks(voice_path=voice, music_path=music, out_path=out,
                                      duration_s=5.0, music_gain_db=-12.0,
                                      ducking={"enabled": True})
        self.assertTrue(out.exists())
        verdict = result["verdict"]
        self.assertTrue(verdict["passed"], verdict["failures"])
        self.assertLessEqual(abs(verdict["deviation_lu"]), verdict["tolerance_lu"])
        self.assertLessEqual(result["measurement"]["true_peak_dbtp"], -1.0 + 0.05)
        self.assertEqual(result["ducking"]["enabled"], True)
        self.assertEqual(result["ducking"]["threshold_source"], "auto_from_voice_level")
        self.assertIn("响度归一生效后绝对电平会被拉回目标", result["ducking"]["measurement_note"])
        self.assertIsNotNone(result["ducking"]["voice_mean_level_db"])

    def test_ducking_actually_lowers_music_during_speech(self) -> None:
        voice = self._tmp / "speech.wav"
        music = self._tmp / "bed.wav"
        make_speech_like(voice, speech_seconds=(1.0, 3.0), total_seconds=4.0)
        make_tone(music, frequency=200, seconds=4.0, volume=0.6)
        ducked = self._tmp / "mix-ducked.wav"
        plain = self._tmp / "mix-plain.wav"
        common = dict(voice_path=voice, music_path=music, duration_s=4.0, music_gain_db=-6.0,
                      target_lufs=-16.0)
        audio_post.mix_tracks(out_path=ducked, ducking={"enabled": True}, **common)
        audio_post.mix_tracks(out_path=plain, ducking={"enabled": False}, **common)
        # 响度归一在 ducking 之后执行，绝对电平会被拉回目标：因此比较**音乐/旁白能量比**
        # （200Hz 为 BGM 频段、300Hz 为旁白频段），这才是 ducking 的作用所在。
        def ratio(path):
            music = audio_post.measure_band_level(path, centre_hz=200, bandwidth_hz=60)["mean_volume_db"]
            voice = audio_post.measure_band_level(path, centre_hz=300, bandwidth_hz=60)["mean_volume_db"]
            return music - voice

        ducked_ratio = ratio(ducked)
        plain_ratio = ratio(plain)
        self.assertLess(ducked_ratio, plain_ratio - 1.0,
                        f"语音段的音乐/旁白能量比未被压低：ducked={ducked_ratio:.2f} plain={plain_ratio:.2f}")
        # 语音之前的纯音乐段落不应被明显压低（同一 200Hz 频段比较）
        ducked_intro = audio_post.measure_band_level(ducked, centre_hz=200, bandwidth_hz=60)["mean_volume_db"]
        plain_intro = audio_post.measure_band_level(plain, centre_hz=200, bandwidth_hz=60)["mean_volume_db"]
        self.assertLess(abs(plain_intro - ducked_intro), 2.0,
                        f"纯音乐段落被过度压低：{plain_intro - ducked_intro:.2f} dB")

    def test_verify_ducking_measures_branch_reduction(self) -> None:
        voice = self._tmp / "speech2.wav"
        music = self._tmp / "bed2.wav"
        make_speech_like(voice, speech_seconds=(0.8, 3.2), total_seconds=4.0)
        make_tone(music, frequency=220, seconds=4.0, volume=0.6)
        report = audio_post.verify_ducking(
            voice_path=voice, music_path=music, duration_s=4.0, music_gain_db=-6.0,
            ducking={"enabled": True, "ratio": 8.0}, workdir=self._tmp / "verify",
        )
        self.assertTrue(report["verified"], report)
        self.assertGreater(report["measurement"]["ducked"]["drop_db"], 1.0)
        self.assertLess(abs(report["measurement"]["plain"]["drop_db"]), 1.0)
        self.assertEqual(report["ducking"]["threshold_source"], "auto_from_voice_level")

    def test_single_track_paths_and_switch_semantics(self) -> None:
        voice = self._tmp / "v.wav"
        music = self._tmp / "m.wav"
        make_tone(voice, frequency=300, seconds=3.0)
        make_tone(music, frequency=200, seconds=3.0)
        voice_only = self._tmp / "voice-only.wav"
        music_only = self._tmp / "music-only.wav"
        audio_post.mix_tracks(voice_path=voice, music_path=None, out_path=voice_only, duration_s=3.0)
        audio_post.mix_tracks(voice_path=None, music_path=music, out_path=music_only, duration_s=3.0)
        voice_probe = audio_post.probe_audio(voice_only)
        music_probe = audio_post.probe_audio(music_only)
        self.assertTrue(voice_probe["has_audio"] and music_probe["has_audio"])
        # 两种混音都被响应归一化到同一目标响度，因此按频段区分（旁白 300Hz / BGM 200Hz）
        voice_band = audio_post.measure_band_level(voice_only, centre_hz=300, bandwidth_hz=60)
        music_band = audio_post.measure_band_level(music_only, centre_hz=300, bandwidth_hz=60)
        self.assertGreater(voice_band["mean_volume_db"] - music_band["mean_volume_db"], 6.0)
        music_in_music_band = audio_post.measure_band_level(music_only, centre_hz=200, bandwidth_hz=60)
        voice_in_music_band = audio_post.measure_band_level(voice_only, centre_hz=200, bandwidth_hz=60)
        self.assertGreater(music_in_music_band["mean_volume_db"] - voice_in_music_band["mean_volume_db"], 6.0)
        with self.assertRaises(ValueError):
            audio_post.mix_tracks(voice_path=None, music_path=None, out_path=self._tmp / "x.wav", duration_s=1.0)

    def test_fade_out_reduces_tail_level(self) -> None:
        music = self._tmp / "m2.wav"
        make_tone(music, frequency=220, seconds=5.0, volume=0.6)
        out = self._tmp / "faded.wav"
        audio_post.mix_tracks(voice_path=None, music_path=music, out_path=out, duration_s=5.0,
                              music_gain_db=0.0, fade_in_s=0.0, fade_out_s=1.5, loop_music=False)
        head = audio_post.measure_window_level(out, start_s=0.2, duration_s=0.6)
        tail = audio_post.measure_window_level(out, start_s=4.3, duration_s=0.5)
        self.assertLess(tail["mean_volume_db"], head["mean_volume_db"] - 3.0)


class DurationAdaptationTests(unittest.TestCase):
    def _plan(self) -> dict:
        return {
            "intent": "适配测试",
            "from_reference": {"analysis_id": "a", "reference_id": "b"},
            "output": {"width": 540, "height": 960, "fps": 24, "duration_seconds": 5.0, "frame_count": 120},
            "shots": [
                {"id": "shot_01", "name": "接触", "duration_frames": 48, "camera": "static", "focal_length_mm": 35},
                {"id": "shot_02", "name": "展示", "duration_frames": 36, "camera": "static", "focal_length_mm": 35},
                {"id": "shot_03", "name": "收尾", "duration_frames": 36, "camera": "static", "focal_length_mm": 35},
            ],
        }

    def test_extend_only_non_contact_shots(self) -> None:
        decision = audio_post.adapt_plan_duration(
            self._plan(), audio_duration_s=5.5, policy="extend_non_contact_shot",
            extend_max_frames=24, interaction_plan={"action": "press_button", "shot_id": "shot_01"},
        )
        self.assertEqual(decision["result"], "EXTENDED", decision["failures"])
        self.assertIn("shot_01", decision["contact_shots_untouched"])
        changed = {item["shot_id"]: item["added_frames"] for item in decision["changes"]}
        self.assertNotIn("shot_01", changed)
        self.assertEqual(sum(changed.values()), 12)
        adapted = decision["adapted_plan"]
        self.assertEqual(adapted["shots"][0]["duration_frames"], 48)  # 接触镜头未变
        self.assertEqual(adapted["output"]["frame_count"], 132)
        self.assertIn("重新生成映射", decision["note"])

    def test_extend_beyond_limit_is_rejected(self) -> None:
        decision = audio_post.adapt_plan_duration(
            self._plan(), audio_duration_s=7.0, policy="extend_non_contact_shot", extend_max_frames=12,
        )
        self.assertEqual(decision["result"], "REJECTED")
        self.assertTrue(any("超过上限" in item for item in decision["failures"]))

    def test_extend_beyond_profile_max_is_rejected(self) -> None:
        decision = audio_post.adapt_plan_duration(
            self._plan(), audio_duration_s=5.5, policy="extend_non_contact_shot",
            extend_max_frames=48, profile_max_seconds=5.2,
        )
        self.assertEqual(decision["result"], "REJECTED")
        self.assertTrue(any("超出 Profile 上限" in item for item in decision["failures"]))

    def test_speech_rate_within_range_and_beyond(self) -> None:
        ok = audio_post.adapt_plan_duration(
            self._plan(), audio_duration_s=5.2, policy="adjust_speech_rate", rate_range=(0.9, 1.1),
        )
        self.assertEqual(ok["result"], "SPEECH_RATE")
        self.assertAlmostEqual(ok["new_rate"], 1.04, places=3)
        bad = audio_post.adapt_plan_duration(
            self._plan(), audio_duration_s=7.0, policy="adjust_speech_rate", rate_range=(0.9, 1.1),
        )
        self.assertEqual(bad["result"], "REJECTED")

    def test_rewrite_and_already_fits(self) -> None:
        fits = audio_post.adapt_plan_duration(self._plan(), audio_duration_s=4.0, policy="rewrite_copy")
        self.assertEqual(fits["result"], "ALREADY_FITS")
        rewrite = audio_post.adapt_plan_duration(self._plan(), audio_duration_s=5.5, policy="rewrite_copy")
        self.assertEqual(rewrite["result"], "REQUIRES_REWRITE")
        self.assertNotIn("adapted_plan", rewrite)


@unittest.skipUnless(FFMPEG, "需要 ffmpeg")
class EncodingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(main.VAR / "test-v603-encode")
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True, exist_ok=True)
        self.video = self.root / "master.mp4"
        subprocess.run(
            [FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=540x960:rate=24:duration=2",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(self.video)],
            check=True, capture_output=True,
        )
        self.audio = self.root / "mix.wav"
        make_tone(self.audio, frequency=300, seconds=2.0, volume=0.5)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_encode_with_audio_and_spec_check(self) -> None:
        out = self.root / "final.mp4"
        report = audio_post.encode_rendition(
            video_path=self.video, audio_path=self.audio, out_path=out,
            width=1080, height=1920, fps=24,
        )
        self.assertTrue(report["passed"], report["failures"])
        self.assertTrue(report["has_audio"])
        self.assertEqual((report["width"], report["height"]), (1080, 1920))
        self.assertGreater(report["size_bytes"], 1000)

    def test_encode_without_audio_declares_no_audio_stream(self) -> None:
        out = self.root / "silent.mp4"
        report = audio_post.encode_rendition(
            video_path=self.video, audio_path=None, out_path=out, width=540, height=960, fps=24,
        )
        self.assertTrue(report["passed"], report["failures"])
        self.assertFalse(report["has_audio"])

    def test_letterbox_and_crop_policies_for_aspect_change(self) -> None:
        wide = self.root / "wide.mp4"
        subprocess.run(
            [FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=960x540:rate=24:duration=1",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(wide)], check=True, capture_output=True,
        )
        letterbox = audio_post.encode_rendition(
            video_path=wide, audio_path=None, out_path=self.root / "letterbox.mp4",
            width=540, height=960, fps=24, crop_policy="letterbox",
            source_size={"width": 960, "height": 540},
        )
        self.assertTrue(letterbox["passed"], letterbox["failures"])
        self.assertEqual(letterbox["geometry"]["strategy"], "letterbox_pad")
        self.assertEqual((letterbox["width"], letterbox["height"]), (540, 960))
        cropped = audio_post.encode_rendition(
            video_path=wide, audio_path=None, out_path=self.root / "crop.mp4",
            width=540, height=960, fps=24, crop_policy="crop_if_safe",
            source_size={"width": 960, "height": 540},
        )
        self.assertEqual(cropped["geometry"]["strategy"], "center_crop")
        self.assertTrue(cropped["passed"], cropped["failures"])
        with self.assertRaises(ValueError):
            audio_post.encode_rendition(
                video_path=wide, audio_path=None, out_path=self.root / "bad.mp4",
                width=540, height=960, fps=24, crop_policy="reframe_3d",
                source_size={"width": 960, "height": 540},
            )

    def test_thumbnail_is_a_real_frame(self) -> None:
        out = self.root / "thumb.jpg"
        report = audio_post.make_thumbnail(self.video, out, at_seconds=0.5)
        self.assertTrue(out.exists())
        self.assertGreater(report["size_bytes"], 500)


class AudioApiTests(unittest.TestCase):
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
        self.root = Path(main.VAR / "test-v603-api")
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True, exist_ok=True)

    tearDown = fixtures.JobControlAcceptanceTests.tearDown

    def _upload(self, path: Path, endpoint: str, data: dict, filename: str):
        with open(path, "rb") as handle:
            return self.client.post(endpoint, files={"file": (filename, handle, "application/octet-stream")}, data=data)

    def _voiceover(self, seconds: float = 3.0) -> dict:
        voice = self.root / f"voice-{seconds}.wav"
        make_tone(voice, frequency=300, seconds=seconds, volume=0.5)
        response = self._upload(voice, f"/api/v1/runs/{self.run_id}/voiceovers",
                                {"locale": "es-MX", "license_ref": "owner-authorized"}, "voice.wav")
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def _music(self, *, commercial: bool = False, seconds: float = 4.0) -> dict:
        music = self.root / "music.wav"
        make_tone(music, frequency=200, seconds=seconds, volume=0.4)
        response = self._upload(music, "/api/v1/music-assets",
                                {"name": "测试音乐床", "license_ref": "internal-test-signal",
                                 "commercial_use_allowed": str(commercial).lower()}, "music.wav")
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_music_upload_requires_license(self) -> None:
        music = self.root / "m.wav"
        make_tone(music, frequency=200, seconds=1.0)
        response = self._upload(music, "/api/v1/music-assets", {"name": "x", "license_ref": ""}, "m.wav")
        self.assertEqual(response.status_code, 422, response.text)
        created = self._music()
        listed = self.client.get("/api/v1/music-assets").json()
        self.assertIn(created["id"], {item["id"] for item in listed})
        content = self.client.get(created["download_url"])
        self.assertEqual(content.status_code, 200)

    def test_mix_requires_at_least_one_track_and_reports_loudness(self) -> None:
        empty = self.client.post(f"/api/v1/runs/{self.run_id}/audio/mix", json={})
        self.assertEqual(empty.status_code, 422)
        voiceover = self._voiceover()
        music = self._music()
        mixed = self.client.post(f"/api/v1/runs/{self.run_id}/audio/mix", json={
            "voiceover_id": voiceover["id"], "music_asset_id": music["id"],
            "duration_s": 5.0, "music_gain_db": -12.0, "ducking": {"enabled": True, "threshold_db": -20.0},
        })
        self.assertEqual(mixed.status_code, 201, mixed.text)
        body = mixed.json()
        self.assertTrue(body["verdict"]["passed"], body["verdict"]["failures"])
        self.assertEqual(body["ducking"]["sidechain"], "voice")
        self.assertTrue(body["music"]["enabled"] and body["voice"]["enabled"])
        self.assertEqual(self.client.get(body["download_url"]).status_code, 200)

    def test_commercial_profile_rejects_noncommercial_music(self) -> None:
        voiceover = self._voiceover()
        music = self._music(commercial=False)
        response = self.client.post(f"/api/v1/runs/{self.run_id}/audio/mix", json={
            "voiceover_id": voiceover["id"], "music_asset_id": music["id"],
            "profile_id": "facebook-ads-1x1-esmx", "duration_s": 5.0,
        })
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["detail"]["code"], "MUSIC_NOT_COMMERCIAL")
        allowed = self.client.post(f"/api/v1/runs/{self.run_id}/audio/mix", json={
            "voiceover_id": voiceover["id"], "music_asset_id": self._music(commercial=True)["id"],
            "profile_id": "facebook-ads-1x1-esmx", "duration_s": 5.0,
        })
        self.assertEqual(allowed.status_code, 201, allowed.text)

    def test_explicit_empty_track_ids_disable_tracks(self) -> None:
        """显式空字符串表示"关闭该音轨"；缺省（None）才自动取最新配音。"""
        voiceover = self._voiceover()
        music = self._music()
        music_only = self.client.post(f"/api/v1/runs/{self.run_id}/audio/mix", json={
            "voiceover_id": "", "music_asset_id": music["id"], "duration_s": 2.0,
        })
        self.assertEqual(music_only.status_code, 201, music_only.text)
        self.assertFalse(music_only.json()["voice"]["enabled"])
        self.assertTrue(music_only.json()["music"]["enabled"])
        voice_only = self.client.post(f"/api/v1/runs/{self.run_id}/audio/mix", json={
            "voiceover_id": voiceover["id"], "music_asset_id": "", "duration_s": 2.0,
        })
        self.assertEqual(voice_only.status_code, 201, voice_only.text)
        self.assertTrue(voice_only.json()["voice"]["enabled"])
        self.assertFalse(voice_only.json()["music"]["enabled"])
        both_disabled = self.client.post(f"/api/v1/runs/{self.run_id}/audio/mix", json={
            "voiceover_id": "", "music_asset_id": "", "duration_s": 2.0,
        })
        self.assertEqual(both_disabled.status_code, 422)

    def test_adaptation_endpoint_records_decision_and_constraints(self) -> None:
        voiceover = self._voiceover(seconds=7.5)
        response = self.client.post(f"/api/v1/runs/{self.run_id}/audio/adapt", json={
            "policy": "extend_non_contact_shot", "voiceover_id": voiceover["id"],
            "profile_id": "tiktok-mx-9x16-esmx", "preset_id": "es-mx-subtitled",
        })
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertIn(body["decision"]["result"], {"EXTENDED", "REJECTED", "ALREADY_FITS", "REQUIRES_REWRITE"})
        self.assertEqual(body["constraints"]["contact_shots_must_not_change"], True)
        self.assertEqual(body["constraints"]["no_truncation_of_video_or_voice"], True)
        self.assertIn("extend_non_contact_shot", body["allowed_policies"])
        rewrite = self.client.post(f"/api/v1/runs/{self.run_id}/audio/adapt", json={
            "policy": "rewrite_copy", "voiceover_id": voiceover["id"],
        })
        self.assertEqual(rewrite.json()["decision"]["result"], "REQUIRES_REWRITE")

    def test_render_outputs_with_mix_burn_in_clean_master_and_thumbnail(self) -> None:
        # 真实视频来源（Master）
        master = self.root / "master.mp4"
        subprocess.run(
            [FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=540x960:rate=24:duration=2.5",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(master)], check=True, capture_output=True,
        )
        source = self._upload(master, "/api/v1/video-sources",
                              {"run_id": self.run_id, "name": "V6-03 测试 Master"}, "master.mp4")
        self.assertEqual(source.status_code, 201, source.text)
        voiceover = self._voiceover(seconds=2.5)
        mixed = self.client.post(f"/api/v1/runs/{self.run_id}/audio/mix", json={
            "voiceover_id": voiceover["id"], "duration_s": 2.5,
        }).json()
        localization = self.client.post(f"/api/v1/runs/{self.run_id}/localizations",
                                        json={"locale": "es-MX"}).json()
        subtitles = self.client.post(
            f"/api/v1/localizations/{localization['localization_id']}/subtitles",
            json={"formats": ["srt"], "source": "timeline", "video_duration_s": 2.5},
        ).json()
        response = self.client.post(f"/api/v1/runs/{self.run_id}/outputs", json={
            "profile_id": "tiktok-mx-9x16-esmx", "video_source_id": source.json()["id"],
            "audio_mix_id": mixed["id"], "subtitle_track_id": subtitles["subtitle_track_id"],
            "clean_master": True, "thumbnail_at_s": 0.5,
        })
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        kinds = {item["kind"] for item in body["renditions"]}
        self.assertEqual(kinds, {"final", "clean_master", "thumbnail"})
        self.assertTrue(body["final"]["report"]["passed"], body["final"]["report"]["failures"])
        self.assertTrue(body["final"]["report"]["has_audio"])
        self.assertTrue(body["final"]["burned_subtitles"])
        self.assertFalse(body["clean_master"]["burned_subtitles"])
        self.assertNotEqual(body["final"]["path"], body["clean_master"]["path"])
        # 来源链完整
        self.assertEqual(body["final"]["video_source_sha256"], source.json()["sha256"])
        self.assertEqual(body["final"]["audio_mix_sha256"], mixed["sha256"])
        self.assertEqual(body["final"]["profile_version"], 1)
        self.assertIsNotNone(body["final"]["profile_payload_sha256"])
        self.assertTrue(body["final_loudness"]["available"])
        self.assertTrue(body["final_loudness_verdict"]["passed"], body["final_loudness_verdict"]["failures"])
        # 无字幕 Master 与成片都可下载
        self.assertEqual(self.client.get(body["final"]["download_url"]
                                         if "download_url" in body["final"]
                                         else f"/api/v1/outputs/{body['final']['id']}/content").status_code, 200)
        thumb = self.client.get(f"/api/v1/outputs/{body['thumbnail']['id']}/content")
        self.assertEqual(thumb.status_code, 200)
        self.assertGreater(len(thumb.content), 500)

    def test_render_requires_video_source(self) -> None:
        response = self.client.post(f"/api/v1/runs/{self.run_id}/outputs", json={"clean_master": False})
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
