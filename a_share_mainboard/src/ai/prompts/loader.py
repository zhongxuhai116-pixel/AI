from __future__ import annotations

from pathlib import Path


class PromptLoader:
    def __init__(self, prompt_root: str | Path) -> None:
        self.prompt_root = Path(prompt_root)

    def load(self, prompt_name: str) -> str:
        prompt_path = self.prompt_root / f"{prompt_name}.md"
        return prompt_path.read_text(encoding="utf-8").strip()
