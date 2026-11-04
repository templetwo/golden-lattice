"""M1 estimate: worst case and expected from matching history; no network."""

from __future__ import annotations

import json
import socket

import pytest

from golden_lattice.evaluation.estimate import estimate, expected_from_history, timeouts_of
from golden_lattice.memory_graph.base import ModelId
from golden_lattice.orchestrator.config import LatticeConfig, TokenAllowance

PEERS = (ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU)


@pytest.fixture
def no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


def _run_json(root, name, *, costs, allowance=None, peers=None):
    run_dir = root / name
    run_dir.mkdir(parents=True)
    run = {
        "peers": peers or [m.value for m in PEERS],
        "allowance": allowance or TokenAllowance().model_dump(),
        "timeouts_seconds": timeouts_of(LatticeConfig()),
        "sessions": [{"status": "completed", "session_cost_usd": c} for c in costs],
    }
    (run_dir / "run.json").write_text(json.dumps(run), encoding="utf-8")


def test_expected_uses_matching_runs_only(tmp_path):
    _run_json(tmp_path, "a", costs=[1.0, 2.0, 3.0])
    _run_json(tmp_path, "b", costs=[100.0], allowance=TokenAllowance(phase_1_response=4096).model_dump())
    result = expected_from_history(tmp_path, peers=PEERS, allowance=TokenAllowance(), config=LatticeConfig())
    assert result["n"] == 3
    assert result["matched_runs"] == 1
    assert result["p50"] == 2.0
    assert result["p95"] == 3.0


def test_expected_says_so_when_history_is_thin(tmp_path):
    _run_json(tmp_path, "a", costs=[1.0])
    result = expected_from_history(tmp_path, peers=PEERS, allowance=TokenAllowance(), config=LatticeConfig())
    assert result["p50"] is None
    assert "insufficient" in result["note"]


def test_estimate_issues_no_calls_and_labels_both_numbers(tmp_path, no_network):
    result = estimate(tmp_path, peers=PEERS, allowance=TokenAllowance(), config=LatticeConfig())
    assert result["worst_case_usd"] > 0
    assert "ceiling" in result["labels"]["worst_case_usd"]
    assert "Haiku" in result["labels"]["expected"]


def test_estimate_worst_case_grows_with_the_phase_1_cap(tmp_path, no_network):
    small = estimate(tmp_path, peers=PEERS, allowance=TokenAllowance(phase_1_response=4096), config=LatticeConfig())
    large = estimate(tmp_path, peers=PEERS, allowance=TokenAllowance(phase_1_response=16384), config=LatticeConfig())
    assert large["worst_case_usd"] > small["worst_case_usd"]
