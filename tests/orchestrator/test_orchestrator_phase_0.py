"""Tests for orchestrator Phase 0 (Investigation) plumbing.

The orchestrator runs Phase 0 when both phase_0_client and search_client are
provided. When either is None, Phase 0 is skipped (Session.phase_0 stays
None — backward compatible with pre-amendment sessions).

These tests cover the _run_phase_0 helper's behavior and the orchestrator
signature's Phase 0 path end-to-end through stub clients.
"""

from __future__ import annotations

import pytest

from golden_lattice.memory_graph.base import (
    INVESTIGATION_TIMEZONE,
    ModelId,
)
from golden_lattice.memory_graph.phase_0 import (
    DateTimeGrounding,
    FailedSearch,
    Phase0Investigation,
    SearchResult,
)
from golden_lattice.memory_graph.schema import Session
from golden_lattice.orchestrator import (
    LatticeConfig,
    run_lattice_session,
)


# --- _run_phase_0 helper (via run_lattice_session end-to-end) ------------


def test_phase_0_seeds_datetime_grounding_first(
    stub_client, stub_phase_0_client, stub_search_client
):
    """The first feed entry must always be DateTimeGrounding, deterministic,
    in INVESTIGATION_TIMEZONE."""
    config = LatticeConfig()
    session = run_lattice_session(
        "p",
        config=config,
        client=stub_client,
        phase_0_client=stub_phase_0_client,
        search_client=stub_search_client,
    )
    assert session.phase_0 is not None
    assert isinstance(session.phase_0, Phase0Investigation)
    first = session.phase_0.feed[0]
    assert isinstance(first, DateTimeGrounding)
    assert first.timezone_name == INVESTIGATION_TIMEZONE


def test_phase_0_collects_proposals_from_all_invited_models(
    stub_client, stub_phase_0_client, stub_search_client
):
    """One InvestigationProposal per invited model, regardless of whether
    that model proposed any queries."""
    config = LatticeConfig()
    session = run_lattice_session(
        "p",
        config=config,
        client=stub_client,
        phase_0_client=stub_phase_0_client,
        search_client=stub_search_client,
    )
    assert session.phase_0 is not None
    proposed_models = {p.model_id for p in session.phase_0.proposals}
    assert proposed_models == {ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU}


def test_phase_0_unions_dedup_queries_across_proposals(
    stub_client, stub_phase_0_client, stub_search_client
):
    """Two models proposing the same query → exactly one search dispatched.
    Rule-based exact union, never semantic merge."""
    stub_phase_0_client.canned_proposals[ModelId.OPUS] = ("shared query",)
    stub_phase_0_client.canned_proposals[ModelId.SONNET] = ("shared query",)
    stub_phase_0_client.canned_proposals[ModelId.HAIKU] = ("unique haiku query",)
    stub_search_client.results = {
        "shared query": "Shared result.",
        "unique haiku query": "Haiku-specific result.",
    }
    config = LatticeConfig()
    session = run_lattice_session(
        "p",
        config=config,
        client=stub_client,
        phase_0_client=stub_phase_0_client,
        search_client=stub_search_client,
    )
    assert session.phase_0 is not None
    # Feed: 1 grounding + 2 unique searches = 3 entries.
    assert len(session.phase_0.feed) == 3
    # Verify the unique queries that landed.
    search_queries = {
        e.query for e in session.phase_0.feed
        if not isinstance(e, DateTimeGrounding)
    }
    assert search_queries == {"shared query", "unique haiku query"}


def test_phase_0_handles_failed_search_as_typed_feed_entry(
    stub_client, stub_phase_0_client, stub_search_client
):
    """A failed search is a FailedSearch entry in the feed (all peers see),
    not a session abort. §8 no-silent-failures."""
    stub_phase_0_client.canned_proposals[ModelId.OPUS] = ("query that succeeds", "query that fails")
    stub_search_client.results = {"query that succeeds": "Success."}
    stub_search_client.fail_reasons = {"query that fails": "rate limit hit"}
    config = LatticeConfig()
    session = run_lattice_session(
        "p",
        config=config,
        client=stub_client,
        phase_0_client=stub_phase_0_client,
        search_client=stub_search_client,
    )
    assert session.phase_0 is not None
    failed = [
        e for e in session.phase_0.feed if isinstance(e, FailedSearch)
    ]
    assert len(failed) == 1
    assert failed[0].query == "query that fails"
    assert failed[0].reason == "rate limit hit"
    succeeded = [
        e for e in session.phase_0.feed if isinstance(e, SearchResult)
    ]
    assert len(succeeded) == 1


def test_phase_0_empty_union_produces_grounding_only_feed(
    stub_client, stub_phase_0_client, stub_search_client
):
    """When every proposal is empty, the feed contains only the
    DateTimeGrounding entry. Valid degenerate path."""
    # Default StubPhase0Client.canned_proposals are all empty.
    config = LatticeConfig()
    session = run_lattice_session(
        "p",
        config=config,
        client=stub_client,
        phase_0_client=stub_phase_0_client,
        search_client=stub_search_client,
    )
    assert session.phase_0 is not None
    assert len(session.phase_0.feed) == 1
    assert isinstance(session.phase_0.feed[0], DateTimeGrounding)


def test_phase_0_skipped_when_clients_not_provided(stub_client):
    """Backward compatibility: existing callers that don't pass Phase 0
    clients see Session.phase_0 = None and pre-amendment behavior."""
    config = LatticeConfig()
    session = run_lattice_session(
        "p",
        config=config,
        client=stub_client,
    )
    assert session.phase_0 is None
    # Other phases run normally.
    assert session.phase_4 is not None
    assert session.metrics is not None


def test_phase_0_requires_both_clients_or_neither(
    stub_client, stub_phase_0_client
):
    """Asymmetric configuration (one client provided, the other not) is a
    user error — refuse at the orchestrator boundary rather than silently
    skip."""
    config = LatticeConfig()
    with pytest.raises(ValueError, match="phase_0_client and search_client"):
        run_lattice_session(
            "p",
            config=config,
            client=stub_client,
            phase_0_client=stub_phase_0_client,
            # search_client missing
        )


def test_phase_0_full_pipeline_produces_substrate_valid_session(
    stub_client, stub_phase_0_client, stub_search_client
):
    """End-to-end: Phase 0 → 1 → 2 → 3 → 4 with Phase 0 enabled produces a
    substrate-valid Session. All four invariants hold."""
    stub_phase_0_client.canned_proposals[ModelId.OPUS] = ("o query",)
    stub_phase_0_client.canned_proposals[ModelId.SONNET] = ("s query",)
    stub_search_client.results = {
        "o query": "Opus result.",
        "s query": "Sonnet result.",
    }
    config = LatticeConfig()
    session = run_lattice_session(
        "p",
        config=config,
        client=stub_client,
        phase_0_client=stub_phase_0_client,
        search_client=stub_search_client,
    )
    assert isinstance(session, Session)
    assert session.phase_0 is not None
    assert session.phase_4 is not None
    # Phase 0 feed includes grounding + 2 search results.
    assert len(session.phase_0.feed) == 3
    # Proposals from all 3 invited models, even though Haiku proposed nothing.
    assert len(session.phase_0.proposals) == 3
    # Parity still computes (Phase 0 does not break the parity pipeline).
    assert session.metrics is not None
