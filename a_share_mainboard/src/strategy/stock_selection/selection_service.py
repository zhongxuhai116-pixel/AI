from __future__ import annotations

import json
from dataclasses import dataclass

import pandas as pd

from data.storage.repositories import ResearchRepository
from strategy.stock_selection.baseline_ranker import BaselineRanker
from strategy.stock_selection.ml_ranker import MLRanker


@dataclass(slots=True)
class SelectionService:
    repo: ResearchRepository
    baseline_ranker: BaselineRanker
    ml_ranker: MLRanker | None = None

    def run(self, trade_date: str, horizons: list[int]) -> int:
        features = self.repo.get_features(trade_date)
        parsed_features = self._parse_features(features)
        if parsed_features.empty:
            self.repo.delete_model_scores_for_trade_date(trade_date)
            return 0

        rows: list[pd.DataFrame] = []
        for horizon in horizons:
            baseline_scores = self.baseline_ranker.score(
                features_df=parsed_features,
                trade_date=trade_date,
                horizon=horizon,
            )
            if baseline_scores.empty:
                continue

            baseline_scores["horizon"] = horizon
            baseline_scores["model_name"] = "baseline"
            rows.append(
                baseline_scores[
                    [
                        "trade_date",
                        "symbol",
                        "horizon",
                        "model_name",
                        "score_raw",
                        "score_rank",
                        "score_bucket",
                    ]
                ].copy()
            )

        if not rows:
            self.repo.delete_model_scores_for_trade_date(trade_date)
            return 0

        payload = pd.concat(rows, ignore_index=True)
        self.repo.delete_model_scores_for_trade_date(trade_date)
        return self.repo.save_model_scores(payload)

    @staticmethod
    def _parse_features(features_df: pd.DataFrame) -> pd.DataFrame:
        if features_df.empty:
            return pd.DataFrame()

        parsed = features_df["feature_values"].apply(
            lambda value: json.loads(value) if value else {}
        ).apply(pd.Series)
        frame = pd.concat([features_df[["trade_date", "symbol"]], parsed], axis=1)
        return frame
