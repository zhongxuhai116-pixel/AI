#!/usr/bin/env python3
"""V3-06 双产品检测的合成数据单测。

数据全部由 `scripts/dual_product_check.py --self-test-fixtures` 程序化合成
（干净背景正例 / 粘贴产品的负例 / 示例 plan.json），不依赖 EXR 与后端依赖。

运行：
    python -m unittest tests.test_v306_dual_product -v
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "dual_product_check.py"

# 与 make_self_test_fixtures 中的夹具常量保持一致
FRAMES = 8
WIDTH, HEIGHT = 240, 160
PRODUCT_AT = (30, 60, 56, 40)   # (x, y, w, h) 产品真值位置
DIRTY_FRAMES = [2, 5]           # 负例中粘贴多余产品的帧号


def run_cli(*cli_args: str) -> tuple[int, dict]:
    """以子进程方式运行 CLI，返回 (退出码, report.json 内容)。"""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *cli_args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(REPO_ROOT),
    )
    out_dir = Path(cli_args[cli_args.index("--out") + 1]) if "--out" in cli_args else None
    report = {}
    if out_dir and (out_dir / "report.json").exists():
        report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    return result.returncode, report


class DualProductCheckTest(unittest.TestCase):
    """合成夹具驱动的端到端用例。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="v306_dual_product_")
        cls.fixtures = Path(cls._tmp.name) / "fixtures"
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--self-test-fixtures", str(cls.fixtures)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(REPO_ROOT),
        )
        if result.returncode != 0:
            raise RuntimeError(f"夹具生成失败：{result.stderr}")
        cls.summary = json.loads(result.stdout)["fixtures"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _out(self, name: str) -> Path:
        path = Path(self._tmp.name) / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    # 用例 1：干净背景必须全部通过（exit 0，verdict PASS）
    def test_clean_background_passes(self) -> None:
        out = self._out("clean")
        code, report = run_cli(
            "--frames", str(self.fixtures / "bg_clean"),
            "--product", str(self.fixtures / "product"),
            "--mask", str(self.fixtures / "mask"),
            "--out", str(out), "--dilate", "2",
        )
        self.assertEqual(code, 0, msg=f"干净背景被误报：{report.get('blocked_reason')}")
        self.assertEqual(report["verdict"], "PASS")
        self.assertEqual(report["frames"], FRAMES)
        self.assertEqual(report["problem_frames"], [])
        for entry in report["frames_report"]:
            self.assertEqual(entry["verdict"], "PASS")
            self.assertEqual(entry["detections"], [])

    # 用例 2：含双产品的背景必须被阻断（exit 非 0），问题帧号正确、热图存在
    def test_dual_product_blocked(self) -> None:
        out = self._out("dirty")
        code, report = run_cli(
            "--frames", str(self.fixtures / "bg_dirty"),
            "--product", str(self.fixtures / "product"),
            "--mask", str(self.fixtures / "mask"),
            "--out", str(out), "--dilate", "2",
        )
        self.assertNotEqual(code, 0, msg="含双产品的背景未被阻断")
        self.assertEqual(report["verdict"], "BLOCKED")
        self.assertEqual(sorted(report["problem_frames"]), DIRTY_FRAMES)
        self.assertIn("多余产品", report["blocked_reason"])
        for frame in DIRTY_FRAMES:
            entry = report["frames_report"][frame]
            self.assertEqual(entry["verdict"], "FAIL")
            self.assertGreaterEqual(len(entry["detections"]), 1)
            detection = entry["detections"][0]
            self.assertEqual(len(detection["bbox"]), 4)
            self.assertGreaterEqual(detection["score"], report["thresholds"]["ncc"])
            heatmap = out / entry["heatmap"]
            self.assertTrue(heatmap.exists(), msg=f"问题帧 {frame} 缺热图 {heatmap}")
            with Image.open(heatmap) as image:
                self.assertEqual(image.size, (WIDTH, HEIGHT))
        # 干净帧不得出现在问题帧里
        clean_frames = [f for f in range(FRAMES) if f not in DIRTY_FRAMES]
        for frame in clean_frames:
            self.assertNotIn(frame, report["problem_frames"])

    # 用例 3：外扩掩码必须排除真值产品位置（产品在原位不误报）
    def test_dilated_mask_excludes_true_product(self) -> None:
        composite = Path(self._tmp.name) / "composite_true_product"
        composite.mkdir(parents=True, exist_ok=True)
        px, py, pw, ph = PRODUCT_AT
        for frame in range(FRAMES):
            with Image.open(self.fixtures / "bg_clean" / f"bg_{frame:04d}.png") as image:
                background = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
            with Image.open(self.fixtures / "product" / f"prod_{frame:04d}.png") as image:
                product = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
            # 按掩码把可信产品合成回原位，等价于 strict_composite 的产物
            mask = np.zeros((HEIGHT, WIDTH), dtype=bool)
            mask[py: py + ph, px: px + pw] = True
            merged = np.where(mask[:, :, None], product, background)
            Image.fromarray((merged * 255 + 0.5).astype(np.uint8)).save(composite / f"frame_{frame:04d}.png")
        out = self._out("composite")
        code, report = run_cli(
            "--frames", str(composite),
            "--product", str(self.fixtures / "product"),
            "--mask", str(self.fixtures / "mask"),
            "--out", str(out), "--dilate", "2",
        )
        self.assertEqual(code, 0, msg=f"原产品位置被误报：{report.get('blocked_reason')}")
        self.assertEqual(report["verdict"], "PASS")
        self.assertEqual(report["problem_frames"], [])

    # 用例 4：提供 --plan 时帧号必须正确映射到 Shot
    def test_plan_maps_frames_to_shots(self) -> None:
        out = self._out("dirty_plan")
        code, report = run_cli(
            "--frames", str(self.fixtures / "bg_dirty"),
            "--product", str(self.fixtures / "product"),
            "--mask", str(self.fixtures / "mask"),
            "--out", str(out), "--dilate", "2",
            "--plan", str(self.fixtures / "plan.json"),
        )
        self.assertNotEqual(code, 0)
        self.assertEqual(sorted(report["problem_frames"]), DIRTY_FRAMES)
        # 夹具 plan：shot_01 覆盖帧 0-3，shot_02 覆盖帧 4-7
        expected = {2: ("shot_01", "开场展示"), 5: ("shot_02", "细节收尾")}
        for frame, (shot_id, shot_name) in expected.items():
            entry = report["frames_report"][frame]
            self.assertEqual(entry["shot_id"], shot_id, msg=f"帧 {frame} Shot 映射错误")
            self.assertEqual(entry["shot_name"], shot_name)

    # 用例 5：report.json 必须携带阈值参数与逐帧判定（供报告与复验使用）
    def test_report_contains_thresholds_and_per_frame_verdicts(self) -> None:
        out = self._out("report_fields")
        code, report = run_cli(
            "--frames", str(self.fixtures / "bg_clean"),
            "--product", str(self.fixtures / "product"),
            "--mask", str(self.fixtures / "mask"),
            "--out", str(out),
        )
        self.assertEqual(code, 0)
        self.assertIn("ncc", report["thresholds"])
        self.assertIn("histogram", report["thresholds"])
        self.assertEqual(report["dilate_pixels"], 2)  # 默认值与 strict_composite 一致
        self.assertEqual(len(report["frames_report"]), FRAMES)
        self.assertTrue(report["templates"], msg="报告缺少模板清单")
        self.assertTrue((out / "report.json").exists())


if __name__ == "__main__":
    unittest.main()
