"""Tests for Phase 0 wire layer — Phase0WireClient + SearchClient Protocols.

The wire layer is the seam between the orchestrator and the outside world.
Two interfaces:

  Phase0WireClient — a model-facing async client that takes a prompt and a
    max_queries cap and returns an InvestigationProposal. Each model runs
    this independently during Phase 0a (no peer visibility).

  SearchClient — a non-model search executor. Takes a query string and
    returns either a SearchResult (success) or a FailedSearch (typed
    failure). Authority-gradient-clean because no model decides what to
    return — the executor is pure I/O to a search service.

These tests exercise the Protocol contract and a minimal stub satisfying
each. Reference implementations against real services (Anthropic API for
proposals, Tavily / Brave / similar for search execution) are separate.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Union

from golden_lattice.exchange.phase_0_investigation import (
    Phase0WireClient,
    SearchClient,
)
from golden_lattice.memory_graph.base import INVESTIGATION_CAP, ModelId
from golden_lattice.memory_graph.phase_0 import (
    FailedSearch,
    InvestigationProposal,
    SearchResult,
    failed_search_id,
    search_result_id,
)


NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)


# --- Minimal stubs --------------------------------------------------------


class StubPhase0Client:
    """Stub Phase0WireClient — returns a canned proposal per model."""

    def __init__(self) -> None:
        self.canned: dict[ModelId, tuple[str, ...]] = {
            ModelId.OPUS: ("opus query 1", "opus query 2"),
            ModelId.SONNET: ("sonnet query 1",),
            ModelId.HAIKU: (),  # Haiku proposes nothing — empty union path
        }

    async def submit_investigation_proposal(
        self,
        *,
        model_id: ModelId,
        original_prompt: str,
        max_queries: int,
    ) -> InvestigationProposal:
        queries = self.canned.get(model_id, ())[:max_queries]
        return InvestigationProposal(model_id=model_id, queries=queries)


class StubSearchClient:
    """Stub SearchClient — pretends to execute a search. Maps a few canned
    queries to results; falls back to FailedSearch otherwise (for testing
    the failure path)."""

    def __init__(self) -> None:
        self.results: dict[str, str] = {
            "opus query 1": "Result text for opus query 1.",
            "opus query 2": "Result text for opus query 2.",
            "sonnet query 1": "Result text for sonnet query 1.",
        }
        self.fail_reasons: dict[str, str] = {}

    async def execute_search(self, query: str) -> Union[SearchResult, FailedSearch]:
        if query in self.fail_reasons:
            return FailedSearch(
                entry_id=failed_search_id(query, NOW),
                query=query,
                reason=self.fail_reasons[query],
                attempted_at=NOW,
            )
        if query in self.results:
            return SearchResult(
                entry_id=search_result_id(query, NOW),
                query=query,
                result_text=self.results[query],
                source_urls=("https://stub.example/" + query.replace(" ", "_"),),
                executed_at=NOW,
            )
        return FailedSearch(
            entry_id=failed_search_id(query, NOW),
            query=query,
            reason=f"stub has no canned result for {query!r}",
            attempted_at=NOW,
        )


# --- Protocol conformance -------------------------------------------------


def test_phase_0_wire_client_protocol_is_runtime_checkable():
    """Phase0WireClient is a runtime_checkable Protocol so isinstance() works
    against concrete clients."""
    stub = StubPhase0Client()
    assert isinstance(stub, Phase0WireClient)


def test_search_client_protocol_is_runtime_checkable():
    stub = StubSearchClient()
    assert isinstance(stub, SearchClient)


def test_phase_0_wire_client_protocol_rejects_non_conformant():
    class NotAClient:
        pass

    assert not isinstance(NotAClient(), Phase0WireClient)


def test_search_client_protocol_rejects_non_conformant():
    class NotAClient:
        pass

    assert not isinstance(NotAClient(), SearchClient)


# --- Stub behavior --------------------------------------------------------


def test_stub_phase_0_client_returns_proposal_with_model_id():
    stub = StubPhase0Client()
    p = asyncio.run(stub.submit_investigation_proposal(
        model_id=ModelId.OPUS,
        original_prompt="some prompt",
        max_queries=INVESTIGATION_CAP,
    ))
    assert isinstance(p, InvestigationProposal)
    assert p.model_id is ModelId.OPUS
    assert len(p.queries) <= INVESTIGATION_CAP


def test_stub_phase_0_client_respects_max_queries():
    """max_queries acts as a cap on what the wire client returns — even if
    the model would propose more, the orchestrator's cap kwarg constrains."""
    stub = StubPhase0Client()
    stub.canned[ModelId.OPUS] = ("q1", "q2", "q3", "q4", "q5")
    p = asyncio.run(stub.submit_investigation_proposal(
        model_id=ModelId.OPUS,
        original_prompt="p",
        max_queries=2,
    ))
    assert len(p.queries) == 2


def test_stub_phase_0_client_can_return_empty_proposal():
    """Haiku stub returns empty queries — empty-union path is a valid
    operational state, not a degenerate one."""
    stub = StubPhase0Client()
    p = asyncio.run(stub.submit_investigation_proposal(
        model_id=ModelId.HAIKU,
        original_prompt="p",
        max_queries=INVESTIGATION_CAP,
    ))
    assert p.queries == ()


def test_stub_search_client_returns_search_result():
    stub = StubSearchClient()
    out = asyncio.run(stub.execute_search("opus query 1"))
    assert isinstance(out, SearchResult)
    assert out.query == "opus query 1"
    assert "opus query 1" in out.result_text
    assert out.entry_id == search_result_id(out.query, out.executed_at)


def test_stub_search_client_returns_failed_search_on_no_match():
    stub = StubSearchClient()
    out = asyncio.run(stub.execute_search("unknown query"))
    assert isinstance(out, FailedSearch)
    assert out.query == "unknown query"
    assert out.reason  # non-empty per substrate validator


def test_stub_search_client_simulates_explicit_failure():
    """The stub can be configured to fail on a specific query — useful for
    orchestrator tests that need to exercise the failed-search-as-feed-entry
    path."""
    stub = StubSearchClient()
    stub.fail_reasons["opus query 1"] = "rate limited by stub"
    out = asyncio.run(stub.execute_search("opus query 1"))
    assert isinstance(out, FailedSearch)
    assert out.reason == "rate limited by stub"
