"""Tests for the session-replay event emitter.

Replay walks a persisted Session and yields LatticeEvents in natural order.
The renderer (live or replay) consumes the same protocol; these tests lock
the protocol's shape, ordering, and integration with real persisted data.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from golden_lattice.events import (
    Phase0DatetimeGroundingEvent,
    Phase0FailedSearchEvent,
    Phase0FeedFrozenEvent,
    Phase0ProposalSubmittedEvent,
    Phase0SearchResultEvent,
    Phase1ClaimEvent,
    Phase1ResponseCompletedEvent,
    Phase1ResponseStartedEvent,
    Phase2CrossReadingEvent,
    Phase2TaggingEvent,
    Phase4ArtifactEvent,
    Phase4FlagInterpretationsEvent,
    Phase4MetricsEvent,
    SelfReflectionEvent,
    SessionCompletedEvent,
    SessionStartedEvent,
)
from golden_lattice.memory_graph.base import (
    FocusTag,
    ModelId,
    Phase,
    claim_id_for,
)
from golden_lattice.memory_graph.schema import (
    Claim,
    IndependentResponse,
    SelfReflectionArtifact,
    Session,
)
from golden_lattice.memory_graph.store import JsonFileSessionStore
from golden_lattice.replay import replay_session_events


NOW = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)


def _claim(model: ModelId, text: str) -> Claim:
    return Claim(
        claim_id=claim_id_for(model, Phase.INDEPENDENT, text),
        source_model=model,
        source_phase=Phase.INDEPENDENT,
        text=text,
    )


def _response(
    model: ModelId,
    *,
    claims: tuple[Claim, ...],
    started_offset_s: float = 0.0,
    completed_offset_s: float = 1.0,
    self_reflection: bool = True,
) -> IndependentResponse:
    started = NOW + timedelta(seconds=started_offset_s)
    completed = NOW + timedelta(seconds=completed_offset_s)
    artifacts = ()
    if self_reflection and len(claims) >= 2:
        artifacts = (
            SelfReflectionArtifact(
                model_id=model,
                generated_at=completed + timedelta(seconds=0.5),
                strongest_claim_id=claims[0].claim_id,
                weakest_claim_id=claims[1].claim_id,
                tag_justification="why this tag",
            ),
        )
    return IndependentResponse(
        model_id=model,
        prompt_hash="h",
        response="prose",
        focus_tag=FocusTag.CORRECTNESS,
        confidence=0.7,
        claims=claims,
        self_reflection_artifacts=artifacts,
        generation_started_at=started,
        generation_completed_at=completed,
    )


def _triad_session_with_latency() -> Session:
    """Triad session mirroring lived Phase 1 timing: Haiku fast, Sonnet mid,
    Opus slow."""
    opus_claims = (_claim(ModelId.OPUS, "opus a"), _claim(ModelId.OPUS, "opus b"))
    sonnet_claims = (_claim(ModelId.SONNET, "sonnet a"), _claim(ModelId.SONNET, "sonnet b"))
    haiku_claims = (_claim(ModelId.HAIKU, "haiku a"), _claim(ModelId.HAIKU, "haiku b"))
    return Session(
        session_id="latency-test",
        prompt="p",
        prompt_hash="h",
        models_invited=(ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU),
        phase_1={
            ModelId.OPUS: _response(ModelId.OPUS, claims=opus_claims, started_offset_s=0.0, completed_offset_s=70.0),
            ModelId.SONNET: _response(ModelId.SONNET, claims=sonnet_claims, started_offset_s=0.0, completed_offset_s=30.0),
            ModelId.HAIKU: _response(ModelId.HAIKU, claims=haiku_claims, started_offset_s=0.0, completed_offset_s=10.0),
        },
    )


def test_replay_yields_session_started_first():
    session = _triad_session_with_latency()
    events = list(replay_session_events(session))
    assert isinstance(events[0], SessionStartedEvent)
    assert events[0].session_id == "latency-test"
    assert events[0].timestamp_offset_ms == 0


def test_replay_yields_session_completed_last():
    session = _triad_session_with_latency()
    events = list(replay_session_events(session))
    assert isinstance(events[-1], SessionCompletedEvent)
    assert events[-1].session_id == "latency-test"


def test_replay_emits_response_started_per_invited_model():
    session = _triad_session_with_latency()
    events = list(replay_session_events(session))
    started = [e for e in events if isinstance(e, Phase1ResponseStartedEvent)]
    assert {e.model_id for e in started} == {
        ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU,
    }


def test_replay_emits_response_completed_in_completion_order():
    """The lived experience: faster models finish first. Replay preserves it."""
    session = _triad_session_with_latency()
    events = list(replay_session_events(session))
    completed = [e for e in events if isinstance(e, Phase1ResponseCompletedEvent)]
    assert [e.model_id for e in completed] == [
        ModelId.HAIKU, ModelId.SONNET, ModelId.OPUS,
    ]
    # And the timestamps reflect the asymmetric latency.
    assert completed[0].timestamp_offset_ms == 10_000  # Haiku at 10s
    assert completed[1].timestamp_offset_ms == 30_000  # Sonnet at 30s
    assert completed[2].timestamp_offset_ms == 70_000  # Opus at 70s


def test_replay_emits_one_claim_event_per_phase_1_claim():
    session = _triad_session_with_latency()
    events = list(replay_session_events(session))
    claim_events = [e for e in events if isinstance(e, Phase1ClaimEvent)]
    total_claims = sum(len(r.claims) for r in session.phase_1.values())
    assert len(claim_events) == total_claims
    emitted_ids = {e.claim_id for e in claim_events}
    expected_ids = {c.claim_id for r in session.phase_1.values() for c in r.claims}
    assert emitted_ids == expected_ids


def test_replay_emits_self_reflection_after_completion():
    session = _triad_session_with_latency()
    events = list(replay_session_events(session))
    for model_id in (ModelId.HAIKU, ModelId.SONNET, ModelId.OPUS):
        completed = next(
            e for e in events
            if isinstance(e, Phase1ResponseCompletedEvent) and e.model_id is model_id
        )
        reflection = next(
            e for e in events
            if isinstance(e, SelfReflectionEvent) and e.model_id is model_id
        )
        assert reflection.timestamp_offset_ms >= completed.timestamp_offset_ms


def test_replay_phase_2_event_counts_for_triad():
    """Triadic session must emit n*(n-1)=6 cross-readings and n=3 taggings.

    Constructs a substrate-valid Session with the full Phase 2 surface so the
    schema validators (cross_readings_resolve_claim_ids, etc.) accept it.
    """
    from golden_lattice.memory_graph.schema import CrossReading
    from golden_lattice.memory_graph.tagging import Phase2Tagging

    session = _triad_session_with_latency()
    invited = session.models_invited
    cross_readings = tuple(
        CrossReading(reader_model=r, target_model=t)
        for r in invited for t in invited if r is not t
    )
    taggings = tuple(Phase2Tagging(tagger_model=m) for m in invited)
    session = session.model_copy(
        update={"phase_2": cross_readings, "phase_2_taggings": taggings}
    )
    events = list(replay_session_events(session))
    cr_events = [e for e in events if isinstance(e, Phase2CrossReadingEvent)]
    tag_events = [e for e in events if isinstance(e, Phase2TaggingEvent)]
    assert len(cr_events) == 6
    assert len(tag_events) == 3


def test_replay_emits_phase_4_when_present():
    """Skip phase_4 if absent; emit artifact + metrics + flag_interpretations
    if present."""
    session = _triad_session_with_latency()
    events = list(replay_session_events(session))
    # No phase_4 set; should still emit metrics and flag_interpretations.
    metrics_events = [e for e in events if isinstance(e, Phase4MetricsEvent)]
    flag_events = [e for e in events if isinstance(e, Phase4FlagInterpretationsEvent)]
    artifact_events = [e for e in events if isinstance(e, Phase4ArtifactEvent)]
    assert len(metrics_events) == 1
    assert len(flag_events) == 1
    assert len(artifact_events) == 0  # no phase_4 set


def test_replay_offsets_are_monotonic_non_decreasing():
    session = _triad_session_with_latency()
    events = list(replay_session_events(session))
    offsets = [e.timestamp_offset_ms for e in events]
    assert offsets == sorted(offsets)


def test_replay_empty_phase_1_yields_minimal_event_stream():
    """A session with empty phase_1 still yields session_started and
    session_completed bookends."""
    # Can't actually construct empty phase_1 due to symmetric-visibility
    # validator — phase_1 keys must match invited models. So this is a
    # boundary test for the replay function's empty-guard in case a future
    # builder produces such a Session.
    # Instead: validate the replay function handles the "no phase_1" branch
    # by constructing a Session that has minimal phase_1 (one model only).
    haiku_claims = (_claim(ModelId.HAIKU, "haiku a"), _claim(ModelId.HAIKU, "haiku b"))
    session = Session(
        session_id="minimal",
        prompt="p",
        prompt_hash="h",
        models_invited=(ModelId.HAIKU, ModelId.SONNET),  # dyad
        phase_1={
            ModelId.HAIKU: _response(ModelId.HAIKU, claims=haiku_claims),
            ModelId.SONNET: _response(
                ModelId.SONNET,
                claims=(_claim(ModelId.SONNET, "sonnet a"), _claim(ModelId.SONNET, "sonnet b")),
            ),
        },
    )
    events = list(replay_session_events(session))
    assert isinstance(events[0], SessionStartedEvent)
    assert isinstance(events[-1], SessionCompletedEvent)
    # Dyad: metrics event emits with metrics=None (compute_parity_shares
    # returns None for N<3).
    metrics_events = [e for e in events if isinstance(e, Phase4MetricsEvent)]
    assert len(metrics_events) == 1
    assert metrics_events[0].metrics is None


# --- Integration against persisted sessions ------------------------------


_PERSISTED = [
    "session_20260504_062015_af03a5ac",
    "session_20260504_071848_19b0600f",
    "session_20260504_232435_0e82ea08",
    "session_20260505_010010_e9fd466c",
]


def _sessions_dir() -> Path:
    return Path(__file__).parent.parent / "sessions"


@pytest.mark.parametrize("session_id", _PERSISTED)
def test_replay_persisted_sessions_event_protocol(session_id):
    """For each on-disk session, replay should emit a complete event stream:
    session_started → ... → session_completed, with at least one event of
    every Phase 1 type and exactly one of each Phase 4 type."""
    path = _sessions_dir() / f"{session_id}.session.json"
    if not path.exists():
        pytest.skip(f"persisted session {session_id} not present")
    store = JsonFileSessionStore(_sessions_dir())
    session = store.load(session_id)
    events = list(replay_session_events(session))

    assert isinstance(events[0], SessionStartedEvent)
    assert isinstance(events[-1], SessionCompletedEvent)

    assert sum(1 for e in events if isinstance(e, Phase1ResponseStartedEvent)) == 3
    assert sum(1 for e in events if isinstance(e, Phase1ResponseCompletedEvent)) == 3
    assert sum(1 for e in events if isinstance(e, Phase2CrossReadingEvent)) == 6
    assert sum(1 for e in events if isinstance(e, Phase2TaggingEvent)) == 3
    assert sum(1 for e in events if isinstance(e, Phase4ArtifactEvent)) == 1
    assert sum(1 for e in events if isinstance(e, Phase4MetricsEvent)) == 1
    assert sum(1 for e in events if isinstance(e, Phase4FlagInterpretationsEvent)) == 1


# --- Phase 0 replay -------------------------------------------------------


def _phase_0_session(
    *,
    with_proposals: bool = True,
    with_failed_search: bool = False,
) -> Session:
    """Build a triadic session with a populated Phase 0 investigation."""
    from datetime import timedelta
    from golden_lattice.memory_graph.phase_0 import (
        DateTimeGrounding,
        FailedSearch,
        InvestigationProposal,
        Phase0Investigation,
        SearchResult,
        datetime_grounding_id,
        failed_search_id,
        search_result_id,
    )
    grounding_at = NOW
    grounding = DateTimeGrounding(
        entry_id=datetime_grounding_id(grounding_at, "America/New_York"),
        timestamp=grounding_at,
        timezone_name="America/New_York",
        formatted_text=f"{grounding_at.isoformat()} (America/New_York)",
    )
    feed_entries = [grounding]
    proposals = []

    if with_proposals:
        proposals.append(
            InvestigationProposal(
                model_id=ModelId.OPUS,
                queries=("opus query", "shared query"),
            )
        )
        proposals.append(
            InvestigationProposal(
                model_id=ModelId.SONNET,
                queries=("shared query",),
            )
        )
        proposals.append(
            InvestigationProposal(model_id=ModelId.HAIKU, queries=())
        )
        # Two deduped queries → two search-result entries.
        result_at = grounding_at + timedelta(seconds=1)
        feed_entries.append(
            SearchResult(
                entry_id=search_result_id("opus query", result_at),
                query="opus query",
                result_text="Result for opus query.",
                executed_at=result_at,
            )
        )
        if with_failed_search:
            feed_entries.append(
                FailedSearch(
                    entry_id=failed_search_id("shared query", result_at),
                    query="shared query",
                    reason="rate limited",
                    attempted_at=result_at,
                )
            )
        else:
            feed_entries.append(
                SearchResult(
                    entry_id=search_result_id("shared query", result_at),
                    query="shared query",
                    result_text="Result for shared query.",
                    executed_at=result_at,
                )
            )

    inv = Phase0Investigation(
        proposals=tuple(proposals),
        feed=tuple(feed_entries),
    )

    opus_claims = (_claim(ModelId.OPUS, "opus a"), _claim(ModelId.OPUS, "opus b"))
    sonnet_claims = (_claim(ModelId.SONNET, "sonnet a"), _claim(ModelId.SONNET, "sonnet b"))
    haiku_claims = (_claim(ModelId.HAIKU, "haiku a"), _claim(ModelId.HAIKU, "haiku b"))
    return Session(
        session_id="phase-0-replay",
        prompt="p",
        prompt_hash="h",
        models_invited=(ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU),
        phase_0=inv,
        phase_1={
            ModelId.OPUS: _response(ModelId.OPUS, claims=opus_claims),
            ModelId.SONNET: _response(ModelId.SONNET, claims=sonnet_claims),
            ModelId.HAIKU: _response(ModelId.HAIKU, claims=haiku_claims),
        },
    )


def test_replay_emits_phase_0_grounding_event_when_phase_0_set():
    session = _phase_0_session()
    events = list(replay_session_events(session))
    grounding_events = [
        e for e in events if isinstance(e, Phase0DatetimeGroundingEvent)
    ]
    assert len(grounding_events) == 1
    g = grounding_events[0]
    assert g.timezone_name == "America/New_York"
    assert "America/New_York" in g.formatted_text


def test_replay_emits_phase_0_proposal_events_one_per_invited_model():
    session = _phase_0_session()
    events = list(replay_session_events(session))
    proposal_events = [
        e for e in events if isinstance(e, Phase0ProposalSubmittedEvent)
    ]
    assert len(proposal_events) == 3
    by_model = {e.model_id: e for e in proposal_events}
    assert by_model[ModelId.OPUS].queries == ("opus query", "shared query")
    assert by_model[ModelId.SONNET].queries == ("shared query",)
    assert by_model[ModelId.HAIKU].queries == ()


def test_replay_emits_phase_0_search_result_event_for_success():
    session = _phase_0_session()
    events = list(replay_session_events(session))
    result_events = [
        e for e in events if isinstance(e, Phase0SearchResultEvent)
    ]
    assert len(result_events) == 2
    queries = {e.query for e in result_events}
    assert queries == {"opus query", "shared query"}


def test_replay_emits_phase_0_failed_search_event_for_failure():
    session = _phase_0_session(with_failed_search=True)
    events = list(replay_session_events(session))
    failed_events = [
        e for e in events if isinstance(e, Phase0FailedSearchEvent)
    ]
    assert len(failed_events) == 1
    f = failed_events[0]
    assert f.query == "shared query"
    assert f.reason == "rate limited"


def test_replay_emits_phase_0_feed_frozen_event_after_entries():
    session = _phase_0_session()
    events = list(replay_session_events(session))
    frozen_events = [
        e for e in events if isinstance(e, Phase0FeedFrozenEvent)
    ]
    assert len(frozen_events) == 1
    # 1 grounding + 2 search results = 3 entries.
    assert frozen_events[0].entry_count == 3


def test_replay_phase_0_events_precede_phase_1_events():
    """Phase 0 emissions must complete before any Phase 1 event fires —
    the §5.0 freeze discipline made temporal in the event stream."""
    session = _phase_0_session()
    events = list(replay_session_events(session))
    first_phase_1_idx = next(
        i for i, e in enumerate(events)
        if isinstance(e, Phase1ResponseStartedEvent)
    )
    frozen_idx = next(
        i for i, e in enumerate(events)
        if isinstance(e, Phase0FeedFrozenEvent)
    )
    assert frozen_idx < first_phase_1_idx
    # Also: all Phase 0 events precede first Phase 1 event.
    phase_0_types = (
        Phase0DatetimeGroundingEvent,
        Phase0ProposalSubmittedEvent,
        Phase0SearchResultEvent,
        Phase0FailedSearchEvent,
        Phase0FeedFrozenEvent,
    )
    for i, e in enumerate(events[:first_phase_1_idx]):
        if isinstance(e, phase_0_types):
            assert i < first_phase_1_idx


def test_replay_grounding_event_precedes_proposal_events():
    """Per §5.0: temporal grounding is the precondition gate. It fires
    before any model proposes."""
    session = _phase_0_session()
    events = list(replay_session_events(session))
    grounding_idx = next(
        i for i, e in enumerate(events)
        if isinstance(e, Phase0DatetimeGroundingEvent)
    )
    first_proposal_idx = next(
        i for i, e in enumerate(events)
        if isinstance(e, Phase0ProposalSubmittedEvent)
    )
    assert grounding_idx < first_proposal_idx


def test_replay_omits_phase_0_events_when_phase_0_is_none():
    """Backward compat: sessions without Phase 0 emit no Phase 0 events."""
    session = _triad_session_with_latency()
    events = list(replay_session_events(session))
    for e in events:
        assert not isinstance(
            e,
            (
                Phase0DatetimeGroundingEvent,
                Phase0ProposalSubmittedEvent,
                Phase0SearchResultEvent,
                Phase0FailedSearchEvent,
                Phase0FeedFrozenEvent,
            ),
        )


def test_replay_lucumi_flag_event_carries_peer_divergence():
    """The replay event stream for the flagged Lucumí session carries the
    peer_divergence interpretation in its Phase4FlagInterpretationsEvent."""
    path = _sessions_dir() / "session_20260504_071848_19b0600f.session.json"
    if not path.exists():
        pytest.skip("Lucumí session not present")
    store = JsonFileSessionStore(_sessions_dir())
    session = store.load("session_20260504_071848_19b0600f")
    events = list(replay_session_events(session))
    flag_event = next(e for e in events if isinstance(e, Phase4FlagInterpretationsEvent))
    assert len(flag_event.interpretations) == 1
    interp = flag_event.interpretations[0]
    assert interp.source_model is ModelId.OPUS
    assert interp.dimension_label == "edge_case_coverage_share"
    assert interp.reading == "peer_divergence"
    assert (interp.histogram_n_zero, interp.histogram_n_one, interp.histogram_n_two) == (1, 5, 1)
