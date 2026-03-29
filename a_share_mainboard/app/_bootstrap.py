from __future__ import annotations

import sys
from pathlib import Path


def bootstrap_paths() -> Path:
    project_root = Path(__file__).resolve().parents[1]
    src_root = project_root / "src"

    for path in (project_root, src_root):
        path_str = str(path)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)

    return project_root

