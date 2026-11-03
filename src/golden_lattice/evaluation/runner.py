"""Dry-run entry point (eval harness step 2).

Runs the real orchestrator (run_lattice_session) with the offline
FixtureClient. Exercises Phases 1 to 4 and builds a real Session, with zero
network calls. Output is labelled as fixture data, never evidence.
"""

from __future__ import annotations

from typing import Optional

from golden_lattice.evaluation.fixtures import FixtureClient
from golden_lattice.memory_graph.base import ModelId
from golden_lattice.memory_graph.schema import Session
from golden_lattice.orchestrator.config import LatticeConfig
from golden_lattice.orchestrator.orchestrator import run_lattice_session

DRY_RUN_LABEL = "dry-run (fixture data, not evidence)"


def dry_run(
    prompt: str,
    *,
    peers: tuple[ModelId, ...],
    seed: int = 0,
    session_id: Optional[str] = None,
) -> Session:
    """Run one lattice session through the orchestrator with the fixture client."""
    return run_lattice_session(
        prompt,
        config=LatticeConfig(),
        client=FixtureClient(seed=seed),
        invited_models=peers,
        session_id=session_id,
    )
