"""M1 + M2 daily runner: refusals before any call, salvage on failure, ledger, sidecar.

Uses FixtureClient (offline) as the inner client, injected through client_factory.
"""

from __future__ import annotations

import json
import socket
from datetime import datetime, timezone

import pytest

from golden_lattice.evaluation.daily import DailyRefused, run_daily
from golden_lattice.evaluation.fixtures import FixtureClient
from golden_lattice.evaluation.recording import RecordingClient
from golden_lattice.memory_graph.base import ModelId
from golden_lattice.orchestrator.errors import OrchestratorProviderError, OrchestratorRefusalError

PROMPT = "design a rate limiter for a public API"
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


def _fixture_factory(**kwargs):
    return FixtureClient(seed=0)


class _FailsInPhase3(FixtureClient):
    async def submit_phase_3_dialogue(self, **kwargs):
        raise OrchestratorProviderError(
            model=ModelId.HAIKU, phase="phase_3", underlying=RuntimeError("upstream 529"),
        )


class _RefusedInPhase3(FixtureClient):
    async def submit_phase_3_dialogue(self, **kwargs):
        raise OrchestratorRefusalError(
            model=ModelId.HAIKU, phase="phase_3", category="cyber", explanation="exploit development",
        )


def test_completed_run_writes_session_answer_sidecar_and_ledger(tmp_path, no_network):
    result = run_daily(PROMPT, budget_usd=15, out_dir=tmp_path, client_factory=_fixture_factory, now=NOW)
    assert result["status"] == "completed"
    run_dir = tmp_path / result["session_id"]
    assert (run_dir / "session.json").is_file()
    assert (run_dir / "answer.md").read_text(encoding="utf-8").strip()
    sidecar = json.loads((run_dir / "sidecar.json").read_text(encoding="utf-8"))
    assert sidecar["status"] == "completed"
    assert sidecar["completed_artifacts"] == 18  # three peers, eighteen calls
    ledger = [json.loads(l) for l in (tmp_path / "ledger.jsonl").read_text().splitlines()]
    assert ledger[-1]["status"] == "completed"
    assert ledger[-1]["month"] == "2026-11"


def test_run_refused_before_any_call_when_worst_case_exceeds_budget(tmp_path, no_network):
    called = []

    def factory(**kwargs):
        called.append(True)
        return FixtureClient(seed=0)

    with pytest.raises(DailyRefused, match="exceeds the budget"):
        run_daily(PROMPT, budget_usd=5, out_dir=tmp_path, client_factory=factory, now=NOW)
    assert called == []
    assert not (tmp_path / "ledger.jsonl").exists()


def test_monthly_cap_refuses_before_any_call(tmp_path, no_network):
    (tmp_path / "ledger.jsonl").write_text(
        json.dumps({"month": "2026-11", "cost_usd": 95.0, "status": "completed"}) + "\n",
        encoding="utf-8",
    )
    called = []

    def factory(**kwargs):
        called.append(True)
        return FixtureClient(seed=0)

    with pytest.raises(DailyRefused, match="monthly cap"):
        run_daily(PROMPT, budget_usd=15, out_dir=tmp_path, client_factory=factory, now=NOW)
    assert called == []


def test_failed_run_salvages_completed_artifacts_and_is_counted(tmp_path, no_network):
    result = run_daily(
        PROMPT, budget_usd=15, out_dir=tmp_path,
        client_factory=lambda **kw: _FailsInPhase3(seed=0), now=NOW,
    )
    assert result["status"] == "failed"
    assert result["error"]["class"] == "OrchestratorProviderError"
    run_dir = tmp_path / result["session_id"]
    assert not (run_dir / "session.json").exists()
    salvage = json.loads((run_dir / "failed.json").read_text(encoding="utf-8"))
    assert len(salvage["completed_artifacts"]) == 15  # everything before Phase 3 completed
    assert {a["method"] for a in salvage["completed_artifacts"]} >= {"submit_phase_1_response", "submit_cross_reading"}
    ledger = [json.loads(l) for l in (tmp_path / "ledger.jsonl").read_text().splitlines()]
    assert ledger[-1]["status"] == "failed"


def test_sidecar_carries_the_retry_log_of_the_client(tmp_path, no_network):
    inner = FixtureClient(seed=0)
    inner.retry_log = [{"model": "claude-opus-5", "phase": "phase_1", "attempt": 1, "status": 529, "error": "x"}]
    result = run_daily(PROMPT, budget_usd=15, out_dir=tmp_path, client_factory=lambda **kw: inner, now=NOW)
    sidecar = json.loads((tmp_path / result["session_id"] / "sidecar.json").read_text(encoding="utf-8"))
    assert sidecar["retry_log"] == inner.retry_log


