from __future__ import annotations

from dataclasses import dataclass

from adapters.llm.base import StructuredLLMClient


@dataclass(slots=True)
class RiskReviewerAgent:
    client: StructuredLLMClient
    prompt_loader: object

    def run(self, payload: dict) -> dict:
        _ = payload
        raise NotImplementedError("RiskReviewerAgent business logic is pending.")

