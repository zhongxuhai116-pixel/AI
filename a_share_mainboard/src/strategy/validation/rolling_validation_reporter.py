from __future__ import annotations


class RollingValidationReporter:
    def build_report(self, result: dict, horizon: int) -> str:
        stability = result.get("stability_summary", {})
        windows = result.get("windows", [])

        lines = [
            "# Rolling Validation Report",
            "",
            f"Run ID: {result.get('run_id', '')}",
            f"Window: {result.get('start_date', '')} -> {result.get('end_date', '')}",
            f"Horizon: {horizon}D",
            f"Rolling window size: {result.get('window_size', 0)} trading days",
            f"Step size: {result.get('step_size', 0)} trading days",
            "",
            "## Stability Summary",
            f"- Window count: {int(stability.get('window_count', 0) or 0)}",
            f"- Positive window ratio: {stability.get('positive_window_ratio', 0.0):.2%}",
            f"- Avg cumulative return: {stability.get('avg_cumulative_return', 0.0):.2%}",
            f"- Median cumulative return: {stability.get('median_cumulative_return', 0.0):.2%}",
            f"- Avg win rate: {stability.get('avg_win_rate', 0.0):.2%}",
            f"- Policy outperformance ratio: {stability.get('policy_outperformance_ratio', 0.0):.2%}",
        ]

        best_window = stability.get("best_window", {})
        if best_window:
            lines.append(
                f"- Best window: {best_window.get('start_date', '')} -> {best_window.get('end_date', '')} | cumulative={best_window.get('cumulative_return', 0.0):.2%}"
            )
        worst_window = stability.get("worst_window", {})
        if worst_window:
            lines.append(
                f"- Worst window: {worst_window.get('start_date', '')} -> {worst_window.get('end_date', '')} | cumulative={worst_window.get('cumulative_return', 0.0):.2%}"
            )

        if windows:
            lines.extend(["", "## Rolling Windows"])
            for item in windows:
                summary = item.get("summary", {})
                policy_review = item.get("policy_review", {})
                policy_group = policy_review.get("policy_group", {})
                non_policy_group = policy_review.get("non_policy_group", {})
                lines.append(
                    f"- {item.get('start_date', '')} -> {item.get('end_date', '')}: cumulative={summary.get('cumulative_return', 0.0):.2%} | win={summary.get('win_rate', 0.0):.2%} | policy_avg={policy_group.get('avg_trade_return', 0.0):.2%} | non_policy_avg={non_policy_group.get('avg_trade_return', 0.0):.2%}"
                )

        return "\n".join(lines).rstrip() + "\n"
