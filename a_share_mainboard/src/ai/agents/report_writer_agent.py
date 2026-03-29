from __future__ import annotations

from dataclasses import dataclass

from adapters.llm.base import StructuredLLMClient


@dataclass(slots=True)
class ReportWriterAgent:
    client: StructuredLLMClient
    prompt_loader: object

    def run(self, payload: dict) -> dict:
        _ = payload
        raise NotImplementedError("ReportWriterAgent business logic is pending.")

