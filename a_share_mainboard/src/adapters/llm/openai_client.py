from __future__ import annotations

import json
import os
from typing import Any

import httpx

from infra.exceptions import AIResponseError, ProviderUnavailableError
from infra.utils.ids import generate_call_id


class OpenAIClient:
    def __init__(self, api_key: str | None, model: str, base_url: str, timeout_seconds: int, max_retries: int) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.last_response_meta: dict[str, Any] = {}

        if not self.api_key:
            raise ProviderUnavailableError(
                "OPENAI_API_KEY is missing. Set it in the environment or pass api_key explicitly."
            )

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        output_schema: dict[str, Any],
        timeout_seconds: int,
    ) -> dict[str, Any]:
        request = self._build_request(
            system_prompt=system_prompt,
            user_payload=user_payload,
            output_schema=output_schema,
        )
        response_json, meta = self._send_request_with_retry(
            request=request,
            timeout_seconds=timeout_seconds,
        )
        self.last_response_meta = meta
        return self._parse_response(response_json)

    def _build_request(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        output_schema: dict[str, Any],
    ) -> dict[str, Any]:
        schema_name = output_schema.get("name", "structured_output")
        return {
            "model": self.model,
            "input": [
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": system_prompt}],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": json.dumps(user_payload, ensure_ascii=False, indent=2),
                        }
                    ],
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "schema": output_schema.get("schema", output_schema),
                    "strict": output_schema.get("strict", True),
                }
            },
        }

    def _send_request_with_retry(
        self,
        *,
        request: dict[str, Any],
        timeout_seconds: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        last_error: Exception | None = None
        for _ in range(self.max_retries):
            try:
                return self._send_request(request=request, timeout_seconds=timeout_seconds)
            except (httpx.HTTPError, AIResponseError) as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    def _send_request(
        self,
        *,
        request: dict[str, Any],
        timeout_seconds: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        call_id = generate_call_id()
        with httpx.Client(timeout=timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/responses",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=request,
            )
            response.raise_for_status()
            return response.json(), {
                "call_id": call_id,
                "request_id": response.headers.get("x-request-id"),
                "status_code": response.status_code,
            }

    def _parse_response(self, response: dict[str, Any]) -> dict[str, Any]:
        if "output_text" in response and response["output_text"]:
            try:
                return json.loads(response["output_text"])
            except json.JSONDecodeError as exc:
                raise AIResponseError("Failed to parse response.output_text as JSON.") from exc

        output_items = response.get("output", [])
        text_fragments: list[str] = []
        for item in output_items:
            for content in item.get("content", []):
                text_value = content.get("text")
                if text_value:
                    text_fragments.append(text_value)

        if not text_fragments:
            raise AIResponseError("No structured text payload found in Responses API output.")

        try:
            return json.loads("\n".join(text_fragments))
        except json.JSONDecodeError as exc:
            raise AIResponseError("Failed to parse structured output fragments as JSON.") from exc
