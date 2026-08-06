"""Lattice session orchestration.

This is the layer where the lattice meets the world.

ARCHITECTURAL COMMITMENTS (this module):

  - Async-orchestrator-sync-kernel boundary. The orchestrator is the only
    async layer. The substrate, wire-layer parsers, and synthesis engine
    are all sync pure functions. World-touching concurrency stays here;
    kernel discipline stays pure.

  - Orchestrator is the canonical Session-builder. Userspace surfaces
    (CLI, evaluation harness, integrations) build Sessions through the
    orchestrator, not by hand-assembling phases. This makes the
    orchestrator's session-construction discipline the single point of
    truth for the kernel-userspace boundary.

  - V0 conservative timeout policy. Hard fail on any per-phase timeout.
    No retries, no partial-Phase-1 substrate amendment. Lost work is
    real; v1 trigger condition is real-session timeout frequency that
    makes session-loss unacceptable.

  - Self-reflection latency-gap utilization. Per-model coroutines
    sequence Phase 1 → self-reflection internally. The orchestrator's
    outer gather waits for all three model coroutines; each internal
    coroutine handles its own (Phase 1 emission → reflection emission)
    sequence so the latency gap on faster models gets used productively.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import datetime, timezone
from typing import AbstractSet, Callable, Optional

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
from golden_lattice.exchange.phase_0_investigation import (
    Phase0WireClient,
    SearchClient,
)
from golden_lattice.exchange.phase_1_independent import (
    Phase1WireClient,
    compose_phase_1_with_reflection,
)
from golden_lattice.exchange.phase_2_cross_reading import Phase2WireClient
from golden_lattice.exchange.phase_3_dialogue import Phase3WireClient
from golden_lattice.memory_graph.base import (
    INVESTIGATION_CAP,
    INVESTIGATION_TIMEZONE,
    PARITY_THRESHOLD,
    ModelId,
)
from golden_lattice.memory_graph.metrics import (
    compute_parity_shares,
    interpret_parity_flags,
)
from golden_lattice.memory_graph.phase_0 import (
    DateTimeGrounding,
    FailedSearch,
    FeedEntry,
    InvestigationProposal,
    Phase0Investigation,
    SearchResult,
    datetime_grounding_id,
)
from golden_lattice.memory_graph.schema import (
    Claim,
    CommitmentTransition,
    CrossReading,
    DialogueTurn,
    IndependentResponse,
    Session,
)
from golden_lattice.memory_graph.tagging import Phase2Tagging
from golden_lattice.orchestrator.config import (
    LatticeConfig,
    validate_provider_capabilities,
)
from golden_lattice.orchestrator.errors import OrchestratorTimeoutError
from golden_lattice.synthesis.engine import synthesize

try:
    # Python 3.9+: stdlib zoneinfo for IANA timezone resolution.
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment, misc]


ProgressCallback = Callable[[LatticeEvent], None]


class _NullEmitter:
    """No-op emitter for when progress_callback is None.

    Keeps phase helpers identical between live-streamed and quiet runs.
    The dispatch is a single attribute lookup; behavior is unchanged.
    """

    def emit(self, event: LatticeEvent) -> None:
        return None

    def now_ms(self) -> int:
        return 0


class _Emitter:
    """Live event emitter — anchored at orchestrator entry, fires through the
    provided progress_callback. Each emit point computes its own offset_ms
    relative to that anchor so the event stream matches what
    replay_session_events would produce against this session post-hoc.
    """

    def __init__(self, callback: ProgressCallback, started_wall: datetime) -> None:
        self._callback = callback
        self._started_wall = started_wall

    def emit(self, event: LatticeEvent) -> None:
        self._callback(event)

    def now_ms(self) -> int:
        delta = datetime.now(timezone.utc) - self._started_wall
        return int(delta.total_seconds() * 1000)


def _make_emitter(callback: Optional[ProgressCallback]) -> _Emitter | _NullEmitter:
    if callback is None:
        return _NullEmitter()
    return _Emitter(callback, datetime.now(timezone.utc))


DEFAULT_INVITED_MODELS: tuple[ModelId, ...] = (
    ModelId.FABLE,
    ModelId.OPUS,
    ModelId.SONNET,
    ModelId.HAIKU,
)


def _generate_session_id() -> str:
    """Timestamp-prefixed UUID. Human-readable ordering plus uniqueness."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    rand = uuid.uuid4().hex[:8]
    return f"session_{ts}_{rand}"


