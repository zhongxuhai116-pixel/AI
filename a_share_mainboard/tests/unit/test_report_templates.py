from __future__ import annotations

from strategy.secretary.report_templates import build_markdown_template


def test_report_template_places_primary_horizon_first():
    context = {
        "trade_date": "2026-03-27",
        "market_regime": {
            "regime_label": "neutral",
            "style_label": "trend",
            "breadth_up_ratio": 0.5,
            "volume_heat": "warm",
        },
        "signals_by_horizon": {
            5: [{"symbol": "000001", "final_rank": 1, "target_weight": 0.1, "rule_tags": ""}],
            10: [{"symbol": "000002", "final_rank": 1, "target_weight": 0.1, "rule_tags": ""}],
        },
        "ai_outputs": {},
        "validation_outputs": {
            "summaries": {
                5: {"signal_days": 1, "trade_count": 1, "avg_trade_return": 0.01, "win_rate": 1.0, "cumulative_return": 0.01, "max_drawdown": 0.0},
                10: {"signal_days": 1, "trade_count": 1, "avg_trade_return": 0.02, "win_rate": 1.0, "cumulative_return": 0.02, "max_drawdown": 0.0},
            }
        },
        "policy_outputs": {},
        "strategy_profile": {"primary_horizon": 10, "auxiliary_horizons": [5]},
    }

    markdown = build_markdown_template(context)

    primary_index = markdown.index("Primary Horizon 10D")
    auxiliary_index = markdown.index("Auxiliary Horizon 5D")
    assert primary_index < auxiliary_index
