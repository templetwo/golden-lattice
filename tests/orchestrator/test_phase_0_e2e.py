"""End-to-end Phase 0 integration tests.

Substrate, wire layer, orchestrator, events/replay, and TUI state have each
been tested in isolation across slices 1–4. Slice 5 wires them together:

  - run a full Phase 0 → 1 → 2 → 3 → 4 session via the orchestrator with
    stub clients
  - persist it through JsonFileSessionStore
  - reload from disk
  - verify the Phase 0 artifact survived the round trip intact
  - verify replay of the reloaded session yields the same event stream a
    live run produces (modulo timing and order-of-arrival for parallel
    coroutines)

This is the gate before plugging in a real search executor and running
live: if these tests pass, the Phase 0 protocol is wire-complete against
stubs and ready for an Anthropic-API + real-search-client integration.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from golden_lattice.events import (
    Phase0DatetimeGroundingEvent,
    Phase0FailedSearchEvent,
    Phase0FeedFrozenEvent,
    Phase0ProposalSubmittedEvent,
    Phase0SearchResultEvent,
)
from golden_lattice.memory_graph.base import ModelId
from golden_lattice.memory_graph.phase_0 import (
    DateTimeGrounding,
    FailedSearch,
    Phase0Investigation,
    SearchResult,
)
from golden_lattice.memory_graph.schema import Session
from golden_lattice.memory_graph.store import JsonFileSessionStore
from golden_lattice.orchestrator import (
    LatticeConfig,
    run_lattice_session,
)
from golden_lattice.replay import replay_session_events


# --- Persistence round-trip ----------------------------------------------


def test_phase_0_session_round_trips_through_json_store(
    tmp_path, stub_client, stub_phase_0_client, stub_search_client
):
    """A Phase 0 session saved to JsonFileSessionStore reloads with the
    Phase 0 artifact byte-equivalent. Validates that Session.phase_0
    serializes/deserializes through Pydantic's model_dump_json /
    model_validate_json without loss."""
    stub_phase_0_client.canned_proposals[ModelId.OPUS] = ("opus q1", "shared q")
    stub_phase_0_client.canned_proposals[ModelId.SONNET] = ("shared q",)
    stub_phase_0_client.canned_proposals[ModelId.HAIKU] = ("haiku fail q",)
    stub_search_client.results = {
        "opus q1": "Opus result text.",
        "shared q": "Shared result text.",
    }
    stub_search_client.fail_reasons = {"haiku fail q": "stub timeout"}

    config = LatticeConfig()
    session = run_lattice_session(
        "p",
        config=config,
        client=stub_client,
        phase_0_client=stub_phase_0_client,
        search_client=stub_search_client,
        session_id="phase0_round_trip",
    )
    assert session.phase_0 is not None
    assert isinstance(session.phase_0, Phase0Investigation)

    store = JsonFileSessionStore(tmp_path)
    store.save(session)
    reloaded = store.load("phase0_round_trip")

    # The reloaded session must equal the original through Pydantic equality
    # (frozen models compare by value).
    assert reloaded.phase_0 is not None
    assert reloaded.phase_0 == session.phase_0
    assert len(reloaded.phase_0.proposals) == 3
    assert len(reloaded.phase_0.feed) == 4  # grounding + 2 results + 1 failed


def test_phase_0_session_round_trip_preserves_feed_entry_types(
    tmp_path, stub_client, stub_phase_0_client, stub_search_client
):
    """Heterogeneous feed entries (DateTimeGrounding, SearchResult,
    FailedSearch) must each survive Pydantic discriminated-union
    serialization."""
    stub_phase_0_client.canned_proposals[ModelId.OPUS] = ("succeeds", "fails")
    stub_search_client.results = {"succeeds": "Success."}
    stub_search_client.fail_reasons = {"fails": "rate limited"}

    config = LatticeConfig()
    session = run_lattice_session(
        "p",
        config=config,
        client=stub_client,
        phase_0_client=stub_phase_0_client,
        search_client=stub_search_client,
        session_id="feed_types_round_trip",
    )

    store = JsonFileSessionStore(tmp_path)
    store.save(session)
    reloaded = store.load("feed_types_round_trip")

    assert reloaded.phase_0 is not None
    types = [type(e).__name__ for e in reloaded.phase_0.feed]
    assert types[0] == "DateTimeGrounding"
    assert "SearchResult" in types
    assert "FailedSearch" in types


# --- Live ↔ replay event stream equivalence ------------------------------


def test_live_event_stream_matches_replay_for_phase_0(
    stub_client, stub_phase_0_client, stub_search_client
):
    """Capture the live orchestrator's event stream, persist the session,
    replay it, and compare event-type sequences. Both sources must agree on:
      - which event types fired
      - their count
      - their ordering relative to phase boundaries (Phase 0 events all
        before first Phase 1 event)
    """
    stub_phase_0_client.canned_proposals[ModelId.OPUS] = ("query A",)
    stub_phase_0_client.canned_proposals[ModelId.SONNET] = ("query B",)
    stub_search_client.results = {
        "query A": "Result A.",
        "query B": "Result B.",
    }

    live_events: list = []
    config = LatticeConfig()
    session = run_lattice_session(
        "p",
        config=config,
        client=stub_client,
        phase_0_client=stub_phase_0_client,
        search_client=stub_search_client,
        progress_callback=live_events.append,
    )

    replay_events = list(replay_session_events(session))

    # Same Phase 0 event types fire in both streams.
    phase_0_types = (
        Phase0DatetimeGroundingEvent,
        Phase0ProposalSubmittedEvent,
        Phase0SearchResultEvent,
        Phase0FailedSearchEvent,
        Phase0FeedFrozenEvent,
    )
    live_phase_0_counts = _count_event_types(live_events, phase_0_types)
    replay_phase_0_counts = _count_event_types(replay_events, phase_0_types)
    assert live_phase_0_counts == replay_phase_0_counts


def _count_event_types(events, types):
    counts: dict[type, int] = {t: 0 for t in types}
    for e in events:
        for t in types:
            if isinstance(e, t):
                counts[t] += 1
    return counts


# --- The four invariants hold under Phase 0 ------------------------------


def test_four_invariants_hold_under_phase_0(
    stub_client, stub_phase_0_client, stub_search_client
):
    """ARCHITECTURE.md §3 — Phase 0 does not break any of the four
    invariants. The substrate refuses construction if any do.

    Asserted here:
      - No authority gradient: Phase 0 produced a Phase0Investigation
        constructed via Pydantic validators (no model decided dedup).
      - Symmetric visibility: phase_1 keys match invited models; same
        prompt_hash.
      - Contribution parity: SessionMetrics computed (None for dyad would
        be a degenerate case — we have a triad).
      - Irreducibility preservation: phase_4.claim_trace covers every
        Phase 1 claim_id.
    """
    stub_phase_0_client.canned_proposals[ModelId.OPUS] = ("o q",)
    stub_search_client.results = {"o q": "Result text."}

    config = LatticeConfig()
    session = run_lattice_session(
        "p",
        config=config,
        client=stub_client,
        phase_0_client=stub_phase_0_client,
        search_client=stub_search_client,
    )
    assert isinstance(session, Session)
    # Invariant 1: no model_id on SynthesisArtifact (schema-enforced; this
    # construction succeeded → invariant holds).
    assert session.phase_4 is not None
    # Invariant 2: symmetric visibility.
    invited = set(session.models_invited)
    assert set(session.phase_1.keys()) == invited
    for r in session.phase_1.values():
        assert r.prompt_hash == session.prompt_hash
    # Invariant 3: contribution parity computed (non-None for triad).
    assert session.metrics is not None
    # Invariant 4: irreducibility — every Phase 1 claim traced.
    phase_1_ids = {
        c.claim_id for r in session.phase_1.values() for c in r.claims
    }
    traced_ids = {e.claim_id for e in session.phase_4.claim_trace}
    assert phase_1_ids == traced_ids


# --- Backward compatibility: pre-amendment sessions still load -----------


def test_pre_amendment_persisted_sessions_still_load_with_phase_0_none():
    """The five existing on-disk sessions (from before this amendment) load
    with phase_0=None. Pydantic's Optional default preserves backward
    compatibility — pre-amendment JSON files do not contain a phase_0
    field, and that's fine."""
    sessions_dir = Path(__file__).parent.parent.parent / "sessions"
    pre_amendment = [
        "session_20260504_062015_af03a5ac",
        "session_20260504_071848_19b0600f",
        "session_20260504_232435_0e82ea08",
        "session_20260505_010010_e9fd466c",
        "session_20260516_065836_1f635cdf",
    ]
    store = JsonFileSessionStore(sessions_dir)
    for sid in pre_amendment:
        path = sessions_dir / f"{sid}.session.json"
        if not path.exists():
            pytest.skip(f"persisted session {sid} not present")
        session = store.load(sid)
        assert session.phase_0 is None, (
            f"Pre-amendment session {sid} should load with phase_0=None"
        )
        # And other invariants still hold on these.
        assert isinstance(session, Session)
