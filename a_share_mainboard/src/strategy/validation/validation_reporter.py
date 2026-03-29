from __future__ import annotations


class ValidationReporter:
    def build_report(self, metrics: dict, horizon: int) -> dict:
        return {
            "horizon": horizon,
            "metrics": metrics,
        }

