from __future__ import annotations

from pathlib import Path


def main() -> None:
    report_dir = Path(__file__).resolve().parents[1] / "data" / "reports"
    print({"status": "SUCCESS", "report_dir": str(report_dir)})


if __name__ == "__main__":
    main()

