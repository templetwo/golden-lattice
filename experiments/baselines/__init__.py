"""Baseline SUT adapters for longitudinal experiment comparison (Task 8).

Declared SUTs (required):
  - strongest_single_peer
  - simple_parallel_responses
  - conventional_judge_summarizer  (optional, non-canonical)
  - golden_lattice

These adapters share one protocol boundary so every system receives the same
four task steps and returns structured step records. They do not alter
canonical Phase 4 synthesis.
"""

from __future__ import annotations

from experiments.baselines.conventional_judge_summarizer import (
    ConventionalJudgeSummarizerSUT,
)
from experiments.baselines.golden_lattice import GoldenLatticeSUT
from experiments.baselines.protocol import SUT
from experiments.baselines.simple_parallel_responses import SimpleParallelResponsesSUT
from experiments.baselines.strongest_single_peer import StrongestSinglePeerSUT

REQUIRED_SUT_IDS: tuple[str, ...] = (
    "strongest_single_peer",
    "simple_parallel_responses",
    "conventional_judge_summarizer",
    "golden_lattice",
)

SUT_REGISTRY: dict[str, SUT] = {
    "strongest_single_peer": StrongestSinglePeerSUT(),
    "simple_parallel_responses": SimpleParallelResponsesSUT(),
    "conventional_judge_summarizer": ConventionalJudgeSummarizerSUT(),
    "golden_lattice": GoldenLatticeSUT(),
}


def get_sut(sut_id: str) -> SUT:
    try:
        return SUT_REGISTRY[sut_id]
    except KeyError as exc:
        known = ", ".join(REQUIRED_SUT_IDS)
        raise KeyError(f"unknown sut_id {sut_id!r}; known: {known}") from exc


__all__ = [
    "REQUIRED_SUT_IDS",
    "SUT_REGISTRY",
    "get_sut",
    "SUT",
]
