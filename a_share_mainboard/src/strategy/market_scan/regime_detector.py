from __future__ import annotations


class RegimeDetector:
    def detect(self, metrics: dict[str, float | int | str]) -> dict[str, str]:
        breadth = float(metrics.get("breadth_up_ratio", 0.0) or 0.0)
        benchmark_ret_5d = float(metrics.get("benchmark_ret_5d", 0.0) or 0.0)

        if breadth >= 0.55 and benchmark_ret_5d > 0:
            regime_label = "bullish"
        elif breadth <= 0.45 and benchmark_ret_5d < 0:
            regime_label = "defensive"
        else:
            regime_label = "neutral"

        if breadth >= 0.6:
            style_label = "trend"
        elif breadth <= 0.4:
            style_label = "risk_off"
        else:
            style_label = "balanced"

        return {
            "regime_label": regime_label,
            "style_label": style_label,
        }
