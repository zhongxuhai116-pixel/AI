from __future__ import annotations

from typing import Any, Protocol


class StructuredLLMClient(Protocol):
    def generate_json(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        output_schema: dict[str, Any],
        timeout_seconds: int,
    ) -> dict[str, Any]: ...

