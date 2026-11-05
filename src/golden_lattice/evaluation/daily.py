"""Daily runs (M1, M2): gated, metered, recorded, and salvageable on failure.

Every run has its own directory under out_dir/<session_id>/:
- completed: session.json, answer.md, sidecar.json
- failed: failed.json (completed artifacts, the error, spend so far), sidecar.json

A monthly ledger (out_dir/ledger.jsonl) records every run, including failed ones,
so spend that produced no answer is still counted. A run is refused before any
call if its worst case exceeds its budget, or if the month would exceed the cap.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from golden_lattice.evaluation.cost import SpendLedger, check_run_gate, worst_case_session_usd
from golden_lattice.evaluation.metering import MeteredAnthropicClient
from golden_lattice.evaluation.recording import RecordingClient
from golden_lattice.evaluation.estimate import timeouts_of
from golden_lattice.memory_graph.base import ModelId
from golden_lattice.orchestrator.config import LatticeConfig, TokenAllowance
from golden_lattice.orchestrator.orchestrator import DEFAULT_INVITED_MODELS, run_lattice_session

DEFAULT_MONTHLY_CAP_USD = 100.0
APPROVER = "owner (CLI --yes)"


class DailyRefused(ValueError):
    """A run was refused before any call was made."""


def _month_to_date(ledger_path: Path, month: str) -> float:
    if not ledger_path.is_file():
        return 0.0
    total = 0.0
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entry = json.loads(line)
            if entry.get("month") == month:
                total += float(entry.get("cost_usd") or 0.0)
    return total


def _append_ledger(ledger_path: Path, entry: dict[str, Any]) -> None:
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def run_daily(
    prompt: str,
    *,
    budget_usd: Optional[float],
    out_dir: Path,
    api_key: Optional[str] = None,
    monthly_cap_usd: float = DEFAULT_MONTHLY_CAP_USD,
    peers: Sequence[ModelId] = DEFAULT_INVITED_MODELS,
    config: Optional[LatticeConfig] = None,
    allowance: Optional[TokenAllowance] = None,
    client_factory: Optional[Callable[..., Any]] = None,
    approved_by: str = APPROVER,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    cfg = config or LatticeConfig()
    cap = allowance or TokenAllowance()
    budget = check_run_gate(budget_usd=budget_usd, approved_by=approved_by)

    worst = worst_case_session_usd(len(peers), cap)
    if worst > budget:
        raise DailyRefused(f"worst case ${worst:.2f} exceeds the budget ${budget:.2f}")

    when = now or datetime.now(timezone.utc)
    month = when.strftime("%Y-%m")
    out_dir = Path(out_dir)
    ledger_path = out_dir / "ledger.jsonl"
    spent = _month_to_date(ledger_path, month)
    if spent + budget > monthly_cap_usd:
        raise DailyRefused(
            f"monthly cap: ${spent:.2f} spent in {month}; a ${budget:.2f} run would pass "
            f"the ${monthly_cap_usd:.2f} cap"
        )

    session_id = f"daily-{when.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    run_ledger = SpendLedger(budget_usd=budget)
    usage: list[dict[str, Any]] = []

    def sink(entry: dict[str, Any]) -> None:
        usage.append(entry)
        run_ledger.record(entry)  # raises BudgetExceeded mid-run; the run is then salvaged

    if client_factory is None:
        inner = MeteredAnthropicClient(api_key=api_key, sink=sink, allowance=cap)
    else:
        inner = client_factory(api_key=api_key, sink=sink, allowance=cap)
    recorder = RecordingClient(inner)

    run_dir = out_dir / session_id
    run_dir.mkdir(parents=True, exist_ok=True)
    status = "completed"
    error: Optional[dict[str, Any]] = None
    answer_path: Optional[Path] = None
    try:
        session = run_lattice_session(
            prompt,
            config=cfg,
            client=recorder,
            invited_models=tuple(peers),
            session_id=session_id,
        )
        (run_dir / "session.json").write_text(session.model_dump_json(indent=2), encoding="utf-8")
        answer_path = run_dir / "answer.md"
        answer_path.write_text(session.phase_4.output, encoding="utf-8")
    except Exception as exc:  # any failure is recorded, never dropped
        status = "failed"
        error = {
            "class": type(exc).__name__,
            "message": str(exc)[:500],
            "model": getattr(getattr(exc, "model", None), "value", None),
            "phase": getattr(exc, "phase", None),
            # Refusal detail (OrchestratorRefusalError); None for any other failure.
            "category": getattr(exc, "category", None),
            "explanation": getattr(exc, "explanation", None),
        }
        spend_so_far = sum(float(u["cost_usd"]) for u in usage)
        (run_dir / "failed.json").write_text(json.dumps({
            "session_id": session_id,
            "error": error,
            "completed_artifacts": recorder.completed,
            "spend_usd": round(spend_so_far, 6),
            "retry_log": list(getattr(inner, "retry_log", [])),
        }, indent=2), encoding="utf-8")

    cost = round(sum(float(u["cost_usd"]) for u in usage), 6)
    sidecar = {
        "session_id": session_id,
        "status": status,
        "error": error,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "peers": [m.value for m in peers],
        "allowance": cap.model_dump(),
        "timeouts_seconds": timeouts_of(cfg),
        "budget_usd": budget,
        "worst_case_usd": round(worst, 4),
        "approved_by": approved_by,
        "cost_usd": cost,
        "calls": usage,
        "completed_artifacts": len(recorder.completed),
        "retry_log": list(getattr(inner, "retry_log", [])),
    }
    (run_dir / "sidecar.json").write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    _append_ledger(ledger_path, {
        "session_id": session_id,
        "month": month,
        "status": status,
        "cost_usd": cost,
        "budget_usd": budget,
        "finished_at": when.isoformat(timespec="seconds"),
    })
    return {
        "session_id": session_id,
        "status": status,
        "cost_usd": cost,
        "budget_usd": budget,
        "month_to_date_usd": round(spent + cost, 6),
        "monthly_cap_usd": monthly_cap_usd,
        "run_dir": str(run_dir),
        "answer_path": str(answer_path) if answer_path else None,
        "error": error,
    }
