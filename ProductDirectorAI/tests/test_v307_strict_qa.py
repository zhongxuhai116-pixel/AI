"""V3-07 Strict QA 自动阻断门测试。

用 numpy + Pillow 程序合成渲染通道产物（beauty RGBA PNG + mask PNG 序列，
Logo 为 16x16 高频棋盘格色块），对五类故障各构造人工负例并断言被阻断、
问题帧定位正确；另有全部干净的正例、Logo 检查 SKIPPED 路径、
Shot 定位与多故障并列（检查器解耦）用例。

测试只 import numpy / Pillow / 标准库与 scripts/strict_qa.py，
不依赖 EXR 与项目后端依赖。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import strict_qa  # noqa: E402

WIDTH = HEIGHT = 64
FRAMES = 8
# Logo：固定在画面 (24:40, 24:40) 的 16x16 高频棋盘格，归一化区域如下
LOGO_REGION = "0.375,0.375,0.25,0.25"


def base_mask(index: int) -> np.ndarray:
    """干净序列的产品掩码：16x16 矩形，每帧向右平移 1 像素（模拟缓慢环绕）。"""
    mask = np.zeros((HEIGHT, WIDTH), dtype=bool)
    x = 8 + index
    mask[8:24, x:x + 16] = True
    return mask


def base_rgb() -> np.ndarray:
    """beauty 底色：柔和渐变 + 固定位置的 Logo 棋盘格。"""
    rgb = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    rgb[:, :, 0] = np.linspace(40, 120, WIDTH, dtype=np.uint8)[None, :]
    rgb[:, :, 1] = np.linspace(60, 100, HEIGHT, dtype=np.uint8)[:, None]
    rgb[:, :, 2] = 80
    block = np.indices((16, 16)).sum(axis=0) % 2 * 255
    rgb[24:40, 24:40, 0] = block
    rgb[24:40, 24:40, 1] = 255 - block
    rgb[24:40, 24:40, 2] = 30
    return rgb


def save_frame(root: Path, index: int, alpha: np.ndarray, rgb: np.ndarray | None = None) -> None:
    """写一帧 beauty（RGBA，rgb + alpha）与 mask（RGBA，rgb 为 0，alpha 同上）。"""
    rgb = base_rgb() if rgb is None else rgb
    beauty = np.dstack([rgb, alpha])
    Image.fromarray(beauty, "RGBA").save(root / "beauty" / f"frame_{index:04d}.png")
    mask_image = np.zeros((HEIGHT, WIDTH, 4), dtype=np.uint8)
    mask_image[:, :, 3] = alpha
    Image.fromarray(mask_image, "RGBA").save(root / "mask" / f"frame_{index:04d}.png")


def build_sequence(root: Path, mutate=None) -> Path:
    """生成干净序列，再经 mutate(index, alpha, rgb) 注入人工故障。

    mutate 返回 (alpha, rgb) 或 None（None 表示该帧不写出，用于构造缺帧）。
    """
    (root / "beauty").mkdir(parents=True)
    (root / "mask").mkdir(parents=True)
    for index in range(FRAMES):
        alpha = base_mask(index).astype(np.uint8) * 255
        rgb = base_rgb()
        if mutate:
            result = mutate(index, alpha, rgb)
            if result is None:
                continue
            alpha, rgb = result
        save_frame(root, index, alpha, rgb)
    return root


def run_qa(case_root: Path, extra_args: list[str] | None = None) -> tuple[int, dict, Path]:
    out_dir = case_root / "qa_out"
    argv = ["--beauty", str(case_root / "beauty"), "--mask", str(case_root / "mask"),
            "--out", str(out_dir)] + (extra_args or [])
    code = strict_qa.main(argv)
    report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    return code, report, out_dir


def logo_args(reference: Path) -> list[str]:
    return ["--logo-region", LOGO_REGION, "--reference", str(reference)]


class StrictQATest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pd-v307-")
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def make_case(self, name: str, mutate=None) -> Path:
        return build_sequence(self.root / name, mutate)

    def problem_frames(self, report: dict, check: str) -> list[int]:
        return [p["frame"] for p in report["problems"] if p["check"] == check]

    # 1. 全部干净（含 Logo 检查）→ 通过，出口码 0
    def test_clean_sequence_passes(self):
        case = self.make_case("clean")
        code, report, _ = run_qa(case, logo_args(case / "beauty" / "frame_0000.png"))
        self.assertEqual(code, 0)
        self.assertTrue(report["passed"])
        self.assertFalse(report["blocked"])
        for name in ("missing_frames", "mask_integrity", "size_stability", "contour_anomaly", "logo_presence"):
            self.assertEqual(report["checks"][name]["status"], "PASS", name)

    # 2. 缺帧负例：抽掉第 3 帧 → missing_frames FAIL 且列出帧号 3
    def test_missing_frame_blocked(self):
        case = self.make_case("missing", lambda i, a, r: None if i == 3 else (a, r))
        code, report, _ = run_qa(case)
        self.assertNotEqual(code, 0)
        self.assertEqual(report["checks"]["missing_frames"]["status"], "FAIL")
        self.assertEqual(report["checks"]["missing_frames"]["missing"], [3])
        self.assertIn(3, self.problem_frames(report, "missing_frames"))

    # 3. Mask/ID 错误负例：第 2 帧掩码 alpha 取中间值 128（非二值）
    def test_non_binary_mask_blocked(self):
        def mutate(i, alpha, rgb):
            if i == 2:
                alpha = (base_mask(i).astype(np.uint8)) * 128
            return alpha, rgb

        case = self.make_case("non_binary", mutate)
        code, report, out_dir = run_qa(case)
        self.assertNotEqual(code, 0)
        self.assertEqual(report["checks"]["mask_integrity"]["status"], "FAIL")
        self.assertIn(2, self.problem_frames(report, "mask_integrity"))
        self.assertTrue((out_dir / "mask_integrity_binariness_frame_0002.png").exists())

    # 4. Mask/ID 错误负例：第 4 帧掩码全空（产品丢失）
    def test_empty_mask_blocked(self):
        def mutate(i, alpha, rgb):
            if i == 4:
                alpha = np.zeros_like(alpha)
            return alpha, rgb

        case = self.make_case("empty", mutate)
        code, report, _ = run_qa(case)
        self.assertNotEqual(code, 0)
        self.assertEqual(report["checks"]["mask_integrity"]["status"], "FAIL")
        self.assertIn(4, self.problem_frames(report, "mask_integrity"))

    # 5. 尺寸变化负例：第 5 帧掩码从 16x16 突变为 24x24（面积 ×2.25）
    def test_size_jump_blocked(self):
        def mutate(i, alpha, rgb):
            if i == 5:
                mask = np.zeros((HEIGHT, WIDTH), dtype=bool)
                mask[8:32, 13:37] = True
                alpha = mask.astype(np.uint8) * 255
            return alpha, rgb

        case = self.make_case("size", mutate)
        code, report, _ = run_qa(case)
        self.assertNotEqual(code, 0)
        self.assertEqual(report["checks"]["size_stability"]["status"], "FAIL")
        self.assertIn(5, self.problem_frames(report, "size_stability"))

    # 6. 轮廓异常负例：第 6 帧掩码跳到画面另一角（IoU 骤降、质心大位移）
    def test_contour_anomaly_blocked(self):
        def mutate(i, alpha, rgb):
            if i == 6:
                mask = np.zeros((HEIGHT, WIDTH), dtype=bool)
                mask[40:56, 40:56] = True
                alpha = mask.astype(np.uint8) * 255
            return alpha, rgb

        case = self.make_case("contour", mutate)
        code, report, out_dir = run_qa(case)
        self.assertNotEqual(code, 0)
        self.assertEqual(report["checks"]["contour_anomaly"]["status"], "FAIL")
        self.assertIn(6, self.problem_frames(report, "contour_anomaly"))
        self.assertTrue((out_dir / "contour_anomaly_frame_0006.png").exists())

    # 7. Logo 缺失负例：第 7 帧 Logo 区域被抹成纯色 → SSIM 骤降被阻断并出热图
    def test_logo_missing_blocked(self):
        def mutate(i, alpha, rgb):
            if i == 7:
                rgb = rgb.copy()
                rgb[24:40, 24:40] = (128, 128, 128)
            return alpha, rgb

        case = self.make_case("logo", mutate)
        code, report, out_dir = run_qa(case, logo_args(case / "beauty" / "frame_0000.png"))
        self.assertNotEqual(code, 0)
        self.assertEqual(report["checks"]["logo_presence"]["status"], "FAIL")
        self.assertIn(7, self.problem_frames(report, "logo_presence"))
        self.assertTrue((out_dir / "logo_presence_heatmap_frame_0007.png").exists())

    # 8. Logo 检查跳过路径：不传 logo 参数 → SKIPPED，整体仍通过
    def test_logo_check_skipped(self):
        case = self.make_case("skipped")
        code, report, _ = run_qa(case)
        self.assertEqual(code, 0)
        self.assertEqual(report["checks"]["logo_presence"]["status"], "SKIPPED")

    # 9. Shot 定位：提供 plan（两个镜头各 4 帧），缺帧 5 应定位到 shot_02 第 1 帧
    def test_plan_shot_mapping(self):
        case = self.make_case("plan", lambda i, a, r: None if i == 5 else (a, r))
        plan = {"intent": "测试", "shots": [
            {"id": "shot_01", "name": "正面推近", "duration_frames": 4},
            {"id": "shot_02", "name": "侧向观察", "duration_frames": 4},
        ]}
        plan_path = case / "director-plan.json"
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        code, report, _ = run_qa(case, ["--plan", str(plan_path)])
        self.assertNotEqual(code, 0)
        located = [p for p in report["problems"] if p["check"] == "missing_frames" and p["frame"] == 5]
        self.assertEqual(len(located), 1)
        self.assertEqual(located[0]["shot"], "shot_02")
        self.assertEqual(located[0]["shot_frame"], 1)

    # 10. 检查器解耦：缺帧（3）+ Logo 缺失（6）同时注入，报告同时列出两类故障
    def test_multiple_faults_all_listed(self):
        def mutate(i, alpha, rgb):
            if i == 3:
                return None
            if i == 6:
                rgb = rgb.copy()
                rgb[24:40, 24:40] = (128, 128, 128)
            return alpha, rgb

        case = self.make_case("multi", mutate)
        code, report, _ = run_qa(case, logo_args(case / "beauty" / "frame_0000.png"))
        self.assertNotEqual(code, 0)
        self.assertEqual(report["checks"]["missing_frames"]["status"], "FAIL")
        self.assertEqual(report["checks"]["logo_presence"]["status"], "FAIL")
        self.assertIn(3, self.problem_frames(report, "missing_frames"))
        self.assertIn(6, self.problem_frames(report, "logo_presence"))
        self.assertGreaterEqual(len(report["blocked_reasons"]), 2)


if __name__ == "__main__":
    unittest.main()