def _prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


# Combined client type — runtime-checkable; concrete clients (AnthropicClient,
# StubAnthropicClient) satisfy all three Protocols simultaneously.
LatticeClient = Phase1WireClient  # alias for the principal type; the others are
# duck-typed satisfied by the same instance. We don't union the Protocols here
# because Python's typing.Protocol composition is awkward; runtime isinstance
# against each Protocol works as expected.


async def run_lattice_session_async(
    prompt: str,
    *,
    config: LatticeConfig,
    client,  # satisfies all three wire Protocols (Phase 1/2/3)
    invited_models: tuple[ModelId, ...] = DEFAULT_INVITED_MODELS,
    session_id: Optional[str] = None,
    progress_callback: Optional[ProgressCallback] = None,
    phase_0_client: Optional[Phase0WireClient] = None,
    search_client: Optional[SearchClient] = None,
    available_endpoints: Optional[AbstractSet[str]] = None,
    commitment_transitions: tuple[CommitmentTransition, ...] = (),
) -> Session:
    """Run a complete Lattice session: Phase 0 → 1 → 2 → 3 → 4. Returns Session.

    Async core. Use run_lattice_session() for sync callers.

    Pipeline:
      0. Capability preflight: seat→endpoint mapping coverage, and when
         ``available_endpoints`` is provided, endpoint membership in that
         set. Fails before Phase 0/1 with OrchestratorCapabilityError.
         No network I/O — callers inject the availability set (tests use a
         frozenset; live scripts may populate it from a provider listing).
      1. Phase 1 dispatch in parallel across invited models. Each model's
         coroutine sequences Phase 1 emission → self-reflection internally,
         using the latency gap productively.
      2. Phase 2 dispatch: n*(n-1) cross-readings + n taggings,
         all in parallel.
      3. Phase 3 dispatch: 1 dialogue batch per model, all in parallel.
      4. Phase 4 synthesis: synchronous, single call to synthesize().
      5. Build Session, return.

    Errors propagate. OrchestratorTimeoutError on per-phase timeout.
    OrchestratorProviderError on SDK failures. OrchestratorCapabilityError
    on preflight failure. Substrate ValidationError on malformed responses
    caught at construction. No partial sessions.

    If progress_callback is provided, the orchestrator fires LatticeEvents at
    each phase boundary as they happen — the same event types replay yields
    against a persisted Session. One renderer, two sources.

    Phase 0 (Investigation) runs iff both phase_0_client and search_client
    are provided. Asymmetric configuration (one provided, the other not)
    raises ValueError — the boundary refuses ambiguous configuration rather
    than silently skipping. When Phase 0 is skipped, Session.phase_0 stays
    None and the lattice proceeds with pre-amendment behavior (backward
    compatible with sessions that predate ARCHITECTURE.md §5.0).

    commitment_transitions is an optional explicit ordered history. No
    transition is auto-created because dialogue or claim text changed; the
    caller/observer must supply CommitmentTransition artifacts.
    """
    if (phase_0_client is None) != (search_client is None):
        raise ValueError(
            "phase_0_client and search_client must be provided together "
            "or both omitted. Asymmetric configuration is a user error — "
            "Phase 0 needs both interfaces to run, and the orchestrator "
            "refuses to silently disable Phase 0 if only one is missing."
        )

    # Preflight before any phase work. Seat identity ≠ provider availability.
    validate_provider_capabilities(
        invited_models=invited_models,
        config=config,
        available_endpoints=available_endpoints,
    )

    if session_id is None:
        session_id = _generate_session_id()
    prompt_hash = _prompt_hash(prompt)
    emitter = _make_emitter(progress_callback)

    emitter.emit(SessionStartedEvent(
        timestamp_offset_ms=emitter.now_ms(),
        session_id=session_id,
        prompt=prompt,
        prompt_hash=prompt_hash,
        models_invited=invited_models,
    ))

    # --- Phase 0: Investigation (optional, runs iff both clients provided)
    phase_0_investigation: Optional[Phase0Investigation] = None
    if phase_0_client is not None and search_client is not None:
        phase_0_investigation = await _run_phase_0(
            prompt=prompt,
            phase_0_client=phase_0_client,
            search_client=search_client,
            invited_models=invited_models,
            config=config,
            emitter=emitter,
        )

    # --- Phase 1 + self-reflection (per-model latency-gap utilization) ---
    phase_1_results = await _run_phase_1_with_reflection(
        prompt=prompt,
        prompt_hash=prompt_hash,
        client=client,
        invited_models=invited_models,
        config=config,
        emitter=emitter,
        phase_0_feed=(
            phase_0_investigation.feed if phase_0_investigation else None
        ),
    )

    # --- Phase 2: cross-readings + taggings, all in parallel ----------
    phase_2_cross_readings, phase_2_taggings = await _run_phase_2(
        prompt=prompt,
        client=client,
        phase_1_results=phase_1_results,
        config=config,
        emitter=emitter,
    )

    # All claim_ids that exist by the time Phase 3 runs (Phase 1 + Phase 2 missing).
    all_claim_ids: set[str] = set()
    for response in phase_1_results.values():
        for c in response.claims:
            all_claim_ids.add(c.claim_id)
    for cr in phase_2_cross_readings:
        for c in cr.missing:
            all_claim_ids.add(c.claim_id)

    # --- Phase 3: dialogue from each speaker, all in parallel ---------
    phase_3_turns = await _run_phase_3(
        prompt=prompt,
        client=client,
        phase_1_results=phase_1_results,
        valid_claim_ids=all_claim_ids,
        invited_models=invited_models,
        config=config,
        emitter=emitter,
    )

    # Build the pre-synthesis Session so synthesize() can compose against it.
    # commitment_transitions are validated here (Phase 1 claim refs, order)
    # before any live transition events are emitted.
    pre_synth_session = Session(
        session_id=session_id,
        prompt=prompt,
        prompt_hash=prompt_hash,
        models_invited=invited_models,
        phase_0=phase_0_investigation,
        phase_1=phase_1_results,
        phase_2=phase_2_cross_readings,
        phase_2_taggings=phase_2_taggings,
        phase_3=phase_3_turns,
        commitment_transitions=commitment_transitions,
    )

    # Explicit commitment transitions (Task 5): emit in order after Phase 3
    # and before Phase 4 so live matches replay_session_events ordering.
    for transition in pre_synth_session.commitment_transitions:
        emitter.emit(CommitmentTransitionEvent(
            timestamp_offset_ms=emitter.now_ms(),
            transition=transition,
        ))

    # --- Phase 4: synthesis (sync, atomic) ---------------------------
    artifact = synthesize(
        pre_synth_session,
        mode=config.output_mode,
        confidence_threshold=config.confidence_threshold,
    )
    emitter.emit(Phase4ArtifactEvent(
        timestamp_offset_ms=emitter.now_ms(),
        output_mode=artifact.output_mode,
        synthesis_rules_applied=artifact.synthesis_rules_applied,
        output=artifact.output,
        claim_trace=artifact.claim_trace,
        elevations=artifact.elevations,
        surfaced_disagreements=artifact.surfaced_disagreements,
    ))

    # --- Parity metrics: single source of truth at the canonical builder.
    # Pure sync over the tagged Session (no LLM call). None for dyad
    # sessions per ARCHITECTURE.md §5.3 — parity is undefined at N<3.
    metrics = compute_parity_shares(pre_synth_session, threshold=PARITY_THRESHOLD)
    emitter.emit(Phase4MetricsEvent(
        timestamp_offset_ms=emitter.now_ms(),
        metrics=metrics,
    ))

    # Final Session with phase_4 and metrics set.
    final = Session(
        session_id=pre_synth_session.session_id,
        prompt=pre_synth_session.prompt,
        prompt_hash=pre_synth_session.prompt_hash,
        models_invited=pre_synth_session.models_invited,
        phase_0=pre_synth_session.phase_0,
        phase_1=pre_synth_session.phase_1,
        phase_2=pre_synth_session.phase_2,
        phase_2_taggings=pre_synth_session.phase_2_taggings,
        phase_3=pre_synth_session.phase_3,
        phase_4=artifact,
        metrics=metrics,
        commitment_transitions=pre_synth_session.commitment_transitions,
    )

    emitter.emit(Phase4FlagInterpretationsEvent(
        timestamp_offset_ms=emitter.now_ms(),
        interpretations=interpret_parity_flags(final),
    ))
    emitter.emit(SessionCompletedEvent(
        timestamp_offset_ms=emitter.now_ms(),
        session_id=final.session_id,
    ))

    return final


