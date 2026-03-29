from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for path in (PROJECT_ROOT, PROJECT_ROOT / "src"):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from app.launcher import run_validate_cli


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run A-share validation workflow")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--horizon", type=int, default=5)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = run_validate_cli(
        start_date=args.start_date,
        end_date=args.end_date,
        horizon=args.horizon,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
