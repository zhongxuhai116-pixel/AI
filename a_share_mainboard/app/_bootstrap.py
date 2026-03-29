from __future__ import annotations

import sys
from pathlib import Path


def bootstrap_paths() -> Path:
    project_root = _resolve_project_root()
    src_root = project_root / "src"

    for path in (project_root, src_root):
        if not path.exists():
            continue
        path_str = str(path)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)

    from infra.config.env_loader import load_project_env

    load_project_env(project_root)

    return project_root


def _resolve_project_root() -> Path:
    if getattr(sys, "frozen", False):
        executable_root = Path(sys.executable).resolve().parent
        if (executable_root / "config").exists():
            return executable_root

        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            bundle_root = Path(meipass)
            if (bundle_root / "config").exists():
                return bundle_root
        return executable_root

    return Path(__file__).resolve().parents[1]
