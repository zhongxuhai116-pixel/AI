from __future__ import annotations


class ValidationMetricsCalculator:
    def summarize(self, trades_df, equity_df):
        _ = (trades_df, equity_df)
        return {"return": 0.0, "max_drawdown": 0.0}

