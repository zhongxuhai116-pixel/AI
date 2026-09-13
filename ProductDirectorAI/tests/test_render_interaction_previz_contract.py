"""render_interaction_previz.py 的静态合同检查（本机无 Blender，用源码文本钉住阈值与复用关系）。"""
from __future__ import annotations

import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "blender" / "scripts" / "render_interaction_previz.py"


class RenderInteractionPrevizContractTests(unittest.TestCase):
    def test_contact_qa_thresholds_match_master_plan(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("CONTACT_TOLERANCE_M = 0.02", text)
        self.assertIn("PENETRATION_TOLERANCE_M = 0.01", text)
        self.assertIn("MAX_PENETRATION_FRAMES = 2", text)
        self.assertIn("TIMING_TOLERANCE_FRAMES = 2", text)
        self.assertIn("PRESS_DEPTH_M = 0.004", text)

    def test_previz_reuses_render_product_and_geometry_module(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("import render_product as rp", text)
        self.assertIn("from productdirector_api import interaction_geometry, interaction_validation", text)
        self.assertIn("interaction_geometry.anchor_to_world", text)
        self.assertIn("proxy_only", text)

    def test_contact_qa_measures_surface_gap_and_bbox_penetration(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("gap = max(0.0, (hand - center).length - radius)", text)
        self.assertIn("inside = all(", text)
        self.assertIn("min(hand[axis] - bbox_min[axis] for axis in range(3))", text)
        self.assertIn("actual_contact_frame", text)
        self.assertIn("hold_window_max_gap_m", text)
        self.assertIn("raise SystemExit(0 if qa_report[\"passed\"] else 1)", text)


if __name__ == "__main__":
    unittest.main()