def test_refusal_failure_record_carries_category_and_explanation(tmp_path, no_network):
    result = run_daily(
        PROMPT, budget_usd=15, out_dir=tmp_path,
        client_factory=lambda **kw: _RefusedInPhase3(seed=0), now=NOW,
    )
    assert result["status"] == "failed"
    error = result["error"]
    assert error["class"] == "OrchestratorRefusalError"
    assert error["model"] == ModelId.HAIKU.value
    assert error["phase"] == "phase_3"
    assert error["category"] == "cyber"
    assert error["explanation"] == "exploit development"
    assert "exploit development" in error["message"]
    run_dir = tmp_path / result["session_id"]
    salvage = json.loads((run_dir / "failed.json").read_text(encoding="utf-8"))
    assert salvage["error"]["category"] == "cyber"
    assert salvage["error"]["explanation"] == "exploit development"
    sidecar = json.loads((run_dir / "sidecar.json").read_text(encoding="utf-8"))
    assert sidecar["error"]["category"] == "cyber"


def test_non_refusal_failure_record_has_null_refusal_fields(tmp_path, no_network):
    result = run_daily(
        PROMPT, budget_usd=15, out_dir=tmp_path,
        client_factory=lambda **kw: _FailsInPhase3(seed=0), now=NOW,
    )
    assert result["error"]["category"] is None
    assert result["error"]["explanation"] is None


def test_recording_client_captures_every_call_of_a_three_peer_session(tmp_path, no_network):
    recorder = RecordingClient(FixtureClient(seed=0))
    result = run_daily(PROMPT, budget_usd=15, out_dir=tmp_path,
                       client_factory=lambda **kw: recorder, now=NOW)
    assert result["status"] == "completed"
    assert len(recorder.completed) == 18


# --- Ledger that survives a crash: started line at gate time, reservations, lock (item 3) ---

def _ledger(path):
    return [json.loads(l) for l in (path / "ledger.jsonl").read_text().splitlines() if l.strip()]


def test_started_line_is_written_before_any_call(tmp_path, no_network):
    seen = []

    class _Peek(FixtureClient):
        async def submit_phase_1_response(self, **kwargs):
            seen.append(_ledger(tmp_path))
            return await super().submit_phase_1_response(**kwargs)

    result = run_daily(PROMPT, budget_usd=15, out_dir=tmp_path,
                       client_factory=lambda **kw: _Peek(seed=0), now=NOW)
    first_view = seen[0]
    assert len(first_view) == 1
    started = first_view[0]
    assert started["status"] == "started"
    assert started["run_id"] == result["session_id"]
    assert started["budget_usd"] == 15.0
    assert started["started_at"] == NOW.isoformat(timespec="seconds")
    final = _ledger(tmp_path)
    assert [e["status"] for e in final] == ["started", "completed"]
    assert final[-1]["run_id"] == result["session_id"]


def test_a_started_run_that_never_finished_reserves_its_budget(tmp_path, no_network):
    # A kill -9 leaves only the started line; its budget still counts against the cap.
    (tmp_path / "ledger.jsonl").write_text(json.dumps({
        "run_id": "daily-killed", "status": "started", "budget_usd": 15.0,
        "month": "2026-11", "started_at": "2026-11-04T10:00:00+00:00",
    }) + "\n", encoding="utf-8")
    called = []

    def factory(**kwargs):
        called.append(True)
        return FixtureClient(seed=0)

    with pytest.raises(DailyRefused, match="monthly cap"):
        run_daily(PROMPT, budget_usd=15, out_dir=tmp_path, monthly_cap_usd=20,
                  client_factory=factory, now=NOW)
    assert called == []


def test_a_finished_line_supersedes_its_started_line(tmp_path, no_network):
    lines = [
        {"run_id": "daily-a", "status": "started", "budget_usd": 15.0, "month": "2026-11",
         "started_at": "2026-11-04T10:00:00+00:00"},
        {"run_id": "daily-a", "status": "completed", "cost_usd": 1.0, "budget_usd": 15.0,
         "month": "2026-11"},
    ]
    (tmp_path / "ledger.jsonl").write_text("".join(json.dumps(l) + "\n" for l in lines), encoding="utf-8")
    result = run_daily(PROMPT, budget_usd=15, out_dir=tmp_path, monthly_cap_usd=20,
                       client_factory=_fixture_factory, now=NOW)
    assert result["status"] == "completed"


