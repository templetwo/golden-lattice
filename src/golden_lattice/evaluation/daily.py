"""Daily runs (M1, M2): gated, metered, recorded, and salvageable on failure.

Every run has its own directory under out_dir/<session_id>/:
- completed: session.json, answer.md, sidecar.json
- failed or interrupted: failed.json (completed artifacts, the error, spend so far),
  sidecar.json

A monthly ledger (out_dir/ledger.jsonl) records every run, including failed ones,
so spend that produced no answer is still counted. Each run writes two lines:
- at gate time, a "started" line carrying its budget. It is written under an
  exclusive flock on the ledger, in the same critical section as the cap check, so
  two concurrent runs cannot both pass a cap that only one of them fits;
- at the end, a final line (completed, failed or interrupted) with the real cost.
  The same run_id supersedes the started line when the ledger is read.

A run whose process dies (kill -9, power loss) leaves only its started line, and
its whole budget stays reserved for that month: the cap may overstate spend, never
understate it. A run is refused before any call if its worst case exceeds its
budget, or if completed costs + reserved budgets + this budget would pass the cap.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from golden_lattice.evaluation.cost import SpendLedger, check_run_gate, worst_case_session_usd
from golden_lattice.evaluation.metering import MeteredAnthropicClient
from golden_lattice.evaluation.recording import RecordingClient
from golden_lattice.evaluation.estimate import timeouts_of
from golden_lattice.memory_graph.base import ModelId
from golden_lattice.orchestrator.config import LatticeConfig, TokenAllowance
from golden_lattice.orchestrator.orchestrator import (
    DEFAULT_INVITED_MODELS,
    ProgressCallback,
    run_lattice_session,
)

DEFAULT_MONTHLY_CAP_USD = 100.0
APPROVER = "owner (CLI --yes)"
LEDGER_NAME = "ledger.jsonl"


class DailyRefused(ValueError):
    """A run was refused before any call was made."""


def month_to_date(lines: Sequence[str], month: str) -> tuple[float, float]:
    """(completed spend, reserved budgets) for a month of ledger lines.

    Lines are keyed by run_id (or session_id, for lines written before run_id
    existed); the last line of a key wins. A key whose last line is "started" is a
    run that has not finished (or died): its budget is reserved. Any other last line
    counts its cost. Lines with no id at all are counted on their own.
    """
    latest: dict[Any, dict[str, Any]] = {}
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        entry = json.loads(line)
        if entry.get("month") != month:
            continue
        key = entry.get("run_id") or entry.get("session_id") or ("anonymous", index)
        latest[key] = entry
    spent = reserved = 0.0
    for entry in latest.values():
        if entry.get("status") == "started":
            reserved += float(entry.get("budget_usd") or 0.0)
        else:
            spent += float(entry.get("cost_usd") or 0.0)
    return spent, reserved


def _write_line(fh: Any, entry: dict[str, Any]) -> None:
    fh.write(json.dumps(entry) + "\n")
    fh.flush()
    os.fsync(fh.fileno())


def _reserve(ledger_path: Path, *, entry: dict[str, Any], month: str, budget: float, cap: float) -> float:
    """Check the cap and write the started line under one exclusive lock.

    Returns completed spend for the month. Raises DailyRefused (and writes nothing)
    when the run does not fit.
    """
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            fh.seek(0)
            spent, reserved = month_to_date(fh.read().splitlines(), month)
            if spent + reserved + budget > cap:
                held = f" plus ${reserved:.2f} reserved by unfinished runs" if reserved else ""
                raise DailyRefused(
                    f"monthly cap: ${spent:.2f} spent{held} in {month}; a ${budget:.2f} run "
                    f"would pass the ${cap:.2f} cap"
                )
            _write_line(fh, entry)
            return spent
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _append_ledger(ledger_path: Path, entry: dict[str, Any]) -> None:
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            _write_line(fh, entry)
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _error_record(exc: BaseException) -> dict[str, Any]:
    return {
        "class": type(exc).__name__,
        "message": str(exc)[:500],
        "model": getattr(getattr(exc, "model", None), "value", None),
        "phase": getattr(exc, "phase", None),
        # Refusal detail (OrchestratorRefusalError); None for any other failure.
        "category": getattr(exc, "category", None),
        "explanation": getattr(exc, "explanation", None),
    }


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
    progress_callback: Optional[ProgressCallback] = None,
) -> dict[str, Any]:
    cfg = config or LatticeConfig()
    cap = allowance or TokenAllowance()
    budget = check_run_gate(budget_usd=budget_usd, approved_by=approved_by)

    worst = worst_case_session_usd(len(peers), cap)
    if worst > budget:
        raise DailyRefused(f"worst case ${worst:.2f} exceeds the budget ${budget:.2f}")

    started_at = now or datetime.now(timezone.utc)
    started_clock = time.monotonic()
    month = started_at.strftime("%Y-%m")
    out_dir = Path(out_dir).expanduser().resolve()
    ledger_path = out_dir / LEDGER_NAME
    session_id = f"daily-{started_at.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    run_dir = out_dir / session_id

    spent = _reserve(ledger_path, month=month, budget=budget, cap=monthly_cap_usd, entry={
        "run_id": session_id,
        "status": "started",
        "budget_usd": budget,
        "started_at": started_at.isoformat(timespec="seconds"),
        "month": month,
        "run_dir": str(run_dir),
    })

    # From here on the started line is in the ledger: every exit path must close it.
    run_ledger = SpendLedger(budget_usd=budget)
    usage: list[dict[str, Any]] = []

    def sink(entry: dict[str, Any]) -> None:
        usage.append(entry)
        run_ledger.record(entry)  # raises BudgetExceeded mid-run; the run is then salvaged

    status = "completed"
    error: Optional[dict[str, Any]] = None
    answer_path: Optional[Path] = None
    inner: Any = None
    recorder: Optional[RecordingClient] = None
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        if client_factory is None:
            inner = MeteredAnthropicClient(api_key=api_key, sink=sink, allowance=cap)
        else:
            inner = client_factory(api_key=api_key, sink=sink, allowance=cap)
        recorder = RecordingClient(inner)
        session = run_lattice_session(
            prompt,
            config=cfg,
            client=recorder,
            invited_models=tuple(peers),
            session_id=session_id,
            progress_callback=progress_callback,
        )
        (run_dir / "session.json").write_text(session.model_dump_json(indent=2), encoding="utf-8")
        answer_path = run_dir / "answer.md"
        answer_path.write_text(session.phase_4.output, encoding="utf-8")
    except BaseException as exc:  # any failure is recorded, never dropped
        status = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        error = _error_record(exc)
        answer_path = None
        if not isinstance(exc, Exception):
            exc.add_note(f"golden-lattice: partial artifacts in {run_dir}")
            raise  # KeyboardInterrupt, SystemExit: recorded in finally, then propagated
    finally:
        duration_s = round(time.monotonic() - started_clock, 3)
        finished_at = started_at + timedelta(seconds=duration_s)
        cost = round(sum(float(u["cost_usd"]) for u in usage), 6)
        completed = list(recorder.completed) if recorder is not None else []
        retry_log = list(getattr(inner, "retry_log", []) or [])
        run_dir.mkdir(parents=True, exist_ok=True)
        if status != "completed":
            (run_dir / "failed.json").write_text(json.dumps({
                "session_id": session_id,
                "status": status,
                "error": error,
                "completed_artifacts": completed,
                "spend_usd": cost,
                "retry_log": retry_log,
            }, indent=2), encoding="utf-8")
        sidecar = {
            "session_id": session_id,
            "status": status,
            "error": error,
            "run_dir": str(run_dir),
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "peers": [m.value for m in peers],
            "allowance": cap.model_dump(),
            "timeouts_seconds": timeouts_of(cfg),
            "budget_usd": budget,
            "worst_case_usd": round(worst, 4),
            "approved_by": approved_by,
            "cost_usd": cost,
            "calls": usage,
            "completed_artifacts": len(completed),
            "retry_log": retry_log,
            "started_at": started_at.isoformat(timespec="seconds"),
            "finished_at": finished_at.isoformat(timespec="milliseconds"),
            "duration_s": duration_s,
        }
        (run_dir / "sidecar.json").write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
        _append_ledger(ledger_path, {
            "run_id": session_id,
            "session_id": session_id,
            "month": month,
            "status": status,
            "cost_usd": cost,
            "budget_usd": budget,
            "started_at": started_at.isoformat(timespec="seconds"),
            "finished_at": finished_at.isoformat(timespec="milliseconds"),
            "duration_s": duration_s,
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
        "duration_s": duration_s,
        "error": error,
    }
