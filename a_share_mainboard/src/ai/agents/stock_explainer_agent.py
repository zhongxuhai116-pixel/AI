from __future__ import annotations

from dataclasses import dataclass

from adapters.llm.base import StructuredLLMClient
from domain.schemas import StockExplanationOutput, build_json_schema


@dataclass(slots=True)
class StockExplainerAgent:
    client: StructuredLLMClient
    prompt_loader: object

    def run(self, payload: dict) -> dict:
        request_payload = dict(payload)
        prompt_name = request_payload.pop("_prompt_name", "stock_explainer_v1")
        timeout_seconds = int(request_payload.pop("_timeout_seconds", 30))
        system_prompt = self.prompt_loader.load(prompt_name)
        return self.client.generate_json(
            system_prompt=system_prompt,
            user_payload=request_payload,
            output_schema=build_json_schema(StockExplanationOutput, "stock_explainer_output"),
            timeout_seconds=timeout_seconds,
        )