def run_lattice_session(
    prompt: str,
    *,
    config: LatticeConfig,
    client,
    invited_models: tuple[ModelId, ...] = DEFAULT_INVITED_MODELS,
    session_id: Optional[str] = None,
    progress_callback: Optional[ProgressCallback] = None,
    phase_0_client: Optional[Phase0WireClient] = None,
    search_client: Optional[SearchClient] = None,
    available_endpoints: Optional[AbstractSet[str]] = None,
    commitment_transitions: tuple[CommitmentTransition, ...] = (),
) -> Session:
    """Sync wrapper around run_lattice_session_async. Canonical CLI entry."""
    return asyncio.run(
        run_lattice_session_async(
            prompt,
            config=config,
            client=client,
            invited_models=invited_models,
            session_id=session_id,
            progress_callback=progress_callback,
            phase_0_client=phase_0_client,
            search_client=search_client,
            available_endpoints=available_endpoints,
            commitment_transitions=commitment_transitions,
        )
    )


# ---------------------------------------------------------------------------
# Phase pipeline helpers.
# ---------------------------------------------------------------------------


def _make_datetime_grounding() -> DateTimeGrounding:
    """Deterministic temporal-grounding precondition. Called by the
    orchestrator before any model proposes investigations. No authority
    gradient because no model decides what comes back — same constitutional
    category as §8 prompt re-anchoring.

    Reads current wall time in INVESTIGATION_TIMEZONE (America/New_York,
    per the locked decision). When zoneinfo is unavailable (e.g., minimal
    runtime), falls back to UTC; the formatted_text reflects which zone
    was actually resolved.
    """
    if ZoneInfo is not None:
        try:
            tz = ZoneInfo(INVESTIGATION_TIMEZONE)
            now = datetime.now(tz)
            tz_label = INVESTIGATION_TIMEZONE
        except Exception:  # pragma: no cover
            now = datetime.now(timezone.utc)
            tz_label = "UTC"
    else:  # pragma: no cover
        now = datetime.now(timezone.utc)
        tz_label = "UTC"
    formatted = f"{now.strftime('%Y-%m-%d %H:%M:%S')} ({tz_label})"
    return DateTimeGrounding(
        entry_id=datetime_grounding_id(now, tz_label),
        timestamp=now,
        timezone_name=tz_label,
        formatted_text=formatted,
    )


