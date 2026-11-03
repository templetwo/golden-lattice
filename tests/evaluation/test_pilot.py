"""Pilot run refusals and hard stops (no network: the factory must never be called)."""

from __future__ import annotations

from pathlib import Path

import pytest

from golden_lattice.evaluation.benchmark import Task
from golden_lattice.evaluation.cost import SpendGateError
from golden_lattice.evaluation.pilot import run_pilot
from golden_lattice.memory_graph.base import ModelId

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
