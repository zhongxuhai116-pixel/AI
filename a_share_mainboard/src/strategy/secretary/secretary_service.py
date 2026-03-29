from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from data.storage.repositories import ResearchRepository
from strategy.secretary.report_templates import build_json_template, build_markdown_template


@dataclass(slots=True)
class SecretaryService:
    repo: ResearchRepository
    markdown_writer: object

    def run(self, trade_date: str, context: dict, report_path: str | Path | None = None) -> dict:
        markdown = build_markdown_template(context)
        payload = build_json_template(context)
        report_df = pd.DataFrame(
            [
                {
                    "trade_date": trade_date,
                    "horizon": 0,
                    "report_markdown": markdown,
                    "report_json": json.dumps(payload, ensure_ascii=False, default=str),
                }
            ]
        )
        self.repo.save_daily_report(report_df)
        if report_path is not None:
            self.markdown_writer.write(report_path, markdown)
        return {"trade_date": trade_date, "report_markdown": markdown, "report_json": payload}