async def _run_phase_0(
    *,
    prompt: str,
    phase_0_client: Phase0WireClient,
    search_client: SearchClient,
    invited_models: tuple[ModelId, ...],
    config: LatticeConfig,
    emitter: "_Emitter | _NullEmitter",
) -> Phase0Investigation:
    """Phase 0: temporal grounding precondition + collective propose-and-union
    + parallel search execution + freeze.

    Pipeline:
      1. Seed the feed with DateTimeGrounding (deterministic, no model).
      2. Dispatch InvestigationProposal calls to all invited models in
         parallel (no peer visibility — Phase 1 independence pattern).
      3. Union the proposed queries by exact/structural dedup (never
         semantic — that would need an adjudicator and reintroduce the
         authority gradient invariant 1 refuses).
      4. Dispatch search executions for each deduplicated query in
         parallel. SearchResult on success, FailedSearch on failure — the
         search client converts internally.
      5. Assemble the frozen Phase0Investigation.
    """
    # Step 1: deterministic temporal grounding.
    grounding = _make_datetime_grounding()
    emitter.emit(Phase0DatetimeGroundingEvent(
        timestamp_offset_ms=emitter.now_ms(),
        entry_id=grounding.entry_id,
        timezone_name=grounding.timezone_name,
        formatted_text=grounding.formatted_text,
    ))

    # Step 2: collect proposals from all invited models in parallel.
    async def _one_proposal(model: ModelId) -> InvestigationProposal:
        proposal = await phase_0_client.submit_investigation_proposal(
            model_id=model,
            original_prompt=prompt,
            max_queries=INVESTIGATION_CAP,
        )
        emitter.emit(Phase0ProposalSubmittedEvent(
            timestamp_offset_ms=emitter.now_ms(),
            model_id=proposal.model_id,
            queries=proposal.queries,
        ))
        return proposal

    proposal_coros = [_one_proposal(m) for m in invited_models]
    proposals = tuple(await asyncio.gather(*proposal_coros))

    # Step 3: rule-based exact union of queries. Preserve first-seen order
    # so the feed has a deterministic deduplicated sequence.
    union_queries: list[str] = []
    seen: set[str] = set()
    for p in proposals:
        for q in p.queries:
            if q not in seen:
                seen.add(q)
                union_queries.append(q)

    # Step 4: dispatch deduplicated searches in parallel.
    async def _one_search(query: str):
        result = await search_client.execute_search(query)
        if isinstance(result, SearchResult):
            emitter.emit(Phase0SearchResultEvent(
                timestamp_offset_ms=emitter.now_ms(),
                entry_id=result.entry_id,
                query=result.query,
                result_text_preview=result.result_text[:200],
                source_urls=result.source_urls,
            ))
        elif isinstance(result, FailedSearch):
            emitter.emit(Phase0FailedSearchEvent(
                timestamp_offset_ms=emitter.now_ms(),
                entry_id=result.entry_id,
                query=result.query,
                reason=result.reason,
            ))
        return result

    search_coros = [_one_search(q) for q in union_queries]
    search_results = await asyncio.gather(*search_coros) if search_coros else []

    # Step 5: assemble Phase 0 artifact and emit the freeze event.
    feed: tuple[FeedEntry, ...] = (grounding, *search_results)
    investigation = Phase0Investigation(proposals=proposals, feed=feed)
    emitter.emit(Phase0FeedFrozenEvent(
        timestamp_offset_ms=emitter.now_ms(),
        entry_count=len(feed),
    ))
    return investigation


