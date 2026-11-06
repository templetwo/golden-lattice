"""Pre-run estimates (M1). No client is built and no call is made.

Two numbers, labelled so they cannot be confused:
- worst case: the cap formula at the highest known rate (a ceiling, not a forecast);
- expected: metered cost of completed sessions with the same peers, allowance and
  timeouts, from eval runs (runs_root/*/run.json) and from daily runs
  (daily_dir/*/sidecar.json, the history a daily user accumulates). Costs recorded
  before cost.PRICE_DATE metered Haiku 5.5 at the fallback rate, so they run high.
"""

from __future__ import annotations

import json
import math
import statistics
from pathlib import Path
from typing import Any, Optional, Sequence

from golden_lattice.evaluation.cost import worst_case_session_usd
from golden_lattice.memory_graph.base import ModelId
from golden_lattice.orchestrator.config import LatticeConfig, TokenAllowance

MIN_HISTORY = 3


def timeouts_of(config: LatticeConfig) -> dict[str, float]:
    return {
        "phase_1": config.timeout_phase_1_seconds,
        "self_reflection": config.timeout_self_reflection_seconds,
        "phase_2": config.timeout_phase_2_seconds,
        "phase_3": config.timeout_phase_3_seconds,
    }


def expected_from_history(
    runs_root: Path,
    *,
    peers: Sequence[ModelId],
    allowance: TokenAllowance,
    config: LatticeConfig,
    daily_dir: Optional[Path] = None,
) -> dict[str, Any]:
    want_peers = [m.value for m in peers]
    want_allowance = allowance.model_dump()
    want_timeouts = timeouts_of(config)
    costs: list[float] = []
    matched_runs = 0
    for run_json in sorted(Path(runs_root).glob("*/run.json")):
        run = json.loads(run_json.read_text(encoding="utf-8"))
        if (run.get("peers") != want_peers
                or run.get("allowance") != want_allowance
                or run.get("timeouts_seconds") != want_timeouts):
            continue
        matched_runs += 1
        costs += [
            float(s["session_cost_usd"])
            for s in run.get("sessions", [])
            if s.get("status") == "completed" and s.get("session_cost_usd") is not None
        ]
    if daily_dir is not None:
        for sidecar_json in sorted(Path(daily_dir).glob("*/sidecar.json")):
            try:
                sidecar = json.loads(sidecar_json.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue  # a half-written sidecar is not history
            if (sidecar.get("status") != "completed"
                    or sidecar.get("cost_usd") is None
                    or sidecar.get("peers") != want_peers
                    or sidecar.get("allowance") != want_allowance
                    or sidecar.get("timeouts_seconds") != want_timeouts):
                continue
            matched_runs += 1
            costs.append(float(sidecar["cost_usd"]))
    if len(costs) < MIN_HISTORY:
        return {"n": len(costs), "matched_runs": matched_runs, "p50": None, "p95": None,
                "note": f"insufficient matching history (need {MIN_HISTORY} completed sessions)"}
    ordered = sorted(costs)
    p95 = ordered[min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)]
    return {"n": len(costs), "matched_runs": matched_runs,
            "p50": round(statistics.median(ordered), 4), "p95": round(p95, 4), "note": None}


def estimate(
    runs_root: Path,
    *,
    peers: Sequence[ModelId],
    allowance: TokenAllowance,
    config: LatticeConfig,
    daily_dir: Optional[Path] = None,
) -> dict[str, Any]:
    return {
        "peers": [m.value for m in peers],
        "worst_case_usd": round(worst_case_session_usd(len(peers), allowance), 4),
        "expected": expected_from_history(runs_root, peers=peers, allowance=allowance, config=config,
                                          daily_dir=daily_dir),
        "labels": {
            "worst_case_usd": "cap formula at the highest known rate; a ceiling, not a forecast",
            "expected": ("metered cost of matching completed sessions (eval runs and daily runs); "
                         "costs recorded before the Haiku 5.5 price was added ran high"),
        },
    }
