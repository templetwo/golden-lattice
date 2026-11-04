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
from golden_lattice.orchestrator.errors import OrchestratorProviderError

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


def test_recording_client_captures_every_call_of_a_three_peer_session(tmp_path, no_network):
    recorder = RecordingClient(FixtureClient(seed=0))
    result = run_daily(PROMPT, budget_usd=15, out_dir=tmp_path,
                       client_factory=lambda **kw: recorder, now=NOW)
    assert result["status"] == "completed"
    assert len(recorder.completed) == 18
