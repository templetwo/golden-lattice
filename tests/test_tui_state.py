"""Tests for the TUI state accumulator and the elevation detector.

The renderer is stateful — each LatticeEvent mutates a TuiState. These tests
lock the fold behavior and the converge-pair elevation detection used by the
loom. Rendering output itself is not snapshot-tested; that surface is tuned
against the live terminal by hand.
"""

from __future__ import annotations

from pathlib import Path

from golden_lattice.events import (
    Phase0DatetimeGroundingEvent,
    Phase0FailedSearchEvent,
    Phase0FeedFrozenEvent,
    Phase0ProposalSubmittedEvent,
    Phase0SearchResultEvent,
    Phase1ClaimEvent,
    Phase1ResponseCompletedEvent,
    Phase1ResponseStartedEvent,
    Phase3TurnEvent,
    SessionCompletedEvent,
    SessionStartedEvent,
)
from golden_lattice.memory_graph.base import FocusTag, ModelId
from golden_lattice.memory_graph.store import JsonFileSessionStore
from golden_lattice.replay import replay_session_events
from golden_lattice.tui.state import (
    TuiState,
    apply_event,
    converge_pairs_per_claim,
)


def test_session_started_records_metadata_and_initializes_columns():
    state = TuiState()
    apply_event(
        state,
        SessionStartedEvent(
            timestamp_offset_ms=0,
            session_id="s1",
            prompt="p",
            prompt_hash="h",
            models_invited=(ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU),
        ),
    )
    assert state.session_id == "s1"
    assert state.invited_models == (ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU)
    # Phase 1 claim buckets pre-initialized so renderer panels can show empty
    # claim lists immediately rather than KeyError.
    assert state.phase_1_claims == {
        ModelId.OPUS: [],
        ModelId.SONNET: [],
        ModelId.HAIKU: [],
    }


def test_apply_event_folds_phase_1_in_order():
    state = TuiState()
    state.invited_models = (ModelId.OPUS,)
    apply_event(state, Phase1ResponseStartedEvent(timestamp_offset_ms=0, model_id=ModelId.OPUS))
    apply_event(
        state,
        Phase1ClaimEvent(
            timestamp_offset_ms=5000,
            model_id=ModelId.OPUS,
            claim_id="c1",
            text="claim text",
        ),
    )
    apply_event(
        state,
        Phase1ResponseCompletedEvent(
            timestamp_offset_ms=5000,
            model_id=ModelId.OPUS,
            focus_tag=FocusTag.CORRECTNESS,
            confidence=0.8,
            claim_count=1,
        ),
    )
    assert state.phase_1_started_ms[ModelId.OPUS] == 0
    assert state.phase_1_completed[ModelId.OPUS].confidence == 0.8
    assert len(state.phase_1_claims[ModelId.OPUS]) == 1
    assert state.phase_1_claims[ModelId.OPUS][0].claim_id == "c1"
    assert state.current_offset_ms == 5000


def test_session_completed_marks_state_complete():
    state = TuiState()
    apply_event(state, SessionCompletedEvent(timestamp_offset_ms=42, session_id="s1"))
    assert state.session_complete is True
    assert state.current_offset_ms == 42


def test_converge_pairs_requires_two_distinct_speakers_on_same_claim():
    """Rule 2 elevation condition: ≥2 converge turns from distinct speakers
    targeting the same claim_id. Self-amplification by one speaker's own two
    converges does not count."""
    state = TuiState()
    # Two converge turns from OPUS on the same claim — does NOT elevate.
    state.turns = [
        Phase3TurnEvent(
            timestamp_offset_ms=0,
            turn_id="t1",
            speaker_model=ModelId.OPUS,
            channel="converge",
            target_model=ModelId.SONNET,
            target_claim_ids=("claim_X",),
            content="opus 1",
        ),
        Phase3TurnEvent(
            timestamp_offset_ms=1,
            turn_id="t2",
            speaker_model=ModelId.OPUS,
            channel="converge",
            target_model=ModelId.HAIKU,
            target_claim_ids=("claim_X",),
            content="opus 2",
        ),
    ]
    assert converge_pairs_per_claim(state) == {}

    # Add a SONNET converge on the same claim — NOW it elevates.
    state.turns.append(
        Phase3TurnEvent(
            timestamp_offset_ms=2,
            turn_id="t3",
            speaker_model=ModelId.SONNET,
            channel="converge",
            target_model=ModelId.OPUS,
            target_claim_ids=("claim_X",),
            content="sonnet 1",
        ),
    )
    pairs = converge_pairs_per_claim(state)
    assert "claim_X" in pairs
    assert {t.speaker_model for t in pairs["claim_X"]} == {ModelId.OPUS, ModelId.SONNET}


def test_converge_pairs_ignores_non_converge_channels():
    state = TuiState()
    state.turns = [
        Phase3TurnEvent(
            timestamp_offset_ms=0,
            turn_id="t1",
            speaker_model=ModelId.OPUS,
            channel="critique",
            target_model=ModelId.SONNET,
            target_claim_ids=("claim_X",),
            content="not a converge",
        ),
        Phase3TurnEvent(
            timestamp_offset_ms=1,
            turn_id="t2",
            speaker_model=ModelId.HAIKU,
            channel="augment",
            target_model=None,
            target_claim_ids=("claim_X",),
            content="also not",
        ),
    ]
    assert converge_pairs_per_claim(state) == {}


