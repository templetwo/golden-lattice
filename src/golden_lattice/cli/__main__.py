"""golden-lattice: estimate, run, and show co-authored answers (M1).

  golden-lattice estimate "prompt or path"          worst case and expected cost; no calls
  golden-lattice run "prompt or path" --budget 15 --yes [--out answer.md]
  golden-lattice show <session.json>                print a saved answer

`run` prints the estimate first and refuses without --yes. It needs ANTHROPIC_API_KEY
in the environment, or --key-file. Exit codes: 0 completed, 1 failed (salvaged to
failed.json), 2 refused or not confirmed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from golden_lattice.evaluation.cost import SpendGateError
from golden_lattice.evaluation.daily import DEFAULT_MONTHLY_CAP_USD, DailyRefused, run_daily
from golden_lattice.evaluation.estimate import estimate as estimate_run
from golden_lattice.orchestrator.config import LatticeConfig, TokenAllowance
from golden_lattice.orchestrator.orchestrator import DEFAULT_INVITED_MODELS


def _prompt_text(arg: str) -> str:
    path = Path(arg)
    return path.read_text(encoding="utf-8") if path.is_file() else arg


def _api_key(key_file: Optional[str]) -> Optional[str]:
    if key_file:
        return Path(key_file).expanduser().read_text(encoding="utf-8").strip()
    return os.environ.get("ANTHROPIC_API_KEY")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="golden-lattice")
    sub = parser.add_subparsers(dest="command", required=True)

    est = sub.add_parser("estimate", help="worst case and expected cost; makes no calls")
    est.add_argument("prompt")
    est.add_argument("--runs-root", default="sessions/eval")

    run = sub.add_parser("run", help="run a co-authored session (metered)")
    run.add_argument("prompt")
    run.add_argument("--budget", type=float, required=True, help="USD cap for this run")
    run.add_argument("--yes", action="store_true", help="confirm after reading the estimate")
    run.add_argument("--out", help="also write the answer to this file")
    run.add_argument("--sessions-dir", default="sessions/daily")
    run.add_argument("--monthly-cap", type=float, default=DEFAULT_MONTHLY_CAP_USD)
    run.add_argument("--runs-root", default="sessions/eval")
    run.add_argument("--key-file", help="read the API key from this file")

    show = sub.add_parser("show", help="print the answer of a saved session")
    show.add_argument("session_json")
    return parser


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    client_factory: Optional[Callable[..., Any]] = None,
) -> int:
    args = build_parser().parse_args(argv)
    peers = DEFAULT_INVITED_MODELS
    config = LatticeConfig()
    allowance = TokenAllowance()

    if args.command == "show":
        session = json.loads(Path(args.session_json).read_text(encoding="utf-8"))
        print(session["phase_4"]["output"])
        return 0

    prompt = _prompt_text(args.prompt)
    estimate = estimate_run(Path(args.runs_root), peers=peers, allowance=allowance, config=config)

    if args.command == "estimate":
        print(json.dumps(estimate, indent=2))
        return 0

    # run
    if not args.yes:
        print(json.dumps(estimate, indent=2))
        print(f"not started: re-run with --yes to spend up to ${args.budget:.2f} on this run")
        return 2
    key = _api_key(args.key_file)
    if key is None and client_factory is None:
        print("not started: no API key; set ANTHROPIC_API_KEY or pass --key-file")
        return 2
    try:
        result = run_daily(
            prompt,
            budget_usd=args.budget,
            out_dir=Path(args.sessions_dir),
            api_key=key,
            monthly_cap_usd=args.monthly_cap,
            peers=peers,
            config=config,
            allowance=allowance,
            client_factory=client_factory,
        )
    except (DailyRefused, SpendGateError) as exc:
        print(f"refused: {exc}")
        return 2

    print(json.dumps({k: v for k, v in result.items() if k != "answer_path"}, indent=2))
    if result["status"] == "completed":
        if args.out and result["answer_path"]:
            Path(args.out).write_text(Path(result["answer_path"]).read_text(encoding="utf-8"), encoding="utf-8")
        return 0
    print(f"failed: {result['error']['class']}: partial artifacts in {result['run_dir']}/failed.json")
    return 1


if __name__ == "__main__":
    sys.exit(main())
