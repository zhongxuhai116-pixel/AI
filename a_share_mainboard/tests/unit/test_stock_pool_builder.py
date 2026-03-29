from __future__ import annotations

import pandas as pd

from data.filters.stock_pool_builder import StockPoolBuilder
from data.storage.duckdb_client import DuckDBClient
from data.storage.repositories import MarketRepository, ResearchRepository
from data.storage.table_bootstrap import bootstrap_core_tables
from infra.config.settings import UniverseSettings


def test_stock_pool_builder_applies_core_mainboard_filters(tmp_path):
    db_path = tmp_path / "stock_pool.duckdb"
    client = DuckDBClient(db_path)
    bootstrap_core_tables(client)

    market_repo = MarketRepository(client)
    research_repo = ResearchRepository(client)

    market_repo.save_instruments(
        pd.DataFrame(
            [
                {
                    "symbol": "000001",
                    "exchange": "SZSE",
                    "board": "MAIN",
                    "name": "平安银行",
                    "list_date": "2000-01-01",
                    "is_st": False,
                    "industry_l1": "金融业",
                    "industry_l2": None,
                },
                {
                    "symbol": "000004",
                    "exchange": "SZSE",
                    "board": "MAIN",
                    "name": "*ST国华",
                    "list_date": "2000-01-01",
                    "is_st": True,
                    "industry_l1": "信息技术",
                    "industry_l2": None,
                },
                {
                    "symbol": "001001",
                    "exchange": "SZSE",
                    "board": "MAIN",
                    "name": "新股样本",
                    "list_date": "2026-01-15",
                    "is_st": False,
                    "industry_l1": "制造业",
                    "industry_l2": None,
                },
                {
                    "symbol": "000011",
                    "exchange": "SZSE",
                    "board": "MAIN",
                    "name": "深物业A",
                    "list_date": "2000-01-01",
                    "is_st": False,
                    "industry_l1": "房地产",
                    "industry_l2": None,
                },
                {
                    "symbol": "000012",
                    "exchange": "SZSE",
                    "board": "MAIN",
                    "name": "南玻A",
                    "list_date": "2000-01-01",
                    "is_st": False,
                    "industry_l1": "制造业",
                    "industry_l2": None,
                },
            ]
        )
    )

    market_repo.save_price_daily(
        pd.DataFrame(
            [
                {
                    "trade_date": "2026-03-27",
                    "symbol": "000001",
                    "open": 10.0,
                    "high": 10.2,
                    "low": 9.9,
                    "close": 10.1,
                    "volume": 1000,
                    "amount": 120_000_000.0,
                    "turnover_rate": 1.0,
                    "adj_factor": None,
                    "upper_limit_price": None,
                    "lower_limit_price": None,
                    "is_suspended": False,
                },
                {
                    "trade_date": "2026-03-27",
                    "symbol": "000004",
                    "open": 5.0,
                    "high": 5.1,
                    "low": 4.9,
                    "close": 5.0,
                    "volume": 1000,
                    "amount": 60_000_000.0,
                    "turnover_rate": 1.0,
                    "adj_factor": None,
                    "upper_limit_price": None,
                    "lower_limit_price": None,
                    "is_suspended": False,
                },
                {
                    "trade_date": "2026-03-27",
                    "symbol": "001001",
                    "open": 15.0,
                    "high": 15.5,
                    "low": 14.8,
                    "close": 15.2,
                    "volume": 1000,
                    "amount": 70_000_000.0,
                    "turnover_rate": 1.0,
                    "adj_factor": None,
                    "upper_limit_price": None,
                    "lower_limit_price": None,
                    "is_suspended": False,
                },
                {
                    "trade_date": "2026-03-27",
                    "symbol": "000011",
                    "open": 8.0,
                    "high": 8.1,
                    "low": 7.9,
                    "close": 8.0,
                    "volume": 1000,
                    "amount": 20_000_000.0,
                    "turnover_rate": 1.0,
                    "adj_factor": None,
                    "upper_limit_price": None,
                    "lower_limit_price": None,
                    "is_suspended": False,
                },
            ]
        )
    )

    builder = StockPoolBuilder(
        settings=UniverseSettings(
            allowed_boards=["MAIN"],
            min_listing_days=120,
            min_avg_amount=50_000_000.0,
            exclude_st=True,
        ),
        market_repo=market_repo,
        repo=research_repo,
    )

    rows = builder.build(trade_date="2026-03-27")
    assert rows == 5

    result = research_repo.read_dataframe(
        """
        SELECT symbol, eligible, reject_reason_codes
        FROM stock_pool_daily
        WHERE trade_date = ?
        ORDER BY symbol
        """,
        ("2026-03-27",),
    )
    result = {
        row["symbol"]: {
            "eligible": row["eligible"],
            "reject_reason_codes": row["reject_reason_codes"],
        }
        for row in result.to_dict(orient="records")
    }

    assert result["000001"]["eligible"] is True
    assert result["000001"]["reject_reason_codes"] == ""

    assert result["000004"]["eligible"] is False
    assert result["000004"]["reject_reason_codes"] == "st"

    assert result["001001"]["eligible"] is False
    assert result["001001"]["reject_reason_codes"] == "listing_days"

    assert result["000011"]["eligible"] is False
    assert result["000011"]["reject_reason_codes"] == "liquidity"

    assert result["000012"]["eligible"] is False
    assert result["000012"]["reject_reason_codes"] == "missing_bar|liquidity"

    client.close()
