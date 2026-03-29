from __future__ import annotations

import pandas as pd

from infra.config.settings import PolicyEventSettings, PolicySettings, PolicyThemeSettings
from strategy.policy.policy_overlay_service import PolicyOverlayService


def test_policy_overlay_applies_bonus_only_when_theme_heat_and_event_are_confirmed():
    service = PolicyOverlayService(
        settings=PolicySettings(
            enabled=True,
            max_total_bonus=0.08,
            min_theme_match_count=2,
            min_theme_positive_ratio=0.55,
            min_theme_amount_ratio_5d=1.0,
            sentiment_multiplier_cap=1.35,
            fresh_event_days=10,
            decay_event_days=45,
            event_decay_floor=0.35,
            themes=[
                PolicyThemeSettings(
                    name="ai_plus",
                    label="人工智能+",
                    start_date="2026-03-01",
                    end_date="2026-03-31",
                    weight=0.05,
                    summary="test",
                    source_url="https://example.com/theme",
                    industries=["I 信息技术"],
                    name_keywords=["机器人"],
                    symbols=[],
                    events=[
                        PolicyEventSettings(
                            date="2026-03-05",
                            title="政府工作报告继续深化人工智能+",
                            source_url="https://example.com/event",
                        )
                    ],
                )
            ],
        )
    )

    candidates = pd.DataFrame(
        [
            {
                "symbol": "000001",
                "name": "机器人科技",
                "industry_l1": None,
                "score_raw": 0.8,
                "ret_5d": 0.06,
                "rs_index_10d": 0.04,
                "amount_ratio_5d": 1.3,
            },
            {
                "symbol": "000002",
                "name": "普通制造",
                "industry_l1": "I 信息技术",
                "score_raw": 0.7,
                "ret_5d": 0.04,
                "rs_index_10d": 0.03,
                "amount_ratio_5d": 1.2,
            },
            {
                "symbol": "000003",
                "name": "传统消费",
                "industry_l1": "F 批发零售",
                "score_raw": 0.9,
                "ret_5d": -0.01,
                "rs_index_10d": -0.02,
                "amount_ratio_5d": 0.8,
            },
        ]
    )

    enriched, context = service.apply(trade_date="2026-03-15", candidates_df=candidates)

    assert context["status"] == "ACTIVE"
    assert context["theme_sentiment_label"] in {"warm", "hot"}
    assert context["matched_symbols"] == 2
    assert enriched.loc[enriched["symbol"] == "000001", "policy_bonus"].iloc[0] > 0.0
    assert enriched.loc[enriched["symbol"] == "000002", "policy_bonus"].iloc[0] > 0.0
    assert enriched.loc[enriched["symbol"] == "000003", "policy_bonus"].iloc[0] == 0.0
    assert (
        enriched.loc[enriched["symbol"] == "000001", "policy_sentiment_label"].iloc[0]
        in {"warm", "hot"}
    )
    assert context["active_themes"][0]["event_label"] == "fresh"


def test_policy_overlay_skips_bonus_when_theme_is_cold():
    service = PolicyOverlayService(
        settings=PolicySettings(
            enabled=True,
            max_total_bonus=0.08,
            min_theme_match_count=2,
            min_theme_positive_ratio=0.55,
            min_theme_amount_ratio_5d=1.0,
            sentiment_multiplier_cap=1.35,
            fresh_event_days=10,
            decay_event_days=45,
            event_decay_floor=0.35,
            themes=[
                PolicyThemeSettings(
                    name="consumer_refresh",
                    label="两新扩围",
                    start_date="2026-01-01",
                    end_date="2026-12-31",
                    weight=0.05,
                    summary="test",
                    source_url="https://example.com/theme",
                    industries=["F 批发零售"],
                    name_keywords=[],
                    symbols=[],
                    events=[
                        PolicyEventSettings(
                            date="2026-02-10",
                            title="持续放大两新政策效能",
                            source_url="https://example.com/event",
                        )
                    ],
                )
            ],
        )
    )

    candidates = pd.DataFrame(
        [
            {
                "symbol": "000010",
                "name": "零售一号",
                "industry_l1": "F 批发零售",
                "score_raw": 0.7,
                "ret_5d": -0.03,
                "rs_index_10d": -0.02,
                "amount_ratio_5d": 0.8,
            },
            {
                "symbol": "000011",
                "name": "零售二号",
                "industry_l1": "F 批发零售",
                "score_raw": 0.6,
                "ret_5d": 0.01,
                "rs_index_10d": -0.01,
                "amount_ratio_5d": 0.9,
            },
        ]
    )

    enriched, context = service.apply(trade_date="2026-03-15", candidates_df=candidates)

    assert context["status"] == "ACTIVE"
    assert context["theme_sentiment_label"] == "cold"
    assert context["matched_symbols"] == 0
    assert float(enriched["policy_bonus"].sum()) == 0.0
    assert context["active_themes"][0]["event_label"] in {"decay", "fresh"}


def test_policy_overlay_event_expiry_disables_bonus_after_decay_window():
    service = PolicyOverlayService(
        settings=PolicySettings(
            enabled=True,
            max_total_bonus=0.08,
            min_theme_match_count=1,
            min_theme_positive_ratio=0.0,
            min_theme_amount_ratio_5d=0.0,
            sentiment_multiplier_cap=1.35,
            fresh_event_days=5,
            decay_event_days=20,
            event_decay_floor=0.35,
            themes=[
                PolicyThemeSettings(
                    name="ai_plus",
                    label="人工智能+",
                    start_date="2026-01-01",
                    end_date="2026-12-31",
                    weight=0.05,
                    summary="test",
                    source_url="https://example.com/theme",
                    industries=["I 信息技术"],
                    name_keywords=[],
                    symbols=[],
                    events=[
                        PolicyEventSettings(
                            date="2026-01-01",
                            title="旧事件",
                            source_url="https://example.com/event",
                        )
                    ],
                )
            ],
        )
    )

    candidates = pd.DataFrame(
        [
            {
                "symbol": "000100",
                "name": "科技一号",
                "industry_l1": "I 信息技术",
                "score_raw": 0.9,
                "ret_5d": 0.06,
                "rs_index_10d": 0.04,
                "amount_ratio_5d": 1.4,
            }
        ]
    )

    enriched, context = service.apply(trade_date="2026-02-15", candidates_df=candidates)

    assert context["active_themes"][0]["event_label"] == "expired"
    assert context["matched_symbols"] == 0
    assert float(enriched["policy_bonus"].sum()) == 0.0
