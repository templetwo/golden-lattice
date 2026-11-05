"""Deterministic fixture Session for the output-mode golden files (M3).

Hand-constructed triadic session. No randomness and no clock reads: every
datetime is the fixed NOW below, and Session.created_at is passed explicitly
(its default reads the clock).

What it covers, and how each case arises from the real engine:
  - present:  claims no rule touches.
  - modified: o_ttl is disagreed with by BOTH non-author peers in Phase 2,
              so build_claim_trace gives it a real [DISPUTED — ...] hedge.
  - omitted:  s_size and h_compress are their author's self-flagged weakest
              claim and no peer cross-reading engages with them, so
              build_claim_trace omits them as low_confidence_isolated.
  - Elevation: two converge turns from distinct speakers on o_lru.
  - SurfacedDisagreement: an Opus critique naming two Sonnet claims, both
              sides at Phase 1 confidence >= 0.8.
  - Phase 2: six cross-readings, one carrying a Phase 2 "missing" claim.
  - Phase 3: converge, critique and augment turns.

The persisted session (golden/session.json) carries the Phase 4 artifact in
TRANSCRIPT mode: its output is the transcript rendering, which M3 does not
change, so the persisted artifact does not depend on the renderers M3 edits.

Regenerate after a deliberate rendering change (then review the diff):

    .venv/bin/python -m tests.synthesis.golden_session
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from golden_lattice.memory_graph.base import (
    FocusTag,
    ModelId,
    OutputMode,
    Phase,
    SynthesisRule,
    claim_id_for,
)
from golden_lattice.memory_graph.schema import (
    Claim,
    ClaimRef,
    CrossReading,
    DialogueTurn,
    Disagreement,
    IndependentResponse,
    SelfReflectionArtifact,
    Session,
    SynthesisArtifact,
)
from golden_lattice.synthesis.attribution import render_output
from golden_lattice.synthesis.claim_trace import build_claim_trace
from golden_lattice.synthesis.disagreement import compute_surfaced_disagreements
from golden_lattice.synthesis.elevation import compute_elevations


GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
SESSION_PATH = GOLDEN_DIR / "session.json"
SURFACING_THRESHOLD = 0.8

NOW = datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)
PROMPT = "Design a cache for a read-heavy multi-tenant API."
PROMPT_HASH = "golden-m3-prompt-hash"


def golden_path(mode: OutputMode) -> Path:
    return GOLDEN_DIR / f"{mode.value}.txt"


def _claim(model: ModelId, text: str, phase: Phase = Phase.INDEPENDENT) -> Claim:
    return Claim(
        claim_id=claim_id_for(model, phase, text),
        source_model=model,
        source_phase=phase,
        text=text,
    )


# Phase 1 claims, in emission order per model.
O_LRU = _claim(ModelId.OPUS, "LRU is the right default eviction policy for a read-heavy cache.")
O_WRITE = _claim(ModelId.OPUS, "Write-through keeps the cache and the store consistent at the cost of write latency.")
O_TTL = _claim(ModelId.OPUS, "A TTL of five minutes is safe for every key.")

S_MEASURE = _claim(ModelId.SONNET, "Measure the hit rate before choosing an eviction policy.")
S_STAMPEDE = _claim(ModelId.SONNET, "Cache stampedes need request coalescing on a miss.")
S_SIZE = _claim(ModelId.SONNET, "Size the cache from the working set, not from available memory.")

H_NEGATIVE = _claim(ModelId.HAIKU, "Negative lookups should be cached with a short TTL.")
H_TENANT = _claim(ModelId.HAIKU, "Every cache key must include the tenant id.")
H_COMPRESS = _claim(ModelId.HAIKU, "Compression might help with large values.")

# Phase 2 "missing" claim surfaced by Haiku reading Opus.
H_REVOKE = _claim(
    ModelId.HAIKU,
    "Revoking a token must evict it from the cache immediately.",
    phase=Phase.CROSS_READING,
)


def _response(
    model: ModelId,
    claims: tuple[Claim, ...],
    *,
    confidence: float,
    strongest: Claim,
    weakest: Claim,
    justification: str,
) -> IndependentResponse:
    return IndependentResponse(
        model_id=model,
        prompt_hash=PROMPT_HASH,
        response=f"{model.value} answers the cache design prompt.",
        focus_tag=FocusTag.CORRECTNESS,
        confidence=confidence,
        claims=claims,
        self_reflection_artifacts=(
            SelfReflectionArtifact(
                model_id=model,
                generated_at=NOW,
                strongest_claim_id=strongest.claim_id,
                weakest_claim_id=weakest.claim_id,
                tag_justification=justification,
            ),
        ),
        generation_started_at=NOW,
        generation_completed_at=NOW,
    )


def build_base_session() -> Session:
    """Phases 1-3, no Phase 4. Fully deterministic."""
    phase_1 = {
        ModelId.OPUS: _response(
            ModelId.OPUS,
            (O_LRU, O_WRITE, O_TTL),
            confidence=0.9,
            strongest=O_LRU,
            weakest=O_TTL,
            justification="The answer turns on correctness of eviction and expiry.",
        ),
        ModelId.SONNET: _response(
            ModelId.SONNET,
            (S_MEASURE, S_STAMPEDE, S_SIZE),
            confidence=0.85,
            strongest=S_STAMPEDE,
            weakest=S_SIZE,
            justification="Failure under load is a correctness question.",
        ),
        ModelId.HAIKU: _response(
            ModelId.HAIKU,
            (H_NEGATIVE, H_TENANT, H_COMPRESS),
            confidence=0.6,
            strongest=H_TENANT,
            weakest=H_COMPRESS,
            justification="Tenant isolation is the correctness floor.",
        ),
    }
    phase_2 = (
        CrossReading(
            reader_model=ModelId.SONNET,
            target_model=ModelId.OPUS,
            agreements=(ClaimRef(claim_id=O_LRU.claim_id),),
            disagreements=(
                Disagreement(
                    target_claim_id=O_TTL.claim_id,
                    reason="Hot keys and cold keys need different TTLs. One value hides stale reads.",
                ),
            ),
        ),
        CrossReading(
            reader_model=ModelId.HAIKU,
            target_model=ModelId.OPUS,
            disagreements=(
                Disagreement(
                    target_claim_id=O_TTL.claim_id,
                    reason="A revoked session token must not live five more minutes. Expiry is not revocation.",
                ),
            ),
            missing=(H_REVOKE,),
        ),
        CrossReading(
            reader_model=ModelId.OPUS,
            target_model=ModelId.SONNET,
            agreements=(
                ClaimRef(claim_id=S_MEASURE.claim_id),
                ClaimRef(claim_id=S_STAMPEDE.claim_id),
            ),
        ),
        CrossReading(
            reader_model=ModelId.HAIKU,
            target_model=ModelId.SONNET,
            agreements=(ClaimRef(claim_id=S_STAMPEDE.claim_id),),
        ),
        CrossReading(
            reader_model=ModelId.OPUS,
            target_model=ModelId.HAIKU,
            agreements=(ClaimRef(claim_id=H_TENANT.claim_id),),
        ),
        CrossReading(
            reader_model=ModelId.SONNET,
            target_model=ModelId.HAIKU,
            agreements=(ClaimRef(claim_id=H_NEGATIVE.claim_id),),
        ),
    )
    phase_3 = (
        DialogueTurn(
            turn_id="t1",
            speaker_model=ModelId.SONNET,
            channel="converge",
            target_model=ModelId.OPUS,
            target_claim_ids=(O_LRU.claim_id,),
            content="Agreed: LRU fits a read-heavy profile.",
        ),
        DialogueTurn(
            turn_id="t2",
            speaker_model=ModelId.HAIKU,
            channel="converge",
            target_model=ModelId.OPUS,
            target_claim_ids=(O_LRU.claim_id,),
            content="LRU is the right starting point here.",
        ),
        DialogueTurn(
            turn_id="t3",
            speaker_model=ModelId.OPUS,
            channel="critique",
            target_model=ModelId.SONNET,
            target_claim_ids=(S_MEASURE.claim_id, S_STAMPEDE.claim_id),
            content="Both assume a single region; multi-region changes the hit rate and the coalescing scope.",
        ),
        DialogueTurn(
            turn_id="t4",
            speaker_model=ModelId.HAIKU,
            channel="augment",
            content="Watch allocator fragmentation when many values are small.",
        ),
    )
    return Session(
        session_id="golden-m3-output-modes",
        prompt=PROMPT,
        prompt_hash=PROMPT_HASH,
        created_at=NOW,
        models_invited=(ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU),
        phase_1=phase_1,
        phase_2=phase_2,
        phase_3=phase_3,
    )


def build_persisted_session() -> Session:
    """The base session plus a Phase 4 artifact (transcript mode) computed by
    the real synthesis rules."""
    base = build_base_session()
    trace = build_claim_trace(base)
    elevations = compute_elevations(base)
    surfaced = compute_surfaced_disagreements(
        base, confidence_threshold=SURFACING_THRESHOLD
    )
    output = render_output(
        base,
        mode=OutputMode.TRANSCRIPT,
        claim_trace=trace,
        elevations=elevations,
        surfaced_disagreements=surfaced,
    )
    artifact = SynthesisArtifact(
        output=output,
        output_mode=OutputMode.TRANSCRIPT,
        claim_trace=trace,
        synthesis_rules_applied=(
            SynthesisRule.IRREDUCIBILITY_PRESERVATION,
            SynthesisRule.AGREEMENT_ELEVATION,
            SynthesisRule.DISAGREEMENT_SURFACING,
            SynthesisRule.ATTRIBUTION_PRESERVATION,
        ),
        elevations=elevations,
        surfaced_disagreements=surfaced,
    )
    return base.model_copy(update={"phase_4": artifact})


def load_persisted_session() -> Session:
    return Session.model_validate_json(SESSION_PATH.read_text(encoding="utf-8"))


def render_from_persisted(session: Session, mode: OutputMode) -> str:
    """Render one mode from a persisted session's own Phase 4 artifact."""
    assert session.phase_4 is not None
    return render_output(
        session.model_copy(update={"phase_4": None}),
        mode=mode,
        claim_trace=session.phase_4.claim_trace,
        elevations=session.phase_4.elevations,
        surfaced_disagreements=session.phase_4.surfaced_disagreements,
    )


def write_goldens(modes: tuple[OutputMode, ...] = tuple(OutputMode)) -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    session = build_persisted_session()
    # Validate the round trip before writing (runs every Session validator).
    payload = session.model_dump_json(indent=2) + "\n"
    Session.model_validate_json(payload)
    SESSION_PATH.write_text(payload, encoding="utf-8")
    for mode in modes:
        golden_path(mode).write_text(
            render_from_persisted(session, mode), encoding="utf-8"
        )


if __name__ == "__main__":
    write_goldens()
