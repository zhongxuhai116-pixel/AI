from __future__ import annotations

import pandas as pd


class MLRanker:
    def __init__(self, model_name: str) -> None:
        self.model_name = model_name

    def fit(self, train_df: pd.DataFrame, label_col: str) -> None:
        _ = (train_df, label_col)

    def predict(self, score_df: pd.DataFrame) -> pd.DataFrame:
        return score_df

    def save(self, path: str) -> None:
        _ = path

    def load(self, path: str) -> None:
        _ = path

