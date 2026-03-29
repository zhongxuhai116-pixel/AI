from __future__ import annotations

import pandas as pd

from strategy.stock_selection.baseline_ranker import BaselineRanker


def test_baseline_ranker_favors_positive_factor_high_values_and_negative_factor_low_values():
    features = pd.DataFrame(
        [
            {"trade_date": "2026-03-27", "symbol": "000001", "ret_5d": 0.10, "volatility_10d": 0.10},
            {"trade_date": "2026-03-27", "symbol": "000002", "ret_5d": 0.05, "volatility_10d": 0.20},
            {"trade_date": "2026-03-27", "symbol": "000003", "ret_5d": -0.02, "volatility_10d": 0.30},
        ]
    )

    result = BaselineRanker(
        factor_weights={"ret_5d": 1.0, "volatility_10d": -1.0}
    ).score(features_df=features, trade_date="2026-03-27", horizon=5)

    assert result.iloc[0]["symbol"] == "000001"
    assert result.iloc[-1]["symbol"] == "000003"
