from __future__ import annotations

import base64
import hashlib
import hmac
import time

import httpx

from infra.exceptions import ProviderUnavailableError


class FeishuWebhookClient:
    def __init__(
        self,
        *,
        webhook_url: str,
        timeout_seconds: int,
        signing_secret: str | None = None,
    ) -> None:
        if not webhook_url:
            raise ProviderUnavailableError("Feishu webhook URL is missing.")
        self.webhook_url = webhook_url
        self.timeout_seconds = timeout_seconds
        self.signing_secret = signing_secret

    def send_text(self, text: str) -> dict:
        payload: dict[str, object] = {
            "msg_type": "text",
            "content": {"text": text},
        }
        if self.signing_secret:
            timestamp = str(int(time.time()))
            sign = self._build_signature(timestamp=timestamp)
            payload["timestamp"] = timestamp
            payload["sign"] = sign

        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(self.webhook_url, json=payload)
            response.raise_for_status()
            return response.json()

    def _build_signature(self, *, timestamp: str) -> str:
        string_to_sign = f"{timestamp}\n{self.signing_secret}"
        digest = hmac.new(
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        return base64.b64encode(digest).decode("utf-8")
