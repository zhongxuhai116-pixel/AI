from __future__ import annotations

import json
from dataclasses import dataclass

import pandas as pd

from data.storage.repositories import LogRepository


@dataclass(slots=True)
class AIResultStore:
    repo: LogRepository

    def save_call_log(self, record: dict) -> int:
        return self.repo.save_ai_calls(pd.DataFrame([record]))

    def save_agent_output(self, trade_date: str, agent_name: str, payload: dict) -> dict:
        return {
            "trade_date": trade_date,
            "agent_name": agent_name,
            "payload_json": json.dumps(payload, ensure_ascii=False),
        }

