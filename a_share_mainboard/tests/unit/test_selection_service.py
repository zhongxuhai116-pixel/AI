from __future__ import annotations

import pandas as pd

from strategy.stock_selection.baseline_ranker import BaselineRanker
from strategy.stock_selection.selection_service import SelectionService


class _RepoStub:
    def __init__(self) -> None:
        self.saved_df = pd.DataFrame()
        self.deleted_trade_date = None

    def get_features(self, trade_date: str) -> pd.DataFrame:
        _ = trade_date
        return pd.DataFrame(columns=["trade_date", "symbol", "feature_values"])

    def get_stock_pool(self, trade_date: str, *, eligible_only: bool = False) -> pd.DataFrame:
        _ = trade_date, eligible_only
        return pd.DataFrame(
            [
                {
                    "trade_date": "2026-03-27",
                    "symbol": "000001",
                    "liquidity_score": 10.0,
                },
                {
                    "trade_date": "2026-03-27",
                    "symbol": "000002",
                    "liquidity_score": 5.0,
                },
            ]
        )

    def delete_model_scores_for_trade_date(self, trade_date: str) -> None:
        self.deleted_trade_date = trade_date

    def save_model_scores(self, df: pd.DataFrame) -> int:
        self.saved_df = df.copy()
        return int(len(df))


def test_selection_service_uses_liquidity_fallback_when_features_empty():
    repo = _RepoStub()
    service = SelectionService(
        repo=repo,  # type: ignore[arg-type]
        baseline_ranker=BaselineRanker(factor_weights={"ret_5d": 0.5}),
    )

    saved = service.run(trade_date="2026-03-27", horizons=[10, 5])

    assert saved == 4
    assert repo.deleted_trade_date == "2026-03-27"
    assert set(repo.saved_df["horizon"].tolist()) == {10, 5}
    assert set(repo.saved_df["model_name"].tolist()) == {"baseline_fallback_liquidity"}
    first_row = repo.saved_df.sort_values(["horizon", "score_rank"]).iloc[0]
    assert first_row["symbol"] == "000001"
    assert int(first_row["score_rank"]) == 1
