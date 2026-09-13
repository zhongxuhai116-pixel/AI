"""V5-02：本地切镜检测、cut time 指标与人工修订 API。"""
from __future__ import annotations

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
from productdirector_api import reference_analysis  # noqa: E402
FFMPEG = main.FFMPEG or shutil.which("ffmpeg")


def _make_three_segment_video(path: Path) -> None:
    """红/蓝/绿三色拼接视频（硬切在 1.0s 与 2.0s，24fps）。"""
    subprocess.run(
        [FFMPEG, "-y", "-v", "error",
         "-f", "lavfi", "-i", "color=c=red:d=1:r=24:s=320x240",
         "-f", "lavfi", "-i", "color=c=blue:d=1:r=24:s=320x240",
         "-f", "lavfi", "-i", "color=c=green:d=1:r=24:s=320x240",
         "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
         "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True,
    )


class EvaluateCutsUnitTests(unittest.TestCase):
    def test_perfect_match_f1(self) -> None:
        report = reference_analysis.evaluate_cuts([1.0, 2.0, 5.0], [0.95, 2.05, 5.1])
        self.assertEqual(report["matched"], 3)
        self.assertEqual(report["f1"], 1.0)
        self.assertTrue(report["passed_f1_gate"])

    def test_miss_and_extra(self) -> None:
        report = reference_analysis.evaluate_cuts([1.0, 3.0], [1.0, 2.0])
        self.assertEqual(report["matched"], 1)
        self.assertEqual(report["missed_annotation_indices"], [1])
        self.assertEqual(report["extra_detection_indices"], [1])
        self.assertEqual(report["precision"], 0.5)
        self.assertEqual(report["recall"], 0.5)
        self.assertEqual(report["f1"], 0.5)
        self.assertFalse(report["passed_f1_gate"])

    def test_empty_annotated(self) -> None:
        report = reference_analysis.evaluate_cuts([], [])
        self.assertEqual(report["recall"], 1.0)
        self.assertEqual(report["precision"], 1.0)


@unittest.skipUnless(FFMPEG, "需要 FFmpeg 才能生成真实参考视频")
class V5AnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        fixtures.JobControlAcceptanceTests.setUp(self)
        self.video = main.VAR / "test-reference-3seg.mp4"
        _make_three_segment_video(self.video)

    tearDown = fixtures.JobControlAcceptanceTests.tearDown

    def _upload(self) -> dict:
        with open(self.video, "rb") as handle:
            response = self.client.post(
                "/api/v1/references/upload",
                files={"file": ("reference.mp4", handle, "video/mp4")},
            )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_detector_finds_known_cuts(self) -> None:
        cuts = reference_analysis.detect_hard_cuts(self.video)
        report = reference_analysis.evaluate_cuts(cuts, [1.0, 2.0])
        self.assertTrue(report["passed_f1_gate"], (cuts, report))
        self.assertGreaterEqual(report["f1"], 0.90)

    def test_analyze_edit_approve_flow(self) -> None:
        ref = self._upload()
        analyzed = self.client.post(f"/api/v1/references/{ref['id']}/analyze", json={"scope": "cuts"})
        self.assertEqual(analyzed.status_code, 201, analyzed.text)
        analysis = analyzed.json()
        self.assertEqual(analysis["revision"], 1)
        self.assertEqual(analysis["status"], "DRAFT")
        self.assertGreaterEqual(len(analysis["analysis"]["segments"]), 3)
        report = reference_analysis.evaluate_cuts(analysis["analysis"]["cuts"], [1.0, 2.0])
        self.assertTrue(report["passed_f1_gate"], (analysis["analysis"]["cuts"], report))
        # 人工修订 → 新修订，记录 edited_from
        edited = self.client.patch(
            f"/api/v1/reference-analyses/{analysis['id']}",
            json={"segments": analysis["analysis"]["segments"], "notes": "人工确认切点"},
        )
        self.assertEqual(edited.status_code, 200, edited.text)
        second = edited.json()
        self.assertEqual(second["revision"], 2)
        self.assertEqual(second["edited_from_revision"], 1)
        # 批准修订 2 → 幂等 + 不可再编辑
        approved = self.client.post(f"/api/v1/reference-analyses/{second['id']}/approve")
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(approved.json()["status"], "APPROVED")
        again = self.client.post(f"/api/v1/reference-analyses/{second['id']}/approve")
        self.assertEqual(again.status_code, 200, again.text)
        locked = self.client.patch(
            f"/api/v1/reference-analyses/{second['id']}",
            json={"segments": analysis["analysis"]["segments"]},
        )
        self.assertEqual(locked.status_code, 409, locked.text)
        listing = self.client.get(f"/api/v1/references/{ref['id']}/analyses")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(len(listing.json()), 2)
        detail = self.client.get(f"/api/v1/reference-analyses/{analysis['id']}")
        self.assertEqual(detail.status_code, 200)


if __name__ == "__main__":
    unittest.main()
