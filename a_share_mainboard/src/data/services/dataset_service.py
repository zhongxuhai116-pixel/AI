from __future__ import annotations

from dataclasses import dataclass

from data.storage.repositories import ResearchRepository


@dataclass(slots=True)
class DatasetService:
    repo: ResearchRepository

