from __future__ import annotations

import pandas as pd

from strategy.validation.execution_model import ExecutionModel
from strategy.validation.validation_engine import ValidationEngine


def test_execution_model_applies_round_trip_costs():
    model = ExecutionModel(execution_price_mode="next_open", cost_bps=15)
    trade_return = model.calculate_trade_return(
        entry_bar={"open": 10.0, "close": 10.2},
        exit_bar={"open": 10.4, "close": 10.5},
    )

    expected = (10.5 / 10.0) - 1.0 - 0.003
    assert trade_return == expected


def test_validation_engine_summarizes_trade_outcomes():
    outcomes = pd.DataFrame(
        [
            {"signal_date": "2026-03-20", "symbol": "000001", "trade_return": 0.05},
            {"signal_date": "2026-03-20", "symbol": "000002", "trade_return": -0.01},
            {"signal_date": "2026-03-21", "symbol": "000003", "trade_return": 0.02},
        ]
    )

    summary = ValidationEngine._summarize_outcomes(
        horizon=5,
        signal_dates=["2026-03-20", "2026-03-21", "2026-03-24"],
        outcomes_df=outcomes,
        skipped_trades=1,
    )

    assert summary["signal_days"] == 2.0
    assert summary["trade_count"] == 3.0
    assert round(summary["avg_trade_return"], 6) == 0.02
    assert round(summary["avg_cohort_return"], 6) == 0.02
    assert round(summary["win_rate"], 6) == round(2 / 3, 6)
    assert summary["skipped_trades"] == 1.0
    assert round(summary["coverage_ratio"], 6) == round(2 / 3, 6)


def test_validation_engine_summarizes_policy_review():
    outcomes = pd.DataFrame(
        [
            {
                "signal_date": "2026-03-20",
                "symbol": "000001",
                "trade_return": 0.05,
                "rule_tags": "mainboard|baseline|policy_gate|policy_warm|two_new_2026",
            },
            {
                "signal_date": "2026-03-20",
                "symbol": "000002",
                "trade_return": -0.01,
                "rule_tags": "mainboard|baseline|sentiment_gate",
            },
            {
                "signal_date": "2026-03-21",
                "symbol": "000003",
                "trade_return": 0.02,
                "rule_tags": "mainboard|baseline|policy_gate|policy_hot|ai_plus_2026",
            },
        ]
    )

    review = ValidationEngine._summarize_policy_review(outcomes)

    assert review["policy_group"]["trade_count"] == 2.0
    assert round(review["policy_group"]["avg_trade_return"], 6) == 0.035
    assert review["non_policy_group"]["trade_count"] == 1.0
    assert len(review["theme_groups"]) == 2
    assert review["theme_groups"][0]["theme"] in {"two_new_2026", "ai_plus_2026"}


def test_validation_engine_builds_universe_review():
    class StubMarketRepo:
        @staticmethod
        def get_instruments():
            return pd.DataFrame([{"symbol": "000001"}, {"symbol": "000002"}])

    class StubResearchRepo:
        @staticmethod
        def read_dataframe(sql, params):
            _ = params
            if "stock_pool_daily" in sql:
                return pd.DataFrame(
                    [
                        {"trade_date": "2026-03-20", "eligible_count": 2},
                        {"trade_date": "2026-03-21", "eligible_count": 1},
                    ]
                )
            if "features_daily" in sql:
                return pd.DataFrame(
                    [
                        {"trade_date": "2026-03-20", "feature_count": 2},
                        {"trade_date": "2026-03-21", "feature_count": 2},
                    ]
                )
            return pd.DataFrame(
                [
                    {"trade_date": "2026-03-20", "signal_count": 1},
                    {"trade_date": "2026-03-21", "signal_count": 0},
                ]
            )

    engine = ValidationEngine(
        market_repo=StubMarketRepo(),
        repo=StubResearchRepo(),
        execution_model=None,
        settings=None,
        universe_settings=None,
        strategy_settings=None,
        policy_settings=None,
    )

    review = engine._build_universe_review(signal_dates=["2026-03-20", "2026-03-21"])

    assert review["instrument_count"] == 2
    assert review["avg_eligible_pool"] == 1.5
    assert review["avg_feature_ready"] == 2.0
    assert review["avg_daily_signals"] == 0.5