def test_a_concurrent_run_sees_the_first_runs_reservation(tmp_path, no_network):
    # The second run starts while the first is mid-flight: the first one's budget is reserved.
    inner_refusals = []

    class _StartsASecondRun(FixtureClient):
        async def submit_phase_1_response(self, **kwargs):
            if not inner_refusals:
                try:
                    run_daily(PROMPT, budget_usd=15, out_dir=tmp_path, monthly_cap_usd=20,
                              client_factory=_fixture_factory, now=NOW)
                    inner_refusals.append(None)
                except DailyRefused as exc:
                    inner_refusals.append(str(exc))
            return await super().submit_phase_1_response(**kwargs)

    result = run_daily(PROMPT, budget_usd=15, out_dir=tmp_path, monthly_cap_usd=20,
                       client_factory=lambda **kw: _StartsASecondRun(seed=0), now=NOW)
    assert result["status"] == "completed"
    assert inner_refusals and inner_refusals[0] is not None
    assert "monthly cap" in inner_refusals[0]


def test_gate_waits_for_the_ledger_lock(tmp_path, no_network):
    import fcntl
    import threading

    ledger = tmp_path / "ledger.jsonl"
    ledger.touch()
    called = []
    done = threading.Event()

    def factory(**kwargs):
        called.append(True)
        return FixtureClient(seed=0)

    def worker():
        run_daily(PROMPT, budget_usd=15, out_dir=tmp_path, client_factory=factory, now=NOW)
        done.set()

    with ledger.open("a+") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX)
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        assert not done.wait(0.3)  # blocked at the gate
        assert called == []
        assert ledger.read_text() == ""
        fcntl.flock(held.fileno(), fcntl.LOCK_UN)
    t.join(10)
    assert done.is_set()
    assert called == [True]


def test_keyboard_interrupt_is_recorded_as_interrupted_and_reraised(tmp_path, no_network):
    class _CtrlC(FixtureClient):
        async def submit_phase_3_dialogue(self, **kwargs):
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_daily(PROMPT, budget_usd=15, out_dir=tmp_path,
                  client_factory=lambda **kw: _CtrlC(seed=0), now=NOW)
    entries = _ledger(tmp_path)
    assert [e["status"] for e in entries] == ["started", "interrupted"]
    run_dir = next(p for p in tmp_path.iterdir() if p.is_dir())
    sidecar = json.loads((run_dir / "sidecar.json").read_text(encoding="utf-8"))
    assert sidecar["status"] == "interrupted"
    salvage = json.loads((run_dir / "failed.json").read_text(encoding="utf-8"))
    assert salvage["status"] == "interrupted"
    assert len(salvage["completed_artifacts"]) == 15


def test_a_client_factory_failure_after_the_gate_still_closes_the_ledger(tmp_path, no_network):
    def factory(**kwargs):
        raise RuntimeError("bad key format")

    result = run_daily(PROMPT, budget_usd=15, out_dir=tmp_path, client_factory=factory, now=NOW)
    assert result["status"] == "failed"
    assert [e["status"] for e in _ledger(tmp_path)] == ["started", "failed"]


# --- Absolute run_dir that does not depend on cwd (item 1) ---

def test_run_dir_and_answer_path_are_absolute_for_a_relative_out_dir(tmp_path, monkeypatch, no_network):
    monkeypatch.chdir(tmp_path)
    result = run_daily(PROMPT, budget_usd=15, out_dir="rel/daily", client_factory=_fixture_factory, now=NOW)
    assert result["run_dir"].startswith(str(tmp_path.resolve()))
    assert result["answer_path"].startswith(str(tmp_path.resolve()))
    sidecar = json.loads((tmp_path / "rel/daily" / result["session_id"] / "sidecar.json").read_text())
    assert sidecar["run_dir"] == result["run_dir"]


# --- Real finish time and duration (item 9) ---

def test_ledger_records_real_finish_time_and_duration(tmp_path, no_network):
    import asyncio

    class _Slow(FixtureClient):
        async def submit_phase_3_dialogue(self, **kwargs):
            await asyncio.sleep(0.05)
            return await super().submit_phase_3_dialogue(**kwargs)

    run_daily(PROMPT, budget_usd=15, out_dir=tmp_path, client_factory=lambda **kw: _Slow(seed=0), now=NOW)
    final = _ledger(tmp_path)[-1]
    assert final["started_at"] == NOW.isoformat(timespec="seconds")
    assert final["duration_s"] >= 0.05
    assert datetime.fromisoformat(final["finished_at"]) > NOW


# --- Progress events (item 4) ---

def test_progress_callback_receives_orchestrator_events(tmp_path, no_network):
    events = []
    run_daily(PROMPT, budget_usd=15, out_dir=tmp_path, client_factory=_fixture_factory, now=NOW,
              progress_callback=events.append)
    kinds = [e.event_type for e in events]
    assert kinds[0] == "session_started"
    assert "phase_1_response_completed" in kinds
    assert kinds[-1] == "session_completed"
