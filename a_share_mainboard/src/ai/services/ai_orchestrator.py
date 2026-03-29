from __future__ import annotations

import json
from dataclasses import dataclass

from ai.guards.fallback_guard import fallback_report
from infra.config.settings import AISettings
from infra.exceptions import AIResponseError, ProviderUnavailableError


@dataclass(slots=True)
class AIOrchestrator:
    settings: AISettings
    market_agent: object | None = None
    stock_agent: object | None = None
    risk_agent: object | None = None
    report_agent: object | None = None
    result_store: object | None = None

    def run_daily(
        self,
        *,
        trade_date: str,
        run_id: str,
        market_payload: dict,
        stock_payloads: list[dict],
    ) -> dict:
        if not self.settings.enabled:
            return fallback_report("AI is disabled by configuration.")
        if self.market_agent is None or self.stock_agent is None:
            return fallback_report("AI agents are not configured.")

        try:
            market_request = dict(market_payload)
            market_request["_prompt_name"] = self.settings.market_prompt_version
            market_request["_timeout_seconds"] = self.settings.timeout_seconds
            market_summary = self.market_agent.run(market_request)
            self._log_call(
                run_id=run_id,
                task_type="market_summary",
                prompt_version=self.settings.market_prompt_version,
                payload=market_payload,
                response=market_summary,
                meta=getattr(self.market_agent.client, "last_response_meta", {}),
            )

            stock_explanations: list[dict] = []
            for stock_payload in stock_payloads[: self.settings.max_symbols_per_day]:
                request_payload = dict(stock_payload)
                request_payload["_prompt_name"] = self.settings.stock_prompt_version
                request_payload["_timeout_seconds"] = self.settings.timeout_seconds
                explanation = self.stock_agent.run(request_payload)
                stock_explanations.append(
                    {
                        "symbol": stock_payload.get("symbol"),
                        "horizon": stock_payload.get("horizon"),
                        "explanation": explanation,
                    }
                )
                self._log_call(
                    run_id=run_id,
                    task_type="stock_explainer",
                    prompt_version=self.settings.stock_prompt_version,
                    payload=stock_payload,
                    response=explanation,
                    meta=getattr(self.stock_agent.client, "last_response_meta", {}),
                )

            return {
                "trade_date": trade_date,
                "status": "SUCCESS",
                "market_summary": market_summary,
                "stock_explanations": stock_explanations,
            }
        except (ProviderUnavailableError, AIResponseError, OSError, ValueError) as exc:
            return fallback_report(str(exc))

    def _log_call(
        self,
        *,
        run_id: str,
        task_type: str,
        prompt_version: str,
        payload: dict,
        response: dict,
        meta: dict,
    ) -> None:
        if self.result_store is None:
            return
        self.result_store.save_call_log(
            {
                "run_id": run_id,
                "call_id": meta.get("call_id", ""),
                "task_type": task_type,
                "model": self.settings.model,
                "prompt_version": prompt_version,
                "status": "SUCCESS",
                "request_id": meta.get("request_id", ""),
                "payload_json": json.dumps(payload, ensure_ascii=False, default=str),
                "response_json": json.dumps(response, ensure_ascii=False, default=str),
            }
        )
