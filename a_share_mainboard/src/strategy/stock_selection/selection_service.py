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
            return self._save_fallback_scores(trade_date=trade_date, horizons=horizons)

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
            return self._save_fallback_scores(trade_date=trade_date, horizons=horizons)

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

    def _save_fallback_scores(self, *, trade_date: str, horizons: list[int]) -> int:
        fallback = self._build_liquidity_fallback(trade_date=trade_date)
        if fallback.empty or not horizons:
            self.repo.delete_model_scores_for_trade_date(trade_date)
            return 0

        rows: list[pd.DataFrame] = []
        for horizon in horizons:
            frame = fallback.copy()
            frame["horizon"] = int(horizon)
            frame["model_name"] = "baseline_fallback_liquidity"
            rows.append(
                frame[
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

        payload = pd.concat(rows, ignore_index=True)
        self.repo.delete_model_scores_for_trade_date(trade_date)
        return self.repo.save_model_scores(payload)

    def _build_liquidity_fallback(self, *, trade_date: str) -> pd.DataFrame:
        pool = self.repo.get_stock_pool(trade_date=trade_date, eligible_only=True)
        if pool.empty:
            return pd.DataFrame(
                columns=["trade_date", "symbol", "score_raw", "score_rank", "score_bucket"]
            )

        frame = pool[["trade_date", "symbol", "liquidity_score"]].copy()
        frame["liquidity_score"] = pd.to_numeric(frame["liquidity_score"], errors="coerce").fillna(0.0)
        frame = frame.sort_values(
            ["liquidity_score", "symbol"],
            ascending=[False, True],
            ignore_index=True,
        )
        if frame.empty:
            return pd.DataFrame(
                columns=["trade_date", "symbol", "score_raw", "score_rank", "score_bucket"]
            )

        frame["score_rank"] = range(1, len(frame) + 1)
        denom = max(len(frame), 1)
        frame["score_raw"] = (denom - frame["score_rank"] + 1) / denom

        bucket_count = min(5, len(frame))
        if bucket_count <= 1:
            frame["score_bucket"] = 1
        else:
            frame["score_bucket"] = (
                pd.qcut(frame["score_rank"], q=bucket_count, labels=False, duplicates="drop") + 1
            )

        return frame[["trade_date", "symbol", "score_raw", "score_rank", "score_bucket"]].copy()