# --- Phase 0 state folding ------------------------------------------------


def test_phase_0_grounding_event_folds_into_state():
    state = TuiState()
    state.invited_models = (ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU)
    apply_event(
        state,
        Phase0DatetimeGroundingEvent(
            timestamp_offset_ms=10,
            entry_id="abc123",
            timezone_name="America/New_York",
            formatted_text="2026-05-17 12:00:00 (America/New_York)",
        ),
    )
    assert state.phase_0_grounding is not None
    assert state.phase_0_grounding.entry_id == "abc123"
    assert state.phase_0_grounding.timezone_name == "America/New_York"


def test_phase_0_proposal_events_accumulate_per_model():
    state = TuiState()
    state.invited_models = (ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU)
    for m, qs in (
        (ModelId.OPUS, ("opus q1", "opus q2")),
        (ModelId.SONNET, ("sonnet q",)),
        (ModelId.HAIKU, ()),
    ):
        apply_event(
            state,
            Phase0ProposalSubmittedEvent(
                timestamp_offset_ms=0,
                model_id=m,
                queries=qs,
            ),
        )
    assert len(state.phase_0_proposals) == 3
    by_model = {p.model_id: p for p in state.phase_0_proposals}
    assert by_model[ModelId.OPUS].queries == ("opus q1", "opus q2")
    assert by_model[ModelId.HAIKU].queries == ()


def test_phase_0_search_result_event_folds_into_state():
    state = TuiState()
    apply_event(
        state,
        Phase0SearchResultEvent(
            timestamp_offset_ms=0,
            entry_id="entry1",
            query="some query",
            result_text_preview="Some result.",
            source_urls=("https://example.com",),
        ),
    )
    assert len(state.phase_0_search_results) == 1
    assert state.phase_0_search_results[0].query == "some query"


def test_phase_0_failed_search_event_folds_into_state():
    state = TuiState()
    apply_event(
        state,
        Phase0FailedSearchEvent(
            timestamp_offset_ms=0,
            entry_id="entry-failed",
            query="bad query",
            reason="rate limited",
        ),
    )
    assert len(state.phase_0_failed_searches) == 1
    assert state.phase_0_failed_searches[0].reason == "rate limited"


def test_phase_0_feed_frozen_event_folds_into_state():
    state = TuiState()
    apply_event(
        state,
        Phase0FeedFrozenEvent(
            timestamp_offset_ms=0,
            entry_count=4,
        ),
    )
    assert state.phase_0_feed_frozen is not None
    assert state.phase_0_feed_frozen.entry_count == 4


def test_claim_grounding_source_returns_feed_when_tool_provenance_matches():
    """Renderer helper: claim_grounding_source(state, claim_id) tells the
    trace ledger whether to mark a claim feed-grounded or prior-grounded."""
    from golden_lattice.tui.state import claim_grounding_source

    state = TuiState()
    apply_event(
        state,
        Phase0SearchResultEvent(
            timestamp_offset_ms=0,
            entry_id="feed-entry-x",
            query="q",
            result_text_preview="r",
        ),
    )
    # Phase 1 claim that references the feed entry via tool_provenance.
    apply_event(
        state,
        Phase1ClaimEvent(
            timestamp_offset_ms=1,
            model_id=ModelId.OPUS,
            claim_id="claim-from-feed",
            text="grounded in evidence",
        ),
    )
    # Tool provenance is tracked separately; for state-level testing, the
    # helper accepts an explicit set of provenance ids.
    assert claim_grounding_source(state, "claim-from-feed", ("feed-entry-x",)) == "feed"
    assert claim_grounding_source(state, "claim-from-feed", ()) == "prior"
    assert claim_grounding_source(state, "claim-from-feed", ("never-in-feed",)) == "unknown"


def test_full_lucumi_replay_round_trip_through_state():
    """End-to-end: walk the Lucumí session through replay → apply_event for
    every event, and assert the resulting state matches the persisted facts."""
    sessions_dir = Path(__file__).parent.parent / "sessions"
    path = sessions_dir / "session_20260504_071848_19b0600f.session.json"
    if not path.exists():
        import pytest
        pytest.skip("Lucumí session not present")

    store = JsonFileSessionStore(sessions_dir)
    session = store.load("session_20260504_071848_19b0600f")
    state = TuiState()
    for event in replay_session_events(session):
        apply_event(state, event)

    assert state.session_id == "session_20260504_071848_19b0600f"
    assert state.session_complete is True
    assert len(state.invited_models) == 3
    # 6 cross-readings, 3 taggings.
    assert len(state.cross_readings) == 6
    assert len(state.taggings) == 3
    # Phase 4 fully populated.
    assert state.artifact is not None
    assert state.metrics_event is not None
    assert state.flag_event is not None
    # The flagged reading present on Opus edge_case.
    assert len(state.flag_event.interpretations) == 1
    f = state.flag_event.interpretations[0]
    assert f.reading == "peer_divergence"
    assert f.source_model is ModelId.OPUS
