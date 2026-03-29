from __future__ import annotations


def build_markdown_template(context: dict) -> str:
    market_regime = context.get("market_regime", {})
    signals_by_horizon = context.get("signals_by_horizon", {})
    ai_outputs = context.get("ai_outputs", {})
    validation_outputs = context.get("validation_outputs", {})
    market_ai = ai_outputs.get("market_summary", {})
    stock_ai = ai_outputs.get("stock_explanations", [])
    validation_summaries = validation_outputs.get("summaries", {})

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
        for horizon in sorted(validation_summaries):
            summary = validation_summaries[horizon]
            lines.append(f"### Horizon {horizon}D")
            lines.append(f"- Signal days: {int(summary.get('signal_days', 0))}")
            lines.append(f"- Trade count: {int(summary.get('trade_count', 0))}")
            lines.append(f"- Avg trade return: {summary.get('avg_trade_return', 0.0):.2%}")
            lines.append(f"- Win rate: {summary.get('win_rate', 0.0):.2%}")
            lines.append(f"- Cumulative return: {summary.get('cumulative_return', 0.0):.2%}")
            lines.append(f"- Max drawdown: {summary.get('max_drawdown', 0.0):.2%}")
            lines.append("")

    lines.extend(["## Signals"])

    if not signals_by_horizon:
        lines.append("- No candidate signals for this run.")
        return "\n".join(lines) + "\n"

    for horizon in sorted(signals_by_horizon):
        lines.append(f"### Horizon {horizon}D")
        for row in signals_by_horizon[horizon][:10]:
            name = row.get("name", "")
            display = f"{row.get('symbol', '')} {name}".strip()
            lines.append(
                f"- #{row.get('final_rank', '-')}: {display} | weight={row.get('target_weight', 0):.4f}"
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
