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
from typing import Optional

from golden_lattice.exchange.phase_1_independent import (
    Phase1WireClient,
    compose_phase_1_with_reflection,
)
from golden_lattice.exchange.phase_2_cross_reading import Phase2WireClient
from golden_lattice.exchange.phase_3_dialogue import Phase3WireClient
from golden_lattice.memory_graph.base import ModelId
from golden_lattice.memory_graph.schema import (
    Claim,
    CrossReading,
    DialogueTurn,
    IndependentResponse,
    Session,
)
from golden_lattice.memory_graph.tagging import Phase2Tagging
from golden_lattice.orchestrator.config import LatticeConfig
from golden_lattice.orchestrator.errors import OrchestratorTimeoutError
from golden_lattice.synthesis.engine import synthesize


DEFAULT_INVITED_MODELS: tuple[ModelId, ...] = (
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
    client,  # satisfies all three wire Protocols
    invited_models: tuple[ModelId, ...] = DEFAULT_INVITED_MODELS,
    session_id: Optional[str] = None,
) -> Session:
    """Run a complete Lattice session: Phase 1 → 2 → 3 → 4. Returns Session.

    Async core. Use run_lattice_session() for sync callers.

    Pipeline:
      1. Phase 1 dispatch in parallel across invited models. Each model's
         coroutine sequences Phase 1 emission → self-reflection internally,
         using the latency gap productively.
      2. Phase 2 dispatch: 6 cross-readings (n*(n-1) for n=3) + 3 taggings,
         all in parallel.
      3. Phase 3 dispatch: 1 dialogue batch per model, all in parallel.
      4. Phase 4 synthesis: synchronous, single call to synthesize().
      5. Build Session, return.

    Errors propagate. OrchestratorTimeoutError on per-phase timeout.
    OrchestratorProviderError on SDK failures. Substrate ValidationError
    on malformed responses caught at construction. No partial sessions.
    """
    if session_id is None:
        session_id = _generate_session_id()
    prompt_hash = _prompt_hash(prompt)

    # --- Phase 1 + self-reflection (per-model latency-gap utilization) ---
    phase_1_results = await _run_phase_1_with_reflection(
        prompt=prompt,
        prompt_hash=prompt_hash,
        client=client,
        invited_models=invited_models,
        config=config,
    )

    # --- Phase 2: cross-readings + taggings, all in parallel ----------
    phase_2_cross_readings, phase_2_taggings = await _run_phase_2(
        prompt=prompt,
        client=client,
        phase_1_results=phase_1_results,
        config=config,
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
    )

    # Build the pre-synthesis Session so synthesize() can compose against it.
    pre_synth_session = Session(
        session_id=session_id,
        prompt=prompt,
        prompt_hash=prompt_hash,
        models_invited=invited_models,
        phase_1=phase_1_results,
        phase_2=phase_2_cross_readings,
        phase_2_taggings=phase_2_taggings,
        phase_3=phase_3_turns,
    )

    # --- Phase 4: synthesis (sync, atomic) ---------------------------
    artifact = synthesize(
        pre_synth_session,
        mode=config.output_mode,
        confidence_threshold=config.confidence_threshold,
    )

    # Final Session with phase_4 set.
    return Session(
        session_id=pre_synth_session.session_id,
        prompt=pre_synth_session.prompt,
        prompt_hash=pre_synth_session.prompt_hash,
        models_invited=pre_synth_session.models_invited,
        phase_1=pre_synth_session.phase_1,
        phase_2=pre_synth_session.phase_2,
        phase_2_taggings=pre_synth_session.phase_2_taggings,
        phase_3=pre_synth_session.phase_3,
        phase_4=artifact,
    )


def run_lattice_session(
    prompt: str,
    *,
    config: LatticeConfig,
    client,
    invited_models: tuple[ModelId, ...] = DEFAULT_INVITED_MODELS,
    session_id: Optional[str] = None,
) -> Session:
    """Sync wrapper around run_lattice_session_async. Canonical CLI entry."""
    return asyncio.run(
        run_lattice_session_async(
            prompt,
            config=config,
            client=client,
            invited_models=invited_models,
            session_id=session_id,
        )
    )


# ---------------------------------------------------------------------------
# Phase pipeline helpers.
# ---------------------------------------------------------------------------


async def _run_phase_1_with_reflection(
    *,
    prompt: str,
    prompt_hash: str,
    client,
    invited_models: tuple[ModelId, ...],
    config: LatticeConfig,
) -> dict[ModelId, IndependentResponse]:
    """Per-model coroutines sequence Phase 1 → self-reflection. Outer gather
    awaits all three. Latency-gap utilization happens inside each coroutine."""

    async def _one_model(model: ModelId) -> tuple[ModelId, IndependentResponse]:
        # Phase 1 with timeout.
        try:
            draft = await asyncio.wait_for(
                client.submit_phase_1_response(
                    model_id=model,
                    original_prompt=prompt,
                    prompt_hash=prompt_hash,
                ),
                timeout=config.timeout_phase_1_seconds,
            )
        except asyncio.TimeoutError:
            raise OrchestratorTimeoutError(
                model=model,
                phase="phase_1",
                timeout_seconds=config.timeout_phase_1_seconds,
            )

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
            return await asyncio.wait_for(
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

    async def _one_tagging(tagger: ModelId) -> Phase2Tagging:
        own_claims = phase_1_results[tagger].claims
        peer_claims: list[Claim] = []
        for other_model, other_response in phase_1_results.items():
            if other_model is tagger:
                continue
            peer_claims.extend(other_response.claims)
        try:
            return await asyncio.wait_for(
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
            return await asyncio.wait_for(
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

    coros = [_one_speaker(m) for m in invited_models]
    per_speaker_turns = await asyncio.gather(*coros)
    flattened: list[DialogueTurn] = []
    for turns in per_speaker_turns:
        flattened.extend(turns)
    return tuple(flattened)
