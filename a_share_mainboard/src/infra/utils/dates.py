from __future__ import annotations

from datetime import date, datetime, timedelta


def today_str() -> str:
    return date.today().isoformat()


def add_days(date_str: str, days: int) -> str:
    base = datetime.strptime(date_str, "%Y-%m-%d").date()
    return (base + timedelta(days=days)).isoformat()

