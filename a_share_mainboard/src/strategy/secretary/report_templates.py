from __future__ import annotations


def build_markdown_template(context: dict) -> str:
    market_regime = context.get("market_regime", {})
    signals_by_horizon = context.get("signals_by_horizon", {})
    ai_outputs = context.get("ai_outputs", {})
    validation_outputs = context.get("validation_outputs", {})
    policy_outputs = context.get("policy_outputs", {})
    market_ai = ai_outputs.get("market_summary", {})
    stock_ai = ai_outputs.get("stock_explanations", [])
    validation_summaries = validation_outputs.get("summaries", {})
    validation_policy_reviews = validation_outputs.get("policy_reviews", {})
    validation_universe_review = validation_outputs.get("universe_review", {})
    policy_themes = policy_outputs.get("active_themes", [])
    strategy_profile = context.get("strategy_profile", {})
    primary_horizon = strategy_profile.get("primary_horizon")
    auxiliary_horizons = strategy_profile.get("auxiliary_horizons", [])

    lines = [
        "# Daily Report",
        "",
        f"Trade date: {context['trade_date']}",
        "",
        "## Market Regime",
        f"- Regime: {market_regime.get('regime_label', 'unknown')}",
        f"- Style: {market_regime.get('style_label', 'unknown')}",
        f"- Breadth up ratio: {market_regime.get('breadth_up_ratio', 0.0):.4f}",
        f"- Volume heat: {market_regime.get('volume_heat', 'unknown')}",
        "",
        "## Strategy Profile",
        f"- Primary horizon: {primary_horizon if primary_horizon is not None else 'n/a'}D",
        f"- Auxiliary horizons: {', '.join(str(value) + 'D' for value in auxiliary_horizons) if auxiliary_horizons else 'none'}",
        "",
    ]

    if market_ai:
        lines.extend(
            [
                "## AI Summary",
                f"- Style label: {market_ai.get('market_style_label', 'unknown')}",
                f"- Summary: {market_ai.get('market_summary', '')}",
            ]
        )
        for note in market_ai.get("market_risk_notes", [])[:3]:
            lines.append(f"- Risk: {note}")
        lines.append("")

    if validation_summaries:
        lines.append("## Validation Snapshot")
        if validation_universe_review:
            lines.append(
                f"- Universe: {int(validation_universe_review.get('instrument_count', 0) or 0)} instruments"
            )
            lines.append(
                f"- Avg eligible pool: {validation_universe_review.get('avg_eligible_pool', 0.0):.1f}"
            )
            lines.append(
                f"- Avg feature-ready pool: {validation_universe_review.get('avg_feature_ready', 0.0):.1f}"
            )
            lines.append(
                f"- Avg daily signals (total): {validation_universe_review.get('avg_daily_signals_total', validation_universe_review.get('avg_daily_signals', 0.0)):.1f}"
            )
            lines.append(
                f"- Avg daily signals (per horizon): {validation_universe_review.get('avg_daily_signals_per_horizon', validation_universe_review.get('avg_daily_signals', 0.0)):.1f}"
            )
            lines.append("")
        for horizon in _ordered_horizons(validation_summaries, primary_horizon):
            summary = validation_summaries[horizon]
            lines.append(f"### { _horizon_label(horizon, primary_horizon) }")
            lines.append(f"- Signal days: {int(summary.get('signal_days', 0))}")
            lines.append(f"- Trade count: {int(summary.get('trade_count', 0))}")
            lines.append(f"- Avg trade return: {summary.get('avg_trade_return', 0.0):.2%}")
            lines.append(f"- Win rate: {summary.get('win_rate', 0.0):.2%}")
            lines.append(f"- Cumulative return: {summary.get('cumulative_return', 0.0):.2%}")
            lines.append(f"- Max drawdown: {summary.get('max_drawdown', 0.0):.2%}")
            lines.append("")

    if validation_policy_reviews:
        lines.append("## Policy Validation")
        for horizon in _ordered_horizons(validation_policy_reviews, primary_horizon):
            review = validation_policy_reviews[horizon]
            policy_group = review.get("policy_group", {})
            non_policy_group = review.get("non_policy_group", {})
            theme_groups = review.get("theme_groups", [])
            lines.append(f"### { _horizon_label(horizon, primary_horizon) }")
            lines.append(
                f"- Policy trades: {int(policy_group.get('trade_count', 0) or 0)} | avg={policy_group.get('avg_trade_return', 0.0):.2%} | win={policy_group.get('win_rate', 0.0):.2%}"
            )
            lines.append(
                f"- Non-policy trades: {int(non_policy_group.get('trade_count', 0) or 0)} | avg={non_policy_group.get('avg_trade_return', 0.0):.2%} | win={non_policy_group.get('win_rate', 0.0):.2%}"
            )
            if theme_groups:
                top_theme = theme_groups[0]
                lines.append(
                    f"- Best policy theme: {top_theme.get('theme', '')} | avg={top_theme.get('avg_trade_return', 0.0):.2%} | trades={int(top_theme.get('trade_count', 0) or 0)}"
                )
            lines.append("")

    if policy_themes:
        lines.append("## Policy Themes")
        matched_candidates = int(policy_outputs.get("matched_candidates", 0) or 0)
        matched_bonus_candidates = int(policy_outputs.get("matched_bonus_candidates", 0) or 0)
        matched_signals = int(policy_outputs.get("matched_signals", 0) or 0)
        lines.append(
            f"- Theme sentiment: {policy_outputs.get('theme_sentiment_label', 'inactive')}"
        )
        lines.append(f"- Matched candidates: {matched_candidates}")
        lines.append(f"- Bonus-ready candidates: {matched_bonus_candidates}")
        lines.append(f"- Matched signals: {matched_signals}")
        for theme in policy_themes:
            lines.append(
                f"- {theme.get('label', theme.get('name', 'unknown'))}: {theme.get('summary', '')}"
            )
            lines.append(
                "  "
                f"heat={theme.get('sentiment_label', 'inactive')} | "
                f"event={theme.get('event_label', 'ongoing')} | "
                f"matches={int(theme.get('matched_count', 0) or 0)} | "
                f"positive_ratio={theme.get('positive_ratio', 0.0):.2%} | "
                f"avg_ret_5d={theme.get('avg_ret_5d', 0.0):.2%} | "
                f"avg_rs_index_10d={theme.get('avg_rs_index_10d', 0.0):.2%} | "
                f"avg_amount_ratio_5d={theme.get('avg_amount_ratio_5d', 0.0):.2f} | "
                f"bonus={theme.get('effective_bonus', 0.0):.4f}"
            )
            latest_event_date = theme.get("latest_event_date", "")
            latest_event_title = theme.get("latest_event_title", "")
            if latest_event_date or latest_event_title:
                lines.append(
                    "  "
                    f"latest_event={latest_event_date} {latest_event_title}".rstrip()
                )
            latest_event_source_url = theme.get("latest_event_source_url", "")
            if latest_event_source_url:
                lines.append(f"  Event source: {latest_event_source_url}")
            source_url = theme.get("source_url", "")
            if source_url:
                lines.append(f"  Source: {source_url}")
            watchlist_candidates = theme.get("watchlist_candidates", [])
            if watchlist_candidates:
                lines.append("  Watchlist sample:")
                for item in watchlist_candidates[:3]:
                    display = f"{item.get('symbol', '')} {item.get('name', '')}".strip()
                    ret_5d = float(item.get("ret_5d", 0.0) or 0.0)
                    amount_ratio_5d = float(item.get("amount_ratio_5d", 0.0) or 0.0)
                    lines.append(
                        "  "
                        f"- {display} | industry={item.get('industry_l1', '')} | "
                        f"ret_5d={ret_5d:.2%} | "
                        f"amount_ratio_5d={amount_ratio_5d:.2f}"
                    )
        lines.append("")

    lines.extend(["## Signals"])

    if not signals_by_horizon:
        lines.append("- No candidate signals for this run.")
        return "\n".join(lines) + "\n"

    for horizon in _ordered_horizons(signals_by_horizon, primary_horizon):
        lines.append(f"### { _horizon_label(horizon, primary_horizon) }")
        for row in signals_by_horizon[horizon][:10]:
            name = row.get("name", "")
            display = f"{row.get('symbol', '')} {name}".strip()
            policy_tags = row.get("rule_tags", "")
            policy_suffix = ""
            heat_suffix = ""
            if "policy_gate" in str(policy_tags):
                theme_tags = [
                    tag
                    for tag in str(policy_tags).split("|")
                    if tag
                    not in {
                        "mainboard",
                        "baseline",
                        "t_plus_1",
                        "sentiment_gate",
                        "policy_gate",
                        "policy_hot",
                        "policy_warm",
                    }
                ]
                if theme_tags:
                    policy_suffix = f" | policy={','.join(theme_tags)}"
                if "policy_hot" in str(policy_tags):
                    heat_suffix = " | heat=hot"
                elif "policy_warm" in str(policy_tags):
                    heat_suffix = " | heat=warm"
            lines.append(
                f"- #{row.get('final_rank', '-')}: {display} | weight={row.get('target_weight', 0):.4f}{policy_suffix}{heat_suffix}"
            )
        lines.append("")

    if stock_ai:
        lines.append("## AI Stock Notes")
        for item in stock_ai[:10]:
            explanation = item.get("explanation", {})
            symbol = item.get("symbol", "")
            lines.append(
                f"- {symbol}: {explanation.get('technical_summary', '')}"
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def build_json_template(context: dict) -> dict:
    return context


def _ordered_horizons(payload: dict, primary_horizon: int | None) -> list[int]:
    horizons = [int(value) for value in payload.keys()]
    unique = sorted(set(horizons))
    if primary_horizon is None or primary_horizon not in unique:
        return unique
    return [primary_horizon] + [value for value in unique if value != primary_horizon]


def _horizon_label(horizon: int, primary_horizon: int | None) -> str:
    if primary_horizon is not None and horizon == primary_horizon:
        return f"Primary Horizon {horizon}D"
    return f"Auxiliary Horizon {horizon}D"
