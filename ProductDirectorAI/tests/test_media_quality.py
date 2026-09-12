"""A06 媒体质量门：用真实 FFmpeg 产物验证黑帧/空白帧检测。"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "apps" / "api"))

from productdirector_api import main  # noqa: E402

FFMPEG = main.FFMPEG or shutil.which("ffmpeg")


def _plan(shots: tuple[int, ...] = (24, 72, 48)) -> dict:
    return {"shots": [{"id": f"shot_0{index + 1}", "duration_frames": frames} for index, frames in enumerate(shots)]}


@unittest.skipUnless(FFMPEG, "需要 FFmpeg 才能生成真实测试视频")
class MediaQualityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._workdir = Path(tempfile.mkdtemp(prefix="pdqa-"))
        self.addCleanup(shutil.rmtree, self._workdir, ignore_errors=True)
        self._original_ffmpeg = main.FFMPEG
        main.FFMPEG = FFMPEG
        self.addCleanup(self._restore_ffmpeg)

    def _restore_ffmpeg(self) -> None:
        main.FFMPEG = self._original_ffmpeg

    def _make_video(self, source: str, name: str) -> Path:
        output = self._workdir / f"{name}.mp4"
        result = subprocess.run(
            [FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", source,
             "-t", "6", "-r", "24", "-s", "540x960", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(output)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr[-500:])
        return output

    def test_sample_frames_follow_shot_boundaries(self) -> None:
        self.assertEqual(main.qa_sample_frames(_plan(), 144), [1, 24, 96, 144])
        self.assertEqual(main.qa_sample_frames({"shots": []}, 144), [1, 144])

    def test_black_video_is_rejected(self) -> None:
        video = self._make_video("color=c=black:s=540x960:r=24", "black")
        report = main.media_quality_report(video, _plan(), main.OutputSpec(), self._workdir / "qa-black")
        self.assertFalse(report["passed"])
        self.assertTrue(any(("全黑" in failure) or ("单色" in failure) for failure in report["failures"]))
        self.assertGreater(report["black_seconds_ratio"], 0.9)

    def test_uniform_white_video_is_rejected_as_invisible(self) -> None:
        video = self._make_video("color=c=white:s=540x960:r=24", "white")
        report = main.media_quality_report(video, _plan(), main.OutputSpec(), self._workdir / "qa-white")
        self.assertFalse(report["passed"])
        self.assertTrue(any("近乎单色" in failure for failure in report["failures"]))

    def test_moving_pattern_passes_and_records_samples(self) -> None:
        video = self._make_video("testsrc=size=540x960:rate=24", "testsrc")
        report = main.media_quality_report(video, _plan(), main.OutputSpec(), self._workdir / "qa-testsrc")
        self.assertTrue(report["passed"], report["failures"])
        self.assertEqual([sample["frame"] for sample in report["samples"]], [1, 24, 96, 144])
        self.assertLess(report["black_seconds_ratio"], 0.05)

    def test_unreadable_video_reports_failure_instead_of_crashing(self) -> None:
        broken = self._workdir / "broken.mp4"
        broken.write_bytes(b"not a video")
        report = main.media_quality_report(broken, _plan(), main.OutputSpec(), self._workdir / "qa-broken")
        self.assertFalse(report["passed"])
        self.assertTrue(report["failures"])


if __name__ == "__main__":
    unittest.main()
