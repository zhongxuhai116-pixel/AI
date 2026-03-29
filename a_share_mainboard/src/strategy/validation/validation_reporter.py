from __future__ import annotations


class ValidationReporter:
    def build_report(self, result: dict, horizon: int) -> str:
        summary = result.get("summary", {})
        policy_review = result.get("policy_review", {})
        universe_review = result.get("universe_review", {})
        policy_group = policy_review.get("policy_group", {})
        non_policy_group = policy_review.get("non_policy_group", {})
        theme_groups = policy_review.get("theme_groups", [])

        lines = [
            "# Validation Report",
            "",
            f"Run ID: {result.get('run_id', '')}",
            f"Window: {result.get('start_date', '')} -> {result.get('end_date', '')}",
            f"Horizon: {horizon}D",
            "",
            "## Universe Scope",
            f"- Instrument count: {int(universe_review.get('instrument_count', 0) or 0)}",
            f"- Avg eligible pool: {universe_review.get('avg_eligible_pool', 0.0):.1f}",
            f"- Avg feature-ready pool: {universe_review.get('avg_feature_ready', 0.0):.1f}",
            f"- Avg daily signals (total): {universe_review.get('avg_daily_signals_total', 0.0):.1f}",
            f"- Avg daily signals (per horizon): {universe_review.get('avg_daily_signals_per_horizon', universe_review.get('avg_daily_signals', 0.0)):.1f}",
            "",
            "## Summary",
            f"- Signal days: {int(summary.get('signal_days', 0) or 0)}",
            f"- Trade count: {int(summary.get('trade_count', 0) or 0)}",
            f"- Avg trade return: {summary.get('avg_trade_return', 0.0):.2%}",
            f"- Win rate: {summary.get('win_rate', 0.0):.2%}",
            f"- Cumulative return: {summary.get('cumulative_return', 0.0):.2%}",
            f"- Max drawdown: {summary.get('max_drawdown', 0.0):.2%}",
            "",
            "## Policy Review",
            f"- Policy trades: {int(policy_group.get('trade_count', 0) or 0)} | avg={policy_group.get('avg_trade_return', 0.0):.2%} | win={policy_group.get('win_rate', 0.0):.2%}",
            f"- Non-policy trades: {int(non_policy_group.get('trade_count', 0) or 0)} | avg={non_policy_group.get('avg_trade_return', 0.0):.2%} | win={non_policy_group.get('win_rate', 0.0):.2%}",
        ]

        if theme_groups:
            lines.extend(["", "## Theme Groups"])
            for row in theme_groups[:5]:
                lines.append(
                    f"- {row.get('theme', '')}: trades={int(row.get('trade_count', 0) or 0)} | avg={row.get('avg_trade_return', 0.0):.2%} | win={row.get('win_rate', 0.0):.2%}"
                )

        return "\n".join(lines).rstrip() + "\n"