async def _run_phase_1_with_reflection(
    *,
    prompt: str,
    prompt_hash: str,
    client,
    invited_models: tuple[ModelId, ...],
    config: LatticeConfig,
    emitter: "_Emitter | _NullEmitter",
    phase_0_feed: Optional[tuple[FeedEntry, ...]] = None,
) -> dict[ModelId, IndependentResponse]:
    """Per-model coroutines sequence Phase 1 → self-reflection. Outer gather
    awaits all three. Latency-gap utilization happens inside each coroutine.

    phase_0_feed (when present) is threaded into each model's submit_phase_1_response
    call so the model's Phase 1 generation sees the §5.0 shared evidence
    feed. Each peer receives the same feed — symmetric visibility on the
    investigation layer (per invariant 2)."""

    async def _one_model(model: ModelId) -> tuple[ModelId, IndependentResponse]:
        emitter.emit(Phase1ResponseStartedEvent(
            timestamp_offset_ms=emitter.now_ms(),
            model_id=model,
        ))
        # Phase 1 with timeout.
        try:
            draft = await asyncio.wait_for(
                client.submit_phase_1_response(
                    model_id=model,
                    original_prompt=prompt,
                    prompt_hash=prompt_hash,
                    feed=phase_0_feed,
                ),
                timeout=config.timeout_phase_1_seconds,
            )
        except asyncio.TimeoutError:
            raise OrchestratorTimeoutError(
                model=model,
                phase="phase_1",
                timeout_seconds=config.timeout_phase_1_seconds,
            )

        # Phase 1 arrived — emit one claim event per claim, then completed.
        completed_ms = emitter.now_ms()
        for claim in draft.claims:
            emitter.emit(Phase1ClaimEvent(
                timestamp_offset_ms=completed_ms,
                model_id=model,
                claim_id=claim.claim_id,
                text=claim.text,
            ))
        emitter.emit(Phase1ResponseCompletedEvent(
            timestamp_offset_ms=completed_ms,
            model_id=model,
            focus_tag=draft.focus_tag,
            confidence=draft.confidence,
            claim_count=len(draft.claims),
        ))

        # Self-reflection with separate timeout.
        try:
            reflection = await asyncio.wait_for(
                client.submit_self_reflection(
                    model_id=model,
                    phase_1_response=draft,
                ),
                timeout=config.timeout_self_reflection_seconds,
            )
        except asyncio.TimeoutError:
            raise OrchestratorTimeoutError(
                model=model,
                phase="self_reflection",
                timeout_seconds=config.timeout_self_reflection_seconds,
            )

        emitter.emit(SelfReflectionEvent(
            timestamp_offset_ms=emitter.now_ms(),
            model_id=model,
            strongest_claim_id=reflection.strongest_claim_id,
            weakest_claim_id=reflection.weakest_claim_id,
            tag_justification=reflection.tag_justification,
        ))

        folded = compose_phase_1_with_reflection(draft, reflection)
        return model, folded

    coros = [_one_model(m) for m in invited_models]
    completed = await asyncio.gather(*coros)
    return dict(completed)


