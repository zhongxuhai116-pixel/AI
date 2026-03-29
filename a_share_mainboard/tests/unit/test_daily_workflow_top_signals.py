from __future__ import annotations

import pandas as pd

from app.daily_workflow import DailyWorkflow


def test_build_top_signals_primary_first_and_limit():
    signals_df = pd.DataFrame(
        [
            {"symbol": "600001", "name": "Alpha", "horizon": 10, "final_rank": 2, "target_weight": 0.2, "rule_tags": "policy_hot"},
            {"symbol": "600002", "name": "Beta", "horizon": 5, "final_rank": 1, "target_weight": 0.3, "rule_tags": "policy_warm"},
            {"symbol": "600003", "name": "Gamma", "horizon": 10, "final_rank": 1, "target_weight": 0.4, "rule_tags": ""},
        ]
    )
    strategy_profile = {"primary_horizon": 10}

    top = DailyWorkflow._build_top_signals(
        signals_df=signals_df,
        strategy_profile=strategy_profile,
        limit=2,
    )

    assert len(top) == 2
    assert top[0]["symbol"] == "600003"
    assert top[0]["role"] == "PRIMARY"
    assert top[1]["symbol"] == "600001"
    assert top[1]["role"] == "PRIMARY"
