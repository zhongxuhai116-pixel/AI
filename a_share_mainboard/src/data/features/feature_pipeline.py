from __future__ import annotations

import json
from dataclasses import dataclass

import pandas as pd

from data.features.liquidity_features import LiquidityFeatureCalculator
from data.features.price_features import PriceFeatureCalculator
from data.features.relative_strength_features import RelativeStrengthFeatureCalculator
from data.storage.repositories import MarketRepository, ResearchRepository
from infra.utils.dates import add_days


@dataclass(slots=True)
class FeaturePipeline:
    market_repo: MarketRepository
    repo: ResearchRepository
    benchmark_index: str = "sh000001"

    def run(self, trade_date: str) -> int:
        pool_df = self.repo.get_stock_pool(trade_date=trade_date, eligible_only=True)
        if pool_df.empty:
            self.repo.delete_features_for_trade_date(trade_date)
            return 0

        symbols = pool_df["symbol"].tolist()
        start_date = add_days(trade_date, -80)
        bars_df = self.market_repo.get_price_history(
            start_date=start_date,
            end_date=trade_date,
            symbols=symbols,
        )
        if bars_df.empty:
            self.repo.delete_features_for_trade_date(trade_date)
            return 0

        index_df = self.market_repo.get_index_history(start_date=start_date, end_date=trade_date)
        index_df = index_df[index_df["index_code"] == self.benchmark_index].copy()

        price_features = PriceFeatureCalculator().compute(bars_df)
        liquidity_features = LiquidityFeatureCalculator().compute(bars_df)
        relative_strength = RelativeStrengthFeatureCalculator().compute(price_features, index_df)

        features = price_features.merge(
            liquidity_features,
            on=["trade_date", "symbol"],
            how="left",
        ).merge(
            relative_strength,
            on=["trade_date", "symbol"],
            how="left",
        )
        features["trade_date"] = pd.to_datetime(features["trade_date"], errors="coerce").dt.date
        features = features[features["trade_date"] == pd.to_datetime(trade_date).date()].copy()
        if features.empty:
            self.repo.delete_features_for_trade_date(trade_date)
            return 0

        feature_columns = [
            "ret_5d",
            "ret_10d",
            "volatility_10d",
            "ma_gap_5",
            "amount_ratio_5d",
            "turnover_5d",
            "rs_index_10d",
        ]
        features = features[["trade_date", "symbol", *feature_columns]].copy()
        for column in feature_columns:
            features[column] = pd.to_numeric(features[column], errors="coerce")

        payload = pd.DataFrame(
            {
                "trade_date": features["trade_date"],
                "symbol": features["symbol"],
                "feature_values": features[feature_columns]
                .apply(
                    lambda row: json.dumps(
                        {
                            key: (None if pd.isna(value) else float(value))
                            for key, value in row.to_dict().items()
                        },
                        ensure_ascii=False,
                    ),
                    axis=1,
                ),
            }
        )
        self.repo.delete_features_for_trade_date(trade_date)
        return self.repo.save_features(payload)
