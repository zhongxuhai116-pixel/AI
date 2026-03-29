from __future__ import annotations

from app.validate_rolling_workflow import RollingValidateWorkflow


def test_build_windows_uses_trade_dates_and_includes_tail_window():
    trade_dates = [
        "2026-03-02",
        "2026-03-03",
        "2026-03-04",
        "2026-03-05",
        "2026-03-06",
        "2026-03-09",
    ]

    windows = RollingValidateWorkflow._build_windows(
        trade_dates=trade_dates,
        window_size=3,
        step_size=2,
    )

    assert windows == [
        {"start_date": "2026-03-02", "end_date": "2026-03-04"},
        {"start_date": "2026-03-04", "end_date": "2026-03-06"},
        {"start_date": "2026-03-05", "end_date": "2026-03-09"},
    ]


def test_summarize_windows_reports_stability_and_policy_outperformance():
    summary = RollingValidateWorkflow._summarize_windows(
        [
            {
                "start_date": "2026-03-02",
                "end_date": "2026-03-10",
                "summary": {"cumulative_return": 0.05, "win_rate": 0.60},
                "policy_review": {
                    "policy_group": {"avg_trade_return": 0.03},
                    "non_policy_group": {"avg_trade_return": 0.01},
                },
            },
            {
                "start_date": "2026-03-11",
                "end_date": "2026-03-19",
                "summary": {"cumulative_return": -0.02, "win_rate": 0.45},
                "policy_review": {
                    "policy_group": {"avg_trade_return": 0.00},
                    "non_policy_group": {"avg_trade_return": 0.02},
                },
            },
        ]
    )

    assert summary["window_count"] == 2
    assert summary["positive_window_ratio"] == 0.5
    assert round(summary["avg_cumulative_return"], 6) == 0.015
    assert summary["policy_outperformance_ratio"] == 0.5
    assert summary["best_window"]["start_date"] == "2026-03-02"
    assert summary["worst_window"]["end_date"] == "2026-03-19"
