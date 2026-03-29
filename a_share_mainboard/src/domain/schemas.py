from __future__ import annotations

from pydantic import BaseModel, Field


class MarketSummaryOutput(BaseModel):
    market_style_label: str
    market_summary: str
    market_risk_notes: list[str] = Field(default_factory=list)


class StockExplanationOutput(BaseModel):
    technical_summary: str
    pattern_tags: list[str] = Field(default_factory=list)
    execution_notes: list[str] = Field(default_factory=list)
    confidence_note: str = "medium"


class RiskReviewOutput(BaseModel):
    risk_summary: str
    risk_tags: list[str] = Field(default_factory=list)
    watch_points: list[str] = Field(default_factory=list)


class DailyReportOutput(BaseModel):
    title: str
    summary: str
    sections: list[str] = Field(default_factory=list)


def build_json_schema(model_type: type[BaseModel], schema_name: str) -> dict:
    return {
        "name": schema_name,
        "schema": model_type.model_json_schema(),
        "strict": True,
    }

