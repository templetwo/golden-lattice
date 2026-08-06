"""Walk a persisted Session and yield LatticeEvents in natural temporal order.

Replay is not a separate code path. It is the same event protocol the live
orchestrator will fire, sourced from a Session already on disk. Build the
renderer against replay, validate it against the four persisted sessions, and
the live wire is the last connection — same events, different origin.

Timing model:
  - Session start anchored at the earliest Phase 1 generation_started_at.
  - Phase 1 response started events at each model's generation_started_at.
  - Phase 1 claim and completed events at each model's generation_completed_at.
    Claims share the completion timestamp; the live stream emits the response
    body and the renderer parses claims out of it, so the event-stream view of
    a Phase 1 response is atomic at completion.
  - Self-reflection events at the artifact's generated_at.
  - Phase 2 cross-reading and tagging events have no per-event persisted
    timestamps; replay assigns monotonically increasing synthetic offsets
    after Phase 1 self-reflection completes.
  - Phase 3 turn events likewise get synthetic monotonic offsets.
  - Phase 4 events (artifact, metrics, flag_interpretations) get the final
    three offsets in sequence; session_completed terminates.

Metrics and flag interpretations are computed during replay (the persisted
Sessions to date have metrics=None due to the parity-wiring gap that this
project just closed). Future Sessions emitted by the patched orchestrator
will carry metrics already; the replay emitter recomputes idempotently and
uses the persisted value when present.
"""

from __future__ import annotations

from typing import Iterator

from golden_lattice.events import (
    CommitmentTransitionEvent,
    LatticeEvent,
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
    Phase3TurnEvent,
    Phase4ArtifactEvent,
    Phase4FlagInterpretationsEvent,
    Phase4MetricsEvent,
    SelfReflectionEvent,
    SessionCompletedEvent,
    SessionStartedEvent,
)
from golden_lattice.memory_graph.metrics import (
    compute_parity_shares,
    interpret_parity_flags,
)
from golden_lattice.memory_graph.phase_0 import (
    DateTimeGrounding,
    FailedSearch,
    SearchResult,
)
from golden_lattice.memory_graph.schema import Session


_SYNTHETIC_GAP_MS = 250


