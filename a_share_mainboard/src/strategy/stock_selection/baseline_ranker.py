from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(slots=True)
class BaselineRanker:
    factor_weights: dict[str, float]

    def score(self, features_df: pd.DataFrame, trade_date: str, horizon: int) -> pd.DataFrame:
        _ = horizon
        if features_df.empty:
            return pd.DataFrame(
                columns=["trade_date", "symbol", "score_raw", "score_rank", "score_bucket"]
            )

        frame = features_df.copy()
        frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.date
        frame = frame[frame["trade_date"] == pd.to_datetime(trade_date).date()].copy()
        if frame.empty:
            return pd.DataFrame(
                columns=["trade_date", "symbol", "score_raw", "score_rank", "score_bucket"]
            )

        active_factors = [
            factor
            for factor in self.factor_weights
            if factor in frame.columns and frame[factor].notna().any()
        ]
        if not active_factors:
            return pd.DataFrame(
                columns=["trade_date", "symbol", "score_raw", "score_rank", "score_bucket"]
            )

        frame["score_raw"] = 0.0
        for factor in active_factors:
            weight = self.factor_weights[factor]
            values = pd.to_numeric(frame[factor], errors="coerce")
            factor_rank = values.rank(
                pct=True,
                ascending=weight < 0,
                method="average",
            ).fillna(0.5)
            frame["score_raw"] += abs(weight) * factor_rank

        frame = frame.sort_values(["score_raw", "symbol"], ascending=[False, True], ignore_index=True)
        frame["score_rank"] = range(1, len(frame) + 1)

        bucket_count = min(5, len(frame))
        if bucket_count <= 1:
            frame["score_bucket"] = 1
        else:
            frame["score_bucket"] = (
                pd.qcut(frame["score_rank"], q=bucket_count, labels=False, duplicates="drop") + 1
            )

        return frame[["trade_date", "symbol", "score_raw", "score_rank", "score_bucket"]].copy()
