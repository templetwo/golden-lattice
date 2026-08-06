#!/usr/bin/env python3
"""Golden Lattice longitudinal experiment runner (Phase 2 Task 8).

Compares declared systems under test on the Task 7 TOML corpus:

  1. strongest_single_peer
  2. simple_parallel_responses
  3. conventional_judge_summarizer  (optional, non-canonical)
  4. golden_lattice

Modes:
  --mode dry_run   Plan only: emit statuses + prompt bundles; never call models
                   and never fabricate responses. Default.
  --mode live      Execute when GOLDEN_LATTICE_EXPERIMENT_LIVE=1 and a provider
                   API key is present; otherwise record status=unavailable with
                   reason (honest skip — not a fake completion).

Examples:
  python experiments/run_experiment.py
  python experiments/run_experiment.py --mode dry_run --out experiments/baselines/runs
  python experiments/run_experiment.py --sut golden_lattice --task gl.longitudinal.design_critique.v1

Does not modify canonical Phase 4 synthesis. Reuses run_lattice_session for the
golden_lattice SUT only.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow `python experiments/run_experiment.py` from repo root without install.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from experiments.baselines import REQUIRED_SUT_IDS  # noqa: E402
from experiments.baselines.protocol import GroundingMode, RunMode  # noqa: E402
from experiments.runner_lib import (  # noqa: E402
    DEFAULT_TASKS_DIR,
    run_batch,
    write_batch_outputs,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run or plan longitudinal baseline comparisons against the Task 7 corpus. "
            "Default is dry-run (no network, no fabricated outputs)."
        )
    )
    parser.add_argument(
        "--mode",
        choices=[m.value for m in RunMode],
        default=RunMode.DRY_RUN.value,
        help="dry_run (default) or live",
    )
    parser.add_argument(
        "--grounding",
        choices=[m.value for m in GroundingMode],
        default=GroundingMode.NONE.value,
        dest="grounding_mode",
        help="Phase 0 grounding: none (default) or tavily",
    )
    parser.add_argument(
        "--tasks-dir",
        type=Path,
        default=DEFAULT_TASKS_DIR,
        help="Directory of task TOML files",
    )
    parser.add_argument(
        "--task",
        action="append",
        dest="tasks",
        default=None,
        help="Task id to include (repeatable). Default: all tasks in --tasks-dir",
    )
    parser.add_argument(
        "--sut",
        action="append",
        dest="suts",
        default=None,
        choices=list(REQUIRED_SUT_IDS),
        help="SUT id to include (repeatable). Default: all declared SUTs",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=_REPO_ROOT / "experiments" / "baselines" / "runs",
        help="Output directory for JSON + summary (created if missing)",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id (default: timestamp-based)",
    )
    parser.add_argument(
        "--print-json",
        action="store_true",
        help="Also print the JSON payload path and a short status table to stdout",
    )
    args = parser.parse_args(argv)

    payload = run_batch(
        tasks_dir=args.tasks_dir,
        sut_ids=args.suts,
        task_ids=args.tasks,
        mode=RunMode(args.mode),
        grounding_mode=GroundingMode(args.grounding_mode),
        output_dir=None,
        run_id=args.run_id,
    )
    paths = write_batch_outputs(payload, args.out)

    manifest = payload["manifest"]
    print(f"run_id:     {manifest['run_id']}")
    print(f"mode:       {manifest['mode']}")
    print(f"grounding:  {manifest['grounding_mode']}")
    print(f"sessions:   {manifest['session_count']}")
    print(f"json:       {paths['json']}")
    print(f"summary:    {paths['summary']}")

    # Compact status table
    print()
    print(f"{'sut_id':<32} {'task_id':<48} {'status'}")
    print("-" * 100)
    for session in payload["sessions"]:
        print(
            f"{session['sut_id']:<32} {session['task_id']:<48} {session['status']}"
        )
        if session.get("unavailable_reason"):
            print(f"  reason: {session['unavailable_reason']}")

    if args.print_json:
        print()
        print(paths["json"].read_text(encoding="utf-8"))

    # Exit non-zero only on hard failures (errors/aborts), not on planned/unavailable.
    hard = {
        s["status"]
        for s in payload["sessions"]
        if s["status"] in {"error", "aborted"}
    }
    return 1 if hard else 0


if __name__ == "__main__":
    raise SystemExit(main())
