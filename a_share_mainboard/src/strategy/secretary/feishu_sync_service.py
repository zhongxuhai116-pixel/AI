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
    ) -> str:
        lines = [
            f"A股主板日报 {trade_date}",
            f"市场状态: {market_regime.get('regime_label', 'unknown')} / {market_regime.get('style_label', 'unknown')}",
        ]

        market_ai = ai_outputs.get("market_summary", {})
        if market_ai:
            lines.append(f"AI摘要: {market_ai.get('market_summary', '')}")

        policy_themes = policy_outputs.get("active_themes", [])
        if policy_themes:
            labels = []
            for theme in policy_themes[:3]:
                label = theme.get("label", theme.get("name", ""))
                heat = theme.get("sentiment_label", "inactive")
                labels.append(f"{label}({heat})")
            lines.append("政策主线: " + "、".join(labels))

        validation_summaries = validation_outputs.get("summaries", {})
        for horizon in sorted(validation_summaries):
            summary = validation_summaries[horizon]
            lines.append(
                f"{horizon}D验证: 胜率 {summary.get('win_rate', 0.0):.1%} | 累计 {summary.get('cumulative_return', 0.0):.1%}"
            )

        if signals_df is not None and not signals_df.empty:
            top5 = signals_df.sort_values(["horizon", "final_rank"]).head(5)
            for row in top5.to_dict(orient="records"):
                name = row.get("name", "")
                heat = ""
                tags = str(row.get("rule_tags", ""))
                if "policy_hot" in tags:
                    heat = " | heat=hot"
                elif "policy_warm" in tags:
                    heat = " | heat=warm"
                lines.append(
                    f"{row.get('horizon')}D #{row.get('final_rank')}: {row.get('symbol')} {name}{heat}".strip()
                )
        else:
            lines.append("今日无候选信号。")

        lines.append(f"报告: {report_path}")
        return "\n".join(lines)
