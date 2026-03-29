from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from data.storage.repositories import ResearchRepository
from infra.config.settings import StrategySettings


@dataclass(slots=True)
class RuleEngine:
    settings: StrategySettings
    repo: ResearchRepository

    def run(self, trade_date: str, horizon: int) -> int:
        scores = self.repo.get_model_scores(trade_date=trade_date, horizon=horizon)
        pool = self.repo.get_stock_pool(trade_date=trade_date, eligible_only=True)
        if scores.empty or pool.empty:
            return 0

        candidates = scores.merge(pool[["symbol"]], on="symbol", how="inner")
        candidates = candidates.sort_values(["score_rank", "symbol"], ignore_index=True)
        candidates = candidates.head(self.settings.top_n).copy()
        if candidates.empty:
            return 0

        weight = 1.0 / len(candidates)
        candidates["trade_date"] = trade_date
        candidates["horizon"] = horizon
        candidates["final_rank"] = range(1, len(candidates) + 1)
        candidates["action"] = "BUY_CANDIDATE"
        candidates["target_weight"] = weight
        candidates["rule_tags"] = "mainboard|baseline|t_plus_1"
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
