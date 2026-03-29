from __future__ import annotations

import pandas as pd


class ReportContextBuilder:
    def build(
        self,
        trade_date: str,
        market_regime: dict,
        signals_df,
        ai_outputs: dict | None,
        validation_outputs: dict | None = None,
        policy_outputs: dict | None = None,
        strategy_profile: dict | None = None,
    ) -> dict:
        if isinstance(signals_df, pd.DataFrame):
            signal_records = signals_df.to_dict(orient="records")
        else:
            signal_records = [] if signals_df is None else signals_df

        signals_by_horizon: dict[int, list[dict]] = {}
        for row in signal_records:
            horizon = int(row.get("horizon", 0) or 0)
            signals_by_horizon.setdefault(horizon, []).append(row)

        return {
            "trade_date": trade_date,
            "market_regime": market_regime,
            "signals": signal_records,
            "signals_by_horizon": signals_by_horizon,
            "ai_outputs": ai_outputs or {},
            "validation_outputs": validation_outputs or {},
            "policy_outputs": policy_outputs or {},
            "strategy_profile": strategy_profile or {},
        }
