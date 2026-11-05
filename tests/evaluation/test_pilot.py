"""Pilot run refusals and hard stops (no network: the factory must never be called)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from golden_lattice.evaluation.benchmark import Task
from golden_lattice.evaluation.cost import SpendGateError
from golden_lattice.evaluation.fixtures import FixtureClient
from golden_lattice.evaluation.pilot import run_pilot
from golden_lattice.memory_graph.base import ModelId
from golden_lattice.orchestrator.errors import OrchestratorProviderError, OrchestratorRefusalError

PEERS = (ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU)
TASKS = (Task(id="t1", kind="system_design", prompt=" ".join(["word"] * 160)),)


def _no_calls(**kwargs):
    raise AssertionError("a client was created for a run that should not start")


def test_pilot_refused_without_approver(tmp_path: Path):
    with pytest.raises(SpendGateError, match="approved_by"):
        run_pilot(
            api_key="k", tasks=TASKS, peers=PEERS, budget_usd=10, approved_by=None,
            out_dir=tmp_path, label="test", client_factory=_no_calls,
        )
    assert not any(tmp_path.iterdir())


def test_pilot_refused_above_ceiling(tmp_path: Path):
    with pytest.raises(SpendGateError, match="exceeds the ceiling"):
        run_pilot(
            api_key="k", tasks=TASKS, peers=PEERS, budget_usd=500, approved_by="Anthony",
            out_dir=tmp_path, label="test", client_factory=_no_calls,
        )


def test_pilot_stops_before_any_call_when_budget_cannot_cover_one_session(tmp_path: Path):
    summary = run_pilot(
        api_key="k", tasks=TASKS, peers=PEERS, budget_usd=0.01, approved_by="Anthony",
        out_dir=tmp_path, label="test", client_factory=_no_calls,
    )
    assert summary["status"] == "budget_stop"
    assert summary["sessions"][0]["status"] == "not_started_budget"
    assert summary["spent_usd"] == 0


class _RefusedInPhase3(FixtureClient):
    async def submit_phase_3_dialogue(self, **kwargs):
        raise OrchestratorRefusalError(
            model=ModelId.HAIKU, phase="phase_3", category="cyber", explanation="exploit development",
        )


class _FailsInPhase3(FixtureClient):
    async def submit_phase_3_dialogue(self, **kwargs):
        raise OrchestratorProviderError(
            model=ModelId.HAIKU, phase="phase_3", underlying=RuntimeError("upstream 529"),
        )


def _eval_record(tmp_path: Path, client_cls) -> dict:
    summary = run_pilot(
        api_key="k", tasks=TASKS, peers=PEERS, budget_usd=15, approved_by="Anthony",
        out_dir=tmp_path, label="test", client_factory=lambda **kw: client_cls(seed=0),
    )
    session = summary["sessions"][0]
    assert session["status"] == "failed"
    path = tmp_path / summary["run_id"] / f"{session['session_id']}.eval.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_pilot_eval_record_of_a_refused_session_carries_category_and_explanation(tmp_path: Path):
    record = _eval_record(tmp_path, _RefusedInPhase3)
    assert record["error"].startswith("OrchestratorRefusalError: ")
    assert "exploit development" in record["error"]
    assert record["category"] == "cyber"
    assert record["explanation"] == "exploit development"


def test_pilot_eval_record_of_a_non_refusal_failure_has_null_refusal_fields(tmp_path: Path):
    record = _eval_record(tmp_path, _FailsInPhase3)
    assert record["category"] is None
    assert record["explanation"] is None
