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
