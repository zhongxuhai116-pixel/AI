from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

import pandas as pd

from data.storage.duckdb_client import DuckDBClient


class TableRepository:
    def __init__(self, client: DuckDBClient) -> None:
        self.client = client

    def upsert_dataframe(self, table_name: str, df: pd.DataFrame, key_columns: list[str]) -> int:
        if df is None or df.empty:
            return 0

        temp_view = f"tmp_{uuid.uuid4().hex[:8]}"
        self.client.connection.register(temp_view, df)
        columns = list(df.columns)
        column_list = ", ".join(columns)
        join_clause = " AND ".join([f"target.{col} = source.{col}" for col in key_columns])

        try:
            self.client.execute(
                f"DELETE FROM {table_name} AS target USING {temp_view} AS source WHERE {join_clause}"
            )
            self.client.execute(
                f"INSERT INTO {table_name} ({column_list}) SELECT {column_list} FROM {temp_view}"
            )
        finally:
            self.client.connection.unregister(temp_view)

        return int(len(df))

    def read_dataframe(self, sql: str, params: tuple | None = None) -> pd.DataFrame:
        return self.client.fetch_df(sql, params)


class MarketRepository(TableRepository):
    def save_trade_calendar(self, df: pd.DataFrame) -> int:
        return self.upsert_dataframe("trade_calendar", df, ["trade_date"])

    def save_instruments(self, df: pd.DataFrame) -> int:
        return self.upsert_dataframe("instrument_basic", df, ["symbol"])

    def save_price_daily(self, df: pd.DataFrame) -> int:
        return self.upsert_dataframe("price_daily", df, ["trade_date", "symbol"])

    def save_index_daily(self, df: pd.DataFrame) -> int:
        return self.upsert_dataframe("index_daily", df, ["trade_date", "index_code"])

    def get_latest_open_trade_date(self, as_of_date: str) -> str | None:
        df = self.read_dataframe(
            """
            SELECT trade_date
            FROM trade_calendar
            WHERE is_open = TRUE AND trade_date <= ?
            ORDER BY trade_date DESC
            LIMIT 1
            """,
            (_normalize_date_param(as_of_date),),
        )
        if df.empty:
            return None
        return _normalize_date_value(df.iloc[0]["trade_date"])

    def get_open_trade_dates(self, *, start_date: str, end_date: str) -> list[str]:
        df = self.read_dataframe(
            """
            SELECT trade_date
            FROM trade_calendar
            WHERE is_open = TRUE
              AND trade_date BETWEEN ? AND ?
            ORDER BY trade_date
            """,
            (_normalize_date_param(start_date), _normalize_date_param(end_date)),
        )
        if df.empty:
            return []
        return [_normalize_date_value(value) for value in df["trade_date"].tolist()]

    def get_price_date_bounds(self) -> tuple[str | None, str | None]:
        df = self.read_dataframe(
            """
            SELECT MIN(trade_date) AS min_trade_date, MAX(trade_date) AS max_trade_date
            FROM price_daily
            """
        )
        if df.empty:
            return None, None
        row = df.iloc[0]
        min_trade_date = row.get("min_trade_date")
        max_trade_date = row.get("max_trade_date")
        return (
            None if pd.isna(min_trade_date) else _normalize_date_value(min_trade_date),
            None if pd.isna(max_trade_date) else _normalize_date_value(max_trade_date),
        )

    def get_instruments(self) -> pd.DataFrame:
        return self.read_dataframe(
            """
            SELECT symbol, exchange, board, name, list_date, is_st, industry_l1, industry_l2
            FROM instrument_basic
            ORDER BY symbol
            """
        )

    def get_price_history(
        self,
        *,
        start_date: str,
        end_date: str,
        symbols: list[str] | None = None,
    ) -> pd.DataFrame:
        if symbols is not None and len(symbols) == 0:
            return pd.DataFrame(
                columns=[
                    "trade_date",
                    "symbol",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "amount",
                    "turnover_rate",
                    "adj_factor",
                    "upper_limit_price",
                    "lower_limit_price",
                    "is_suspended",
                ]
            )
        df = self.read_dataframe(
            """
            SELECT
                trade_date,
                symbol,
                open,
                high,
                low,
                close,
                volume,
                amount,
                turnover_rate,
                adj_factor,
                upper_limit_price,
                lower_limit_price,
                is_suspended
            FROM price_daily
            WHERE trade_date BETWEEN ? AND ?
            ORDER BY trade_date, symbol
            """,
            (_normalize_date_param(start_date), _normalize_date_param(end_date)),
        )
        if symbols:
            symbol_set = set(symbols)
            df = df[df["symbol"].isin(symbol_set)].copy()
        return df

    def get_index_history(self, *, start_date: str, end_date: str) -> pd.DataFrame:
        return self.read_dataframe(
            """
            SELECT trade_date, index_code, open, high, low, close, volume, amount
            FROM index_daily
            WHERE trade_date BETWEEN ? AND ?
            ORDER BY trade_date, index_code
            """,
            (_normalize_date_param(start_date), _normalize_date_param(end_date)),
        )

    def delete_price_daily_for_trade_date(self, trade_date: str) -> None:
        self.client.execute(
            "DELETE FROM price_daily WHERE trade_date = ?",
            (_normalize_date_param(trade_date),),
        )


