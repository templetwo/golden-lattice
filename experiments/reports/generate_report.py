#!/usr/bin/env python3
"""CLI for Phase 2 Task 9 case reports.

Reads Task 8 machine-readable run JSON and writes a human-readable case
report with five fixed sections. Pure formatting — no model calls.

Examples:
  python experiments/reports/generate_report.py \\
      --run experiments/baselines/runs/task8_dryrun_all.json

  python experiments/reports/generate_report.py \\
      --run experiments/baselines/runs/task8_dryrun_all.json \\
      --task gl.longitudinal.design_critique.v1 \\
      --out /tmp/design_critique_report.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from experiments.reports.generator import (  # noqa: E402
    generate_case_report,
    load_run_json,
    write_case_report,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a human-readable case report from Task 8 run JSON. "
            "Does not call models or modify canonical synthesis."
        )
    )
    parser.add_argument(
        "--run",
        type=Path,
        required=True,
        help="Path to Task 8 batch JSON (manifest + sessions)",
    )
    parser.add_argument(
        "--task",
        default=None,
        help="Optional task_id filter (one task report)",
    )
    parser.add_argument(
        "--session",
        default=None,
        dest="session_id",
        help="Optional session_id focus (baseline table still includes sibling SUTs)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output markdown path (default: stdout only)",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Force printing the report to stdout even when --out is set",
    )
    args = parser.parse_args(argv)

    payload = load_run_json(args.run)
    report = generate_case_report(
        payload, task_id=args.task, session_id=args.session_id
    )

    if args.out is not None:
        path = write_case_report(
            payload,
            args.out,
            task_id=args.task,
            session_id=args.session_id,
        )
        print(f"wrote: {path}", file=sys.stderr)

    if args.out is None or args.stdout:
        sys.stdout.write(report)
        if not report.endswith("\n"):
            sys.stdout.write("\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
