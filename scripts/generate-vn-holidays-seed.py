"""(Re)generate `dbt/seeds/vn_holidays.csv` from the `holidays` library.

One-shot dev-time script — NOT a runtime dependency. Run when:
- the holidays package gets new entries for upcoming years
- the desired year range expands

Usage:
    python scripts/generate-vn-holidays-seed.py --start 2020 --end 2035 \\
        --out dbt/seeds/vn_holidays.csv
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import holidays


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate VN public holidays CSV seed.")
    parser.add_argument("--start", type=int, default=2020)
    parser.add_argument("--end", type=int, default=2035)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("dbt/seeds/vn_holidays.csv"),
    )
    args = parser.parse_args()

    vn = holidays.Vietnam(years=range(args.start, args.end + 1))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "holiday_name", "is_public_holiday"])
        for d, name in sorted(vn.items()):
            writer.writerow([d.isoformat(), name, "true"])
    print(f"wrote {len(vn)} holidays → {args.out}")


if __name__ == "__main__":
    main()
