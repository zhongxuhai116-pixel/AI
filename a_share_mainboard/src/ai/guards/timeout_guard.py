from __future__ import annotations


def resolve_timeout(requested_timeout: int | None, default_timeout: int) -> int:
    return requested_timeout or default_timeout

