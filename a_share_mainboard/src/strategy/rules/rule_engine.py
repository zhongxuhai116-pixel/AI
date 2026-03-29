from __future__ import annotations

import json
from dataclasses import dataclass

import pandas as pd

from data.storage.repositories import ResearchRepository
from infra.config.settings import StrategySettings


@dataclass(slots=True)
class RuleEngine:
    settings: StrategySettings
    repo: ResearchRepository

    def run(self, trade_date: str, horizon: int) -> int:
        if not self._market_is_tradeable(trade_date):
            return 0

        scores = self.repo.get_model_scores(trade_date=trade_date, horizon=horizon)
        pool = self.repo.get_stock_pool(trade_date=trade_date, eligible_only=True)
        features = self._parse_features(trade_date)
        if scores.empty or pool.empty or features.empty:
            return 0

        candidates = (
            scores.merge(pool[["symbol"]], on="symbol", how="inner")
            .merge(features, on="symbol", how="inner")
            .sort_values(["score_rank", "symbol"], ignore_index=True)
        )
        candidates = self._apply_feature_filters(candidates)
        if candidates.empty:
            return 0

        candidates = candidates.sort_values(
            ["score_raw", "score_rank", "symbol"],
            ascending=[False, True, True],
            ignore_index=True,
        )
        candidates = candidates.head(self.settings.top_n).copy()
        if candidates.empty:
            return 0

        weight = 1.0 / len(candidates)
        candidates["trade_date"] = trade_date
        candidates["horizon"] = horizon
        candidates["final_rank"] = range(1, len(candidates) + 1)
        candidates["action"] = "BUY_CANDIDATE"
        candidates["target_weight"] = weight
        candidates["rule_tags"] = "mainboard|baseline|t_plus_1|sentiment_gate"
        candidates["blocked_reason"] = ""
        payload = candidates[
            [
                "trade_date",
                "symbol",
                "horizon",
                "final_rank",
                "action",
                "target_weight",
                "rule_tags",
                "blocked_reason",
            ]
        ].copy()
        return self.repo.save_signals(payload)

    def _market_is_tradeable(self, trade_date: str) -> bool:
        market_regime_df = self.repo.get_market_regime(trade_date)
        if market_regime_df.empty:
            return False

        market_regime = market_regime_df.iloc[0].to_dict()
        regime_label = str(market_regime.get("regime_label", "unknown"))
        volume_heat = str(market_regime.get("volume_heat", "unknown"))
        extra_payload = market_regime.get("extra_payload") or "{}"
        try:
            metrics = json.loads(extra_payload)
        except json.JSONDecodeError:
            metrics = {}

        benchmark_ret_5d = float(metrics.get("benchmark_ret_5d", 0.0) or 0.0)
        if self.settings.allowed_regimes and regime_label not in self.settings.allowed_regimes:
            return False
        if self.settings.allowed_volume_heat and volume_heat not in self.settings.allowed_volume_heat:
            return False
        if self.settings.require_benchmark_positive and benchmark_ret_5d <= 0:
            return False
        return True

    def _apply_feature_filters(self, candidates: pd.DataFrame) -> pd.DataFrame:
        frame = candidates.copy()

        if self.settings.min_amount_ratio_5d is not None:
            frame = frame[frame["amount_ratio_5d"] >= self.settings.min_amount_ratio_5d]
        if self.settings.min_ret_5d is not None:
            frame = frame[frame["ret_5d"] >= self.settings.min_ret_5d]
        if self.settings.min_rs_index_10d is not None:
            frame = frame[frame["rs_index_10d"] >= self.settings.min_rs_index_10d]

        if (
            self.settings.max_turnover_quantile is not None
            and frame["turnover_5d"].notna().sum() >= 10
        ):
            turnover_cutoff = frame["turnover_5d"].quantile(self.settings.max_turnover_quantile)
            frame = frame[frame["turnover_5d"] <= turnover_cutoff]

        return frame

    def _parse_features(self, trade_date: str) -> pd.DataFrame:
        features = self.repo.get_features(trade_date)
        if features.empty:
            return pd.DataFrame()

        parsed = features["feature_values"].apply(
            lambda value: json.loads(value) if value else {}
        ).apply(pd.Series)
        parsed = pd.concat([features[["symbol"]], parsed], axis=1)
        numeric_columns = [column for column in parsed.columns if column != "symbol"]
        for column in numeric_columns:
            parsed[column] = pd.to_numeric(parsed[column], errors="coerce")
        return parsed
