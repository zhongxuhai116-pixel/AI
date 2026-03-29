from __future__ import annotations

from pathlib import Path

from infra.utils.io import write_text


class MarkdownWriter:
    def write(self, path: str | Path, content: str) -> None:
        write_text(path, content)

