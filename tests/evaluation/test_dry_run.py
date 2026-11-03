"""Dry run through the real orchestrator with zero network use (step 2).

test_dry_run_makes_no_network_calls is the gate: socket creation is blocked
for the whole run, so any network attempt fails the test.
"""

from __future__ import annotations

import socket

import pytest

from golden_lattice.evaluation.runner import DRY_RUN_LABEL, dry_run
from golden_lattice.memory_graph.base import ModelId

THREE_PEERS = (ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU)
FOUR_PEERS = (ModelId.FABLE, ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU)
PROMPT = " ".join(["design a rate limiter for a public API"] * 20)


@pytest.fixture
def no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("network access attempted during a dry run")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


@pytest.mark.parametrize("peers", [THREE_PEERS, FOUR_PEERS], ids=["n3", "n4"])
def test_dry_run_makes_no_network_calls(no_network, peers):
    session = dry_run(PROMPT, peers=peers, session_id=f"dry-{len(peers)}")
    assert set(session.phase_1) == set(peers)


def test_dry_run_builds_real_session_for_three_peers(no_network):
    session = dry_run(PROMPT, peers=THREE_PEERS, seed=1, session_id="dry-n3-seed1")
    assert set(session.phase_1) == set(THREE_PEERS)
    assert session.session_id == "dry-n3-seed1"


def test_dry_run_is_reproducible_for_same_seed(no_network):
    a = dry_run(PROMPT, peers=THREE_PEERS, seed=7, session_id="dry-rep-a")
    b = dry_run(PROMPT, peers=THREE_PEERS, seed=7, session_id="dry-rep-b")
    claims_a = {m: tuple(c.text for c in r.claims) for m, r in a.phase_1.items()}
    claims_b = {m: tuple(c.text for c in r.claims) for m, r in b.phase_1.items()}
    assert claims_a == claims_b


def test_dry_run_label_marks_fixture_data():
    assert "fixture data" in DRY_RUN_LABEL
    assert "not evidence" in DRY_RUN_LABEL