async def _run_phase_2(
    *,
    prompt: str,
    client,
    phase_1_results: dict[ModelId, IndependentResponse],
    config: LatticeConfig,
    emitter: "_Emitter | _NullEmitter",
) -> tuple[tuple[CrossReading, ...], tuple[Phase2Tagging, ...]]:
    """Phase 2: n*(n-1) cross-readings + n taggings, all in parallel."""

    invited_models = tuple(phase_1_results.keys())
    valid_claim_ids = {
        c.claim_id for r in phase_1_results.values() for c in r.claims
    }

    async def _one_cross_reading(reader: ModelId, target: ModelId) -> CrossReading:
        target_response = phase_1_results[target]
        target_reflection = (
            target_response.self_reflection_artifacts[0].tag_justification
            if target_response.self_reflection_artifacts
            else None
        )
        try:
            result = await asyncio.wait_for(
                client.submit_cross_reading(
                    reader_model=reader,
                    target_model=target,
                    original_prompt=prompt,
                    target_response=target_response.response,
                    target_claims=target_response.claims,
                    target_self_reflection=target_reflection,
                    valid_claim_ids=valid_claim_ids,
                ),
                timeout=config.timeout_phase_2_seconds,
            )
        except asyncio.TimeoutError:
            raise OrchestratorTimeoutError(
                model=reader,
                phase=f"phase_2_cross_reading_target_{target.value}",
                timeout_seconds=config.timeout_phase_2_seconds,
            )
        emitter.emit(Phase2CrossReadingEvent(
            timestamp_offset_ms=emitter.now_ms(),
            reader_model=result.reader_model,
            target_model=result.target_model,
            agreements_count=len(result.agreements),
            disagreements_count=len(result.disagreements),
            missing_count=len(result.missing),
        ))
        return result

    async def _one_tagging(tagger: ModelId) -> Phase2Tagging:
        own_claims = phase_1_results[tagger].claims
        peer_claims: list[Claim] = []
        for other_model, other_response in phase_1_results.items():
            if other_model is tagger:
                continue
            peer_claims.extend(other_response.claims)
        try:
            result = await asyncio.wait_for(
                client.submit_phase_2_tagging(
                    tagger_model=tagger,
                    original_prompt=prompt,
                    own_claims=own_claims,
                    peer_claims=tuple(peer_claims),
                    valid_claim_ids=valid_claim_ids,
                ),
                timeout=config.timeout_phase_2_seconds,
            )
        except asyncio.TimeoutError:
            raise OrchestratorTimeoutError(
                model=tagger,
                phase="phase_2_tagging",
                timeout_seconds=config.timeout_phase_2_seconds,
            )
        emitter.emit(Phase2TaggingEvent(
            timestamp_offset_ms=emitter.now_ms(),
            tagger_model=result.tagger_model,
            peer_tags_count=len(result.peer_tags),
            self_tags_count=len(result.self_tags),
        ))
        return result

    cross_reading_coros = [
        _one_cross_reading(reader, target)
        for reader in invited_models
        for target in invited_models
        if reader is not target
    ]
    tagging_coros = [_one_tagging(tagger) for tagger in invited_models]

    cross_readings, taggings = await asyncio.gather(
        asyncio.gather(*cross_reading_coros),
        asyncio.gather(*tagging_coros),
    )
    return tuple(cross_readings), tuple(taggings)


