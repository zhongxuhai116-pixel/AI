from __future__ import annotations

import os
from dataclasses import dataclass

import pandas as pd

from adapters.feishu.webhook_client import FeishuWebhookClient
from infra.config.settings import FeishuSettings
from infra.exceptions import ProviderUnavailableError


@dataclass(slots=True)
class FeishuSyncService:
    settings: FeishuSettings

    def send_daily_summary(
        self,
        *,
        trade_date: str,
        market_regime: dict,
        signals_df: pd.DataFrame,
        ai_outputs: dict | None,
        report_path: str,
        validation_outputs: dict | None = None,
        policy_outputs: dict | None = None,
        strategy_profile: dict | None = None,
    ) -> dict:
        if not self.settings.enabled:
            return {"status": "SKIPPED", "message": "Feishu is disabled by configuration."}

        webhook_url = os.getenv(self.settings.webhook_url_env, "")
        if not webhook_url:
            return {
                "status": "SKIPPED",
                "message": f"{self.settings.webhook_url_env} is not set.",
            }

        secret = os.getenv(self.settings.signing_secret_env, "") or None
        try:
            client = FeishuWebhookClient(
                webhook_url=webhook_url,
                timeout_seconds=self.settings.timeout_seconds,
                signing_secret=secret,
            )
            message = self._build_message(
                trade_date=trade_date,
                market_regime=market_regime,
                signals_df=signals_df,
                ai_outputs=ai_outputs or {},
                report_path=report_path,
                validation_outputs=validation_outputs or {},
                policy_outputs=policy_outputs or {},
                strategy_profile=strategy_profile or {},
            )
            response = client.send_text(message)
            return {"status": "SUCCESS", "response": response}
        except ProviderUnavailableError as exc:
            return {"status": "SKIPPED", "message": str(exc)}
        except Exception as exc:  # pragma: no cover - network-dependent
            return {"status": "FAILED", "message": str(exc)}

    @staticmethod
    def _build_message(
        *,
        trade_date: str,
        market_regime: dict,
        signals_df: pd.DataFrame,
        ai_outputs: dict,
        report_path: str,
        validation_outputs: dict,
        policy_outputs: dict,
        strategy_profile: dict,
    ) -> str:
        primary_horizon = strategy_profile.get("primary_horizon")
        lines = [
            f"A-share Mainboard Daily {trade_date}",
            f"Market regime: {market_regime.get('regime_label', 'unknown')} / {market_regime.get('style_label', 'unknown')}",
            f"Primary horizon: {primary_horizon if primary_horizon is not None else 'n/a'}D",
        ]

        market_ai = ai_outputs.get("market_summary", {})
        if market_ai:
            lines.append(f"AI summary: {market_ai.get('market_summary', '')}")

        policy_themes = policy_outputs.get("active_themes", [])
        if policy_themes:
            labels = []
            for theme in policy_themes[:3]:
                label = theme.get("label", theme.get("name", ""))
                heat = theme.get("sentiment_label", "inactive")
                event = theme.get("event_label", "ongoing")
                labels.append(f"{label}({heat}/{event})")
            lines.append("Policy themes: " + " | ".join(labels))

        validation_summaries = validation_outputs.get("summaries", {})
        for horizon in _ordered_horizons(validation_summaries, primary_horizon):
            summary = validation_summaries[horizon]
            role = "PRIMARY" if horizon == primary_horizon else "AUX"
            lines.append(
                f"{role} {horizon}D validation: win {summary.get('win_rate', 0.0):.1%} | cumulative {summary.get('cumulative_return', 0.0):.1%}"
            )

        if signals_df is not None and not signals_df.empty:
            top5 = signals_df.sort_values(["horizon", "final_rank"]).head(5)
            for row in top5.to_dict(orient="records"):
                name = row.get("name", "")
                horizon = int(row.get("horizon", 0) or 0)
                role = "P" if primary_horizon is not None and horizon == primary_horizon else "A"
                heat = ""
                tags = str(row.get("rule_tags", ""))
                if "policy_hot" in tags:
                    heat = " | heat=hot"
                elif "policy_warm" in tags:
                    heat = " | heat=warm"
                lines.append(
                    f"{role} {horizon}D #{row.get('final_rank')}: {row.get('symbol')} {name}{heat}".strip()
                )
        else:
            lines.append("No candidate signals today.")

        lines.append(f"Report: {report_path}")
        return "\n".join(lines)


def _ordered_horizons(payload: dict, primary_horizon: int | None) -> list[int]:
    horizons = [int(value) for value in payload.keys()]
    unique = sorted(set(horizons))
    if primary_horizon is None or primary_horizon not in unique:
        return unique
    return [primary_horizon] + [value for value in unique if value != primary_horizon]
