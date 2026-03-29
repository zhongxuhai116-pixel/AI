from __future__ import annotations

import json
from dataclasses import dataclass, field

import pandas as pd

from data.storage.repositories import ResearchRepository
from infra.config.settings import StrategySettings
from strategy.policy.policy_overlay_service import PolicyOverlayService


@dataclass(slots=True)
class RuleEngine:
    settings: StrategySettings
    repo: ResearchRepository
    policy_service: PolicyOverlayService | None = None
    instruments_df: pd.DataFrame | None = None
    last_run_context: dict[int, dict] = field(default_factory=dict)

    def run(self, trade_date: str, horizon: int) -> int:
        scores = self.repo.get_model_scores(trade_date=trade_date, horizon=horizon)
        pool = self.repo.get_stock_pool(trade_date=trade_date, eligible_only=True)
        features = self._parse_features(trade_date)
        if scores.empty or pool.empty or features.empty:
            self.last_run_context[horizon] = self._build_policy_context(
                trade_date=trade_date,
                status="NO_INPUT",
            )
            return 0

        candidates = (
            scores.merge(pool[["symbol"]], on="symbol", how="inner")
            .merge(features, on="symbol", how="inner")
            .sort_values(["score_rank", "symbol"], ignore_index=True)
        )
        if self.instruments_df is not None and not self.instruments_df.empty:
            candidates = candidates.merge(
                self.instruments_df[
                    [column for column in ["symbol", "name", "industry_l1", "industry_l2"] if column in self.instruments_df.columns]
                ],
                on="symbol",
                how="left",
            )
        candidates = self._apply_feature_filters(candidates)
        if candidates.empty:
            self.last_run_context[horizon] = self._build_policy_context(
                trade_date=trade_date,
                status="NO_CANDIDATES",
            )
            return 0

        policy_context = {"active_themes": []}
        if self.policy_service is not None:
            candidates, policy_context = self.policy_service.apply(
                trade_date=trade_date,
                candidates_df=candidates,
            )
        else:
            candidates["policy_bonus"] = 0.0
            candidates["policy_tags"] = ""
            candidates["policy_sentiment_label"] = ""
        candidates["score_raw_plus_policy"] = pd.to_numeric(
            candidates["score_raw"], errors="coerce"
        ).fillna(0.0) + pd.to_numeric(
            candidates["policy_bonus"], errors="coerce"
        ).fillna(0.0)

        if not self._market_is_tradeable(trade_date):
            policy_context["status"] = "MARKET_BLOCKED"
            self.last_run_context[horizon] = policy_context
            return 0

        candidates = candidates.sort_values(
            ["score_raw_plus_policy", "score_rank", "symbol"],
            ascending=[False, True, True],
            ignore_index=True,
        )
        candidates = candidates.head(self.settings.top_n).copy()
        self.last_run_context[horizon] = policy_context
        if candidates.empty:
            return 0

        weight = 1.0 / len(candidates)
        candidates["trade_date"] = trade_date
        candidates["horizon"] = horizon
        candidates["final_rank"] = range(1, len(candidates) + 1)
        candidates["action"] = "BUY_CANDIDATE"
        candidates["target_weight"] = weight
        candidates["rule_tags"] = candidates.apply(
            lambda row: self._build_rule_tags(
                policy_tags=str(row.get("policy_tags", "")),
                policy_sentiment_label=str(row.get("policy_sentiment_label", "")),
            ),
            axis=1,
        )
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

    def _build_policy_context(self, *, trade_date: str, status: str) -> dict:
        if self.policy_service is None:
            return {
                "status": status,
                "trade_date": trade_date,
                "theme_sentiment_label": "inactive",
                "active_bonus_count": 0,
                "active_themes": [],
                "matched_candidates": 0,
                "matched_bonus_candidates": 0,
                "matched_signals": 0,
                "matched_symbols": 0,
            }
        context = self.policy_service.build_context(trade_date=trade_date)
        context["status"] = status
        return context

    @staticmethod
    def _build_rule_tags(*, policy_tags: str, policy_sentiment_label: str) -> str:
        base_tags = ["mainboard", "baseline", "t_plus_1", "sentiment_gate"]
        if policy_tags:
            base_tags.append("policy_gate")
        if policy_sentiment_label in {"warm", "hot"}:
            base_tags.append(f"policy_{policy_sentiment_label}")
        if policy_tags:
            base_tags.extend([tag for tag in policy_tags.split("|") if tag])
        return "|".join(dict.fromkeys(base_tags))