async def _run_phase_3(
    *,
    prompt: str,
    client,
    phase_1_results: dict[ModelId, IndependentResponse],
    valid_claim_ids: set[str],
    invited_models: tuple[ModelId, ...],
    config: LatticeConfig,
    emitter: "_Emitter | _NullEmitter",
) -> tuple[DialogueTurn, ...]:
    """Phase 3: each speaker produces a dialogue batch. All in parallel."""

    async def _one_speaker(speaker: ModelId) -> tuple[DialogueTurn, ...]:
        own_response = phase_1_results[speaker].response
        peer_blocks = tuple(
            (m, r.response, r.claims)
            for m, r in phase_1_results.items()
            if m is not speaker
        )
        try:
            turns = await asyncio.wait_for(
                client.submit_phase_3_dialogue(
                    speaker_model=speaker,
                    original_prompt=prompt,
                    own_phase_1_response=own_response,
                    peer_phase_1_blocks=peer_blocks,
                    valid_claim_ids=valid_claim_ids,
                ),
                timeout=config.timeout_phase_3_seconds,
            )
        except asyncio.TimeoutError:
            raise OrchestratorTimeoutError(
                model=speaker,
                phase="phase_3",
                timeout_seconds=config.timeout_phase_3_seconds,
            )

        for turn in turns:
            emitter.emit(Phase3TurnEvent(
                timestamp_offset_ms=emitter.now_ms(),
                turn_id=turn.turn_id,
                speaker_model=turn.speaker_model,
                channel=turn.channel,
                target_model=turn.target_model,
                target_claim_ids=turn.target_claim_ids,
                content=turn.content,
            ))
        return turns

    coros = [_one_speaker(m) for m in invited_models]
    per_speaker_turns = await asyncio.gather(*coros)
    flattened: list[DialogueTurn] = []
    for turns in per_speaker_turns:
        flattened.extend(turns)
    return tuple(flattened)
