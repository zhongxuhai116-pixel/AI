from __future__ import annotations

from pathlib import Path

import pandas as pd

from infra.utils.io import ensure_dir


class ParquetStore:
    def __init__(self, root: str | Path) -> None:
        self.root = ensure_dir(root)

    def write(self, relative_path: str, df: pd.DataFrame) -> Path:
        output_path = self.root / relative_path
        ensure_dir(output_path.parent)
        df.to_parquet(output_path, index=False)
        return output_path