def replay_session_events(session: Session) -> Iterator[LatticeEvent]:
    """Yield LatticeEvents reconstructed from a persisted Session in order.

    Order: session_started → (per-model phase_1_response_started in start-time
    order) → (per-model in completion-time order: phase_1_claim × N,
    phase_1_response_completed, self_reflection) → phase_2_cross_reading × n*(n-1)
    → phase_2_tagging × n → phase_3_turn × |phase_3| →
    commitment_transition × |commitment_transitions| → phase_4_artifact →
    phase_4_metrics → phase_4_flag_interpretations → session_completed.
    """
    phase_1 = session.phase_1
    if not phase_1:
        yield SessionStartedEvent(
            timestamp_offset_ms=0,
            session_id=session.session_id,
            prompt=session.prompt,
            prompt_hash=session.prompt_hash,
            models_invited=session.models_invited,
        )
        yield SessionCompletedEvent(timestamp_offset_ms=0, session_id=session.session_id)
        return

    session_start = min(r.generation_started_at for r in phase_1.values())

    def _offset_ms(when) -> int:
        delta = when - session_start
        return int(delta.total_seconds() * 1000)

    yield SessionStartedEvent(
        timestamp_offset_ms=0,
        session_id=session.session_id,
        prompt=session.prompt,
        prompt_hash=session.prompt_hash,
        models_invited=session.models_invited,
    )

    # Phase 0 events (replay) — emitted iff session.phase_0 is not None.
    # Emission order: grounding → proposals → search entries → frozen.
    # All Phase 0 events use synthetic offsets just after session start so
    # they precede Phase 1 events in the rendered stream.
    if session.phase_0 is not None:
        phase_0_offset = 0
        for entry in session.phase_0.feed:
            if isinstance(entry, DateTimeGrounding):
                yield Phase0DatetimeGroundingEvent(
                    timestamp_offset_ms=phase_0_offset,
                    entry_id=entry.entry_id,
                    timezone_name=entry.timezone_name,
                    formatted_text=entry.formatted_text,
                )
                phase_0_offset += _SYNTHETIC_GAP_MS
                break
        for proposal in session.phase_0.proposals:
            yield Phase0ProposalSubmittedEvent(
                timestamp_offset_ms=phase_0_offset,
                model_id=proposal.model_id,
                queries=proposal.queries,
            )
            phase_0_offset += _SYNTHETIC_GAP_MS
        for entry in session.phase_0.feed:
            if isinstance(entry, DateTimeGrounding):
                continue
            if isinstance(entry, SearchResult):
                preview = entry.result_text[:200]
                yield Phase0SearchResultEvent(
                    timestamp_offset_ms=phase_0_offset,
                    entry_id=entry.entry_id,
                    query=entry.query,
                    result_text_preview=preview,
                    source_urls=entry.source_urls,
                )
            elif isinstance(entry, FailedSearch):
                yield Phase0FailedSearchEvent(
                    timestamp_offset_ms=phase_0_offset,
                    entry_id=entry.entry_id,
                    query=entry.query,
                    reason=entry.reason,
                )
            phase_0_offset += _SYNTHETIC_GAP_MS
        yield Phase0FeedFrozenEvent(
            timestamp_offset_ms=phase_0_offset,
            entry_count=len(session.phase_0.feed),
        )

    # Per-model phase_1_response_started, ordered by generation_started_at.
    started_order = sorted(
        phase_1.items(),
        key=lambda kv: kv[1].generation_started_at,
    )
    for model_id, response in started_order:
        yield Phase1ResponseStartedEvent(
            timestamp_offset_ms=_offset_ms(response.generation_started_at),
            model_id=model_id,
        )

    # Per-model completion + claims + self-reflection, ordered by completion.
    completed_order = sorted(
        phase_1.items(),
        key=lambda kv: kv[1].generation_completed_at,
    )
    max_offset_so_far = 0
    for model_id, response in completed_order:
        completed_ms = _offset_ms(response.generation_completed_at)
        for claim in response.claims:
            yield Phase1ClaimEvent(
                timestamp_offset_ms=completed_ms,
                model_id=model_id,
                claim_id=claim.claim_id,
                text=claim.text,
            )
        yield Phase1ResponseCompletedEvent(
            timestamp_offset_ms=completed_ms,
            model_id=model_id,
            focus_tag=response.focus_tag,
            confidence=response.confidence,
            claim_count=len(response.claims),
        )
        max_offset_so_far = max(max_offset_so_far, completed_ms)

        for artifact in response.self_reflection_artifacts:
            reflection_ms = _offset_ms(artifact.generated_at)
            yield SelfReflectionEvent(
                timestamp_offset_ms=reflection_ms,
                model_id=model_id,
                strongest_claim_id=artifact.strongest_claim_id,
                weakest_claim_id=artifact.weakest_claim_id,
                tag_justification=artifact.tag_justification,
            )
            max_offset_so_far = max(max_offset_so_far, reflection_ms)

    # Phase 2: synthetic monotonic offsets after the last Phase 1 / reflection.
    offset = max_offset_so_far + _SYNTHETIC_GAP_MS
    for cr in session.phase_2:
        yield Phase2CrossReadingEvent(
            timestamp_offset_ms=offset,
            reader_model=cr.reader_model,
            target_model=cr.target_model,
            agreements_count=len(cr.agreements),
            disagreements_count=len(cr.disagreements),
            missing_count=len(cr.missing),
        )
        offset += _SYNTHETIC_GAP_MS

    for tagging in session.phase_2_taggings:
        yield Phase2TaggingEvent(
            timestamp_offset_ms=offset,
            tagger_model=tagging.tagger_model,
            peer_tags_count=len(tagging.peer_tags),
            self_tags_count=len(tagging.self_tags),
        )
        offset += _SYNTHETIC_GAP_MS

    # Phase 3.
    for turn in session.phase_3:
        yield Phase3TurnEvent(
            timestamp_offset_ms=offset,
            turn_id=turn.turn_id,
            speaker_model=turn.speaker_model,
            channel=turn.channel,
            target_model=turn.target_model,
            target_claim_ids=turn.target_claim_ids,
            content=turn.content,
        )
        offset += _SYNTHETIC_GAP_MS

    # Explicit commitment transitions (Phase 1 Task 5). Emitted in session
    # tuple order after Phase 3 and before Phase 4. Never inferred from text.
    for transition in session.commitment_transitions:
        yield CommitmentTransitionEvent(
            timestamp_offset_ms=offset,
            transition=transition,
        )
        offset += _SYNTHETIC_GAP_MS

    # Phase 4: artifact, metrics, flag interpretations.
    artifact = session.phase_4
    if artifact is not None:
        yield Phase4ArtifactEvent(
            timestamp_offset_ms=offset,
            output_mode=artifact.output_mode,
            synthesis_rules_applied=artifact.synthesis_rules_applied,
            output=artifact.output,
            claim_trace=artifact.claim_trace,
            elevations=artifact.elevations,
            surfaced_disagreements=artifact.surfaced_disagreements,
        )
        offset += _SYNTHETIC_GAP_MS

    # Use persisted metrics if present; otherwise compute. Either way emit.
    metrics = session.metrics if session.metrics is not None else compute_parity_shares(session)
    yield Phase4MetricsEvent(
        timestamp_offset_ms=offset,
        metrics=metrics,
    )
    offset += _SYNTHETIC_GAP_MS

    # Flag interpretations need a Session whose metrics field is populated.
    session_for_interp = (
        session if session.metrics is not None
        else session.model_copy(update={"metrics": metrics})
    )
    yield Phase4FlagInterpretationsEvent(
        timestamp_offset_ms=offset,
        interpretations=interpret_parity_flags(session_for_interp),
    )
    offset += _SYNTHETIC_GAP_MS

    yield SessionCompletedEvent(
        timestamp_offset_ms=offset,
        session_id=session.session_id,
    )
