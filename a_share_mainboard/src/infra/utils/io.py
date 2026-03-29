from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def ensure_dir(path: str | Path) -> Path:
    path_obj = Path(path)
    path_obj.mkdir(parents=True, exist_ok=True)
    return path_obj


def write_text(path: str | Path, content: str) -> None:
    path_obj = Path(path)
    ensure_dir(path_obj.parent)
    path_obj.write_text(content, encoding="utf-8")


def write_json(path: str | Path, payload: Any) -> None:
    path_obj = Path(path)
    ensure_dir(path_obj.parent)
    path_obj.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def append_jsonl(path: str | Path, payload: Any) -> None:
    path_obj = Path(path)
    ensure_dir(path_obj.parent)
    with path_obj.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

