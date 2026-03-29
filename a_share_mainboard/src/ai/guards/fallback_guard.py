from __future__ import annotations


def fallback_report(message: str) -> dict:
    return {"status": "FALLBACK", "message": message}

