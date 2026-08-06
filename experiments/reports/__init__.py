"""Case report package (Phase 2 Task 9).

Human-readable reports over Task 8 run JSON. Does not touch canonical synthesis.
"""

from experiments.reports.generator import (
    generate_case_report,
    load_run_json,
    write_case_report,
)

__all__ = [
    "generate_case_report",
    "load_run_json",
    "write_case_report",
]