class ResearchRepository(TableRepository):
    def save_stock_pool(self, df: pd.DataFrame) -> int:
        return self.upsert_dataframe("stock_pool_daily", df, ["trade_date", "symbol"])

    def save_features(self, df: pd.DataFrame) -> int:
        return self.upsert_dataframe("features_daily", df, ["trade_date", "symbol"])

    def save_market_regime(self, df: pd.DataFrame) -> int:
        return self.upsert_dataframe("market_regime_daily", df, ["trade_date"])

    def save_model_scores(self, df: pd.DataFrame) -> int:
        return self.upsert_dataframe(
            "model_scores_daily",
            df,
            ["trade_date", "symbol", "horizon", "model_name"],
        )

    def save_signals(self, df: pd.DataFrame) -> int:
        return self.upsert_dataframe("signals_daily", df, ["trade_date", "symbol", "horizon"])

    def save_validation_metrics(self, df: pd.DataFrame) -> int:
        return self.upsert_dataframe(
            "validation_metrics",
            df,
            ["run_id", "horizon", "metric_name"],
        )

    def save_daily_report(self, df: pd.DataFrame) -> int:
        return self.upsert_dataframe("daily_reports", df, ["trade_date", "horizon"])

    def get_validation_metrics(
        self,
        *,
        run_id: str | None = None,
        horizon: int | None = None,
    ) -> pd.DataFrame:
        sql = """
            SELECT run_id, horizon, metric_name, metric_value
            FROM validation_metrics
            WHERE 1 = 1
        """
        params: list[Any] = []
        if run_id is not None:
            sql += " AND run_id = ?"
            params.append(run_id)
        if horizon is not None:
            sql += " AND horizon = ?"
            params.append(horizon)
        sql += " ORDER BY run_id, horizon, metric_name"
        return self.read_dataframe(sql, tuple(params))

    def delete_stock_pool_for_trade_date(self, trade_date: str) -> None:
        self.client.execute(
            "DELETE FROM stock_pool_daily WHERE trade_date = ?",
            (_normalize_date_param(trade_date),),
        )

    def get_stock_pool(self, trade_date: str, *, eligible_only: bool = False) -> pd.DataFrame:
        sql = """
            SELECT trade_date, symbol, eligible, reject_reason_codes, list_days, liquidity_score
            FROM stock_pool_daily
            WHERE trade_date = ?
        """
        params: list[Any] = [_normalize_date_param(trade_date)]
        if eligible_only:
            sql += " AND eligible = TRUE"
        sql += " ORDER BY symbol"
        return self.read_dataframe(sql, tuple(params))

    def delete_features_for_trade_date(self, trade_date: str) -> None:
        self.client.execute(
            "DELETE FROM features_daily WHERE trade_date = ?",
            (_normalize_date_param(trade_date),),
        )

    def get_features(self, trade_date: str) -> pd.DataFrame:
        return self.read_dataframe(
            """
            SELECT trade_date, symbol, feature_values
            FROM features_daily
            WHERE trade_date = ?
            ORDER BY symbol
            """,
            (_normalize_date_param(trade_date),),
        )

    def delete_market_regime_for_trade_date(self, trade_date: str) -> None:
        self.client.execute(
            "DELETE FROM market_regime_daily WHERE trade_date = ?",
            (_normalize_date_param(trade_date),),
        )

    def get_market_regime(self, trade_date: str) -> pd.DataFrame:
        return self.read_dataframe(
            """
            SELECT
                trade_date,
                regime_label,
                style_label,
                breadth_up_ratio,
                limit_up_count,
                limit_down_count,
                volume_heat,
                extra_payload
            FROM market_regime_daily
            WHERE trade_date = ?
            """,
            (_normalize_date_param(trade_date),),
        )

    def delete_model_scores_for_trade_date(self, trade_date: str) -> None:
        self.client.execute(
            "DELETE FROM model_scores_daily WHERE trade_date = ?",
            (_normalize_date_param(trade_date),),
        )

    def get_model_scores(self, trade_date: str, *, horizon: int | None = None) -> pd.DataFrame:
        sql = """
            SELECT
                trade_date,
                symbol,
                horizon,
                model_name,
                score_raw,
                score_rank,
                score_bucket
            FROM model_scores_daily
            WHERE trade_date = ?
        """
        params: list[Any] = [_normalize_date_param(trade_date)]
        if horizon is not None:
            sql += " AND horizon = ?"
            params.append(horizon)
        sql += " ORDER BY horizon, score_rank, symbol"
        return self.read_dataframe(sql, tuple(params))

    def delete_signals_for_trade_date(self, trade_date: str) -> None:
        self.client.execute(
            "DELETE FROM signals_daily WHERE trade_date = ?",
            (_normalize_date_param(trade_date),),
        )

    def get_signals(self, trade_date: str, *, horizon: int | None = None) -> pd.DataFrame:
        sql = """
            SELECT
                trade_date,
                symbol,
                horizon,
                final_rank,
                action,
                target_weight,
                rule_tags,
                blocked_reason
            FROM signals_daily
            WHERE trade_date = ?
        """
        params: list[Any] = [_normalize_date_param(trade_date)]
        if horizon is not None:
            sql += " AND horizon = ?"
            params.append(horizon)
        sql += " ORDER BY horizon, final_rank, symbol"
        return self.read_dataframe(sql, tuple(params))


class LogRepository(TableRepository):
    def save_ai_calls(self, df: pd.DataFrame) -> int:
        return self.upsert_dataframe("ai_calls", df, ["run_id", "call_id"])


def _normalize_date_param(value: str | date | datetime) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _normalize_date_value(value: Any) -> str:
    if hasattr(value, "date"):
        try:
            return value.date().isoformat()
        except TypeError:
            pass
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)
