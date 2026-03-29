from __future__ import annotations

from data.storage.duckdb_client import DuckDBClient


def bootstrap_core_tables(client: DuckDBClient) -> None:
    ddl_statements = [
        """
        CREATE TABLE IF NOT EXISTS trade_calendar (
            trade_date DATE,
            is_open BOOLEAN,
            prev_trade_date DATE,
            next_trade_date DATE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS instrument_basic (
            symbol VARCHAR,
            exchange VARCHAR,
            board VARCHAR,
            name VARCHAR,
            list_date DATE,
            is_st BOOLEAN,
            industry_l1 VARCHAR,
            industry_l2 VARCHAR
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS price_daily (
            trade_date DATE,
            symbol VARCHAR,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            volume DOUBLE,
            amount DOUBLE,
            turnover_rate DOUBLE,
            adj_factor DOUBLE,
            upper_limit_price DOUBLE,
            lower_limit_price DOUBLE,
            is_suspended BOOLEAN
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS index_daily (
            trade_date DATE,
            index_code VARCHAR,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            volume DOUBLE,
            amount DOUBLE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS stock_pool_daily (
            trade_date DATE,
            symbol VARCHAR,
            eligible BOOLEAN,
            reject_reason_codes VARCHAR,
            list_days INTEGER,
            liquidity_score DOUBLE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS features_daily (
            trade_date DATE,
            symbol VARCHAR,
            feature_values VARCHAR
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS market_regime_daily (
            trade_date DATE,
            regime_label VARCHAR,
            style_label VARCHAR,
            breadth_up_ratio DOUBLE,
            limit_up_count INTEGER,
            limit_down_count INTEGER,
            volume_heat VARCHAR,
            extra_payload VARCHAR
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS model_scores_daily (
            trade_date DATE,
            symbol VARCHAR,
            horizon INTEGER,
            model_name VARCHAR,
            score_raw DOUBLE,
            score_rank INTEGER,
            score_bucket INTEGER
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS signals_daily (
            trade_date DATE,
            symbol VARCHAR,
            horizon INTEGER,
            final_rank INTEGER,
            action VARCHAR,
            target_weight DOUBLE,
            rule_tags VARCHAR,
            blocked_reason VARCHAR
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS validation_metrics (
            run_id VARCHAR,
            horizon INTEGER,
            metric_name VARCHAR,
            metric_value DOUBLE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS daily_reports (
            trade_date DATE,
            horizon INTEGER,
            report_markdown VARCHAR,
            report_json VARCHAR
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS ai_calls (
            run_id VARCHAR,
            call_id VARCHAR,
            task_type VARCHAR,
            model VARCHAR,
            prompt_version VARCHAR,
            status VARCHAR,
            request_id VARCHAR,
            payload_json VARCHAR,
            response_json VARCHAR
        )
        """,
    ]

    for statement in ddl_statements:
        client.execute(statement)

