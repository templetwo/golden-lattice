"""Metered pilot run over a benchmark split (eval harness steps 7, 9, 11).

Every session is gated: before it starts, the worst-case estimate must fit
the remaining budget. Measured spend is recorded per call through the
metered client. A mid-session overrun aborts the run and is reported as
spend, never dropped.

Output is labelled by the caller. A pilot over placeholder tasks is a
measurement of the pipeline, not evidence about the architecture.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from golden_lattice.evaluation.audit import audit_session, parity_readings
from golden_lattice.evaluation.benchmark import Task
from golden_lattice.evaluation.cost import (
    CEILING_USD,
    PRICE_DATE,
    BudgetExceeded,
    SpendLedger,
    check_run_gate,
    worst_case_session_usd,
)
from golden_lattice.evaluation.metering import MeteredAnthropicClient
from golden_lattice.memory_graph.base import ModelId
from golden_lattice.orchestrator.config import LatticeConfig, TokenAllowance
from golden_lattice.orchestrator.orchestrator import run_lattice_session


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _raw_of(exc: BaseException) -> Any:
    """Find the raw tool_input attached at the parse site, following the cause chain."""
    seen = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        raw = getattr(current, "raw_tool_input", None)
        if raw is not None:
            return raw
        current = current.__cause__ or current.__context__
    return None


def run_pilot(
    *,
    api_key: str,
    tasks: Sequence[Task],
    peers: tuple[ModelId, ...],
    budget_usd: Optional[float],
    approved_by: Optional[str],
    out_dir: Path,
    label: str,
    ceiling_usd: float = CEILING_USD,
    client_factory: Optional[Callable[..., Any]] = None,
    config: Optional[LatticeConfig] = None,
    allowance: Optional[TokenAllowance] = None,
) -> dict:
    """Run one session per task under a gated, metered budget."""
    cfg = config or LatticeConfig()
    cap = allowance or TokenAllowance()
    budget = check_run_gate(
        budget_usd=budget_usd, approved_by=approved_by, ceiling_usd=ceiling_usd
    )
    run_id = datetime.now(timezone.utc).strftime("pilot-%Y%m%dT%H%M%SZ")
    run_dir = out_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    ledger = SpendLedger(budget_usd=budget)
    estimate = worst_case_session_usd(len(peers), cap)
    summary: dict[str, Any] = {
        "run_id": run_id,
        "label": label,
        "started_at": _utc_now(),
        "approved_by": approved_by,
        "budget_usd": budget,
        "ceiling_usd": ceiling_usd,
        "price_date": PRICE_DATE,
        "peers": [m.value for m in peers],
        "per_session_estimate_usd": estimate,
        "allowance": cap.model_dump(),
        "timeouts_seconds": {
            "phase_1": cfg.timeout_phase_1_seconds,
            "self_reflection": cfg.timeout_self_reflection_seconds,
            "phase_2": cfg.timeout_phase_2_seconds,
            "phase_3": cfg.timeout_phase_3_seconds,
        },
        "sessions": [],
        "status": "running",
    }

    for task in tasks:
        if not ledger.can_start(estimate):
            summary["sessions"].append({"task_id": task.id, "status": "not_started_budget"})
            summary["status"] = "budget_stop"
            break

        session_id = f"{run_id}-{task.id}"
        calls: list[dict] = []

        def sink(entry: dict, _calls: list[dict] = calls) -> None:
            _calls.append(entry)
            ledger.record(entry)

        factory = client_factory or MeteredAnthropicClient
        client = factory(api_key=api_key, sink=sink, allowance=cap)
        record: dict[str, Any] = {
            "task_id": task.id,
            "session_id": session_id,
            "prompt_sha256": hashlib.sha256(task.prompt.encode("utf-8")).hexdigest(),
        }
        try:
            session = run_lattice_session(
                task.prompt,
                config=cfg,
                client=client,
                invited_models=peers,
                session_id=session_id,
            )
            (run_dir / f"{session_id}.session.json").write_text(
                session.model_dump_json(indent=2), encoding="utf-8"
            )
            session_dict = json.loads(session.model_dump_json())
            record["parity"] = parity_readings(session_dict.get("metrics") or {})
            record["audit"] = audit_session(session_dict)
            record["status"] = "completed"
        except BudgetExceeded as exc:
            record["status"] = "budget_exceeded_mid_session"
            record["error"] = str(exc)
            summary["status"] = "budget_stop"
        except Exception as exc:  # recorded, never silently dropped
            record["status"] = "failed"
            record["error"] = f"{type(exc).__name__}: {exc}"
            record["raw_tool_input"] = _raw_of(exc)

        record["calls"] = calls
        record["session_cost_usd"] = sum(c["cost_usd"] for c in calls)
        (run_dir / f"{session_id}.eval.json").write_text(
            json.dumps(record, indent=2), encoding="utf-8"
        )
        summary["sessions"].append(
            {k: record[k] for k in ("task_id", "session_id", "status", "session_cost_usd")}
        )
        if summary["status"] == "budget_stop":
            break

    summary["spent_usd"] = round(ledger.spent_usd, 6)
    summary["remaining_usd"] = round(ledger.remaining_usd, 6)
    summary["finished_at"] = _utc_now()
    if summary["status"] == "running":
        summary["status"] = "completed"
    (run_dir / "run.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
