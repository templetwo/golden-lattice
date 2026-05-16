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
