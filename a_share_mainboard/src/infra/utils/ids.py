from __future__ import annotations

import uuid
from datetime import datetime, timezone


def generate_run_id(prefix: str = "run") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{prefix}_{stamp}_{uuid.uuid4().hex[:8]}"


def generate_call_id(prefix: str = "call") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"

