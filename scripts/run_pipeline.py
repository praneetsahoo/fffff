"""Run the pipeline on a CSV from the command line and print the data-quality report.

    python scripts/run_pipeline.py sample_data/operations_orders.csv
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.errors import InvalidFileError  # noqa: E402
from app.pipeline import run_pipeline  # noqa: E402
from app.profile import load_profile  # noqa: E402


def main(path: str) -> int:
    file = Path(path)
    profile = load_profile("operations_orders")
    try:
        r = run_pipeline(file.read_bytes(), file.name, profile)
    except InvalidFileError as exc:
        print(f"FILE REJECTED [{exc.code}]: {exc.message}")
        return 1

    print(f"\nData-quality report: {file.name}")
    print(f"  Total records        {r.total_rows:>8,}")
    print(f"  Valid records        {r.valid_rows:>8,}")
    print(f"  Invalid records      {r.invalid_rows:>8,}")
    print(f"  Duplicates           {r.duplicate_rows:>8,}")
    print(f"  Processing success   {r.success_rate:>7.2f}%")
    print(f"  Source quality score {r.quality_score:>7.2f}%  (rows perfect on arrival)")
    print("\nIssues by column:")
    for row in r.issues_as_rows():
        print(f"  {row['column']:<15} {row['issue_type']:<14} {row['count']:>6,}")
    print("\nFirst rejected rows:")
    for rej in r.rejected[:8]:
        print(f"  line {rej['row_number']:>5} [{rej['kind']}] {'; '.join(rej['reasons'])}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
