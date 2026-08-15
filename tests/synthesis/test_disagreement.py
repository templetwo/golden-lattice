"""Tests for Rule 3 (disagreement surfacing) — compute_surfaced_disagreements."""

from datetime import datetime, timezone

import pytest

from golden_lattice.memory_graph.base import (
    FocusTag,
    ModelId,
    Phase,
    SynthesisRule,
    claim_id_for,
)
from golden_lattice.memory_graph.schema import (
    Claim,
    ClaimRef,
    ClaimTraceEntry,
    CrossReading,
    DialogueTurn,
    Disagreement,
    IndependentResponse,
    Session,
    SynthesisArtifact,
)
from golden_lattice.synthesis.disagreement import (
    compute_surfaced_disagreements,
)


NOW = datetime(2026, 5, 3, 12, 0, 0, tzinfo=timezone.utc)


def _claim(model: ModelId, text: str) -> Claim:
    return Claim(
        claim_id=claim_id_for(model, Phase.INDEPENDENT, text),
        source_model=model,
        source_phase=Phase.INDEPENDENT,
        text=text,
    )


def _response(
    model: ModelId,
    claims: tuple[Claim, ...],
    confidence: float = 0.8,
) -> IndependentResponse:
    return IndependentResponse(
        model_id=model,
        prompt_hash="h",
        response="r",
        focus_tag=FocusTag.CORRECTNESS,
        confidence=confidence,
        claims=claims,
        generation_started_at=NOW,
        generation_completed_at=NOW,
    )


def _critique(
    turn_id: str,
    speaker: ModelId,
    target_model: ModelId,
    target_claim_ids: tuple[str, ...],
    content: str = "i disagree",
) -> DialogueTurn:
    return DialogueTurn(
        turn_id=turn_id,
        speaker_model=speaker,
        channel="critique",
        target_model=target_model,
        target_claim_ids=target_claim_ids,
        content=content,
    )


def _triad_claims() -> dict[ModelId, list[Claim]]:
    """Two claims per model. Pure function — same input every call."""
    return {
        ModelId.OPUS: [
            _claim(ModelId.OPUS, "opus alpha"),
            _claim(ModelId.OPUS, "opus beta"),
        ],
        ModelId.SONNET: [
            _claim(ModelId.SONNET, "sonnet alpha"),
            _claim(ModelId.SONNET, "sonnet beta"),
        ],
        ModelId.HAIKU: [
            _claim(ModelId.HAIKU, "haiku alpha"),
            _claim(ModelId.HAIKU, "haiku beta"),
        ],
    }


def _triad_session_with(
    claims: dict[ModelId, list[Claim]],
    *,
    confidences: dict[ModelId, float] | None = None,
    phase_2: tuple[CrossReading, ...] = (),
    phase_3: tuple[DialogueTurn, ...] = (),
) -> Session:
    """Build a triadic Session from pre-built claims. Splitting claim-construction
    from session-construction avoids the chicken-and-egg pattern where a phase_3
    DialogueTurn needs to reference claim_ids that the session itself produces."""
    confidences = confidences or {}
    phase_1 = {
        m: _response(m, tuple(cs), confidence=confidences.get(m, 0.8))
        for m, cs in claims.items()
    }
    return Session(
        session_id="t",
        prompt="p",
        prompt_hash="h",
        models_invited=(ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU),
        phase_1=phase_1,
        phase_2=phase_2,
        phase_3=phase_3,
    )


# --- Positive case --------------------------------------------------------


def test_critique_with_two_target_claim_ids_above_threshold_surfaces():
    claims = _triad_claims()
    session = _triad_session_with(
        claims,
        phase_3=(
            _critique(
                "crit_1",
                speaker=ModelId.SONNET,
                target_model=ModelId.OPUS,
                target_claim_ids=(claims[ModelId.OPUS][0].claim_id, claims[ModelId.OPUS][1].claim_id),
                content="both opus claims miss the boundary",
            ),
        ),
    )
    out = compute_surfaced_disagreements(session, confidence_threshold=0.7)
    assert len(out) == 1
    sd = out[0]
    assert set(sd.claim_ids) == {
        claims[ModelId.OPUS][0].claim_id,
        claims[ModelId.OPUS][1].claim_id,
    }
    assert "claude-sonnet-5 disagrees with claude-opus-5" in sd.note
    assert "both opus claims miss the boundary" in sd.note


# --- Threshold gating both sides -----------------------------------------


def test_speaker_below_threshold_not_surfaced():
    claims = _triad_claims()
    session = _triad_session_with(
        claims,
        confidences={ModelId.SONNET: 0.4, ModelId.OPUS: 0.9},
        phase_3=(
            _critique(
                "crit_1",
                speaker=ModelId.SONNET,
                target_model=ModelId.OPUS,
                target_claim_ids=(claims[ModelId.OPUS][0].claim_id, claims[ModelId.OPUS][1].claim_id),
            ),
        ),
    )
    assert compute_surfaced_disagreements(session, confidence_threshold=0.7) == ()


def test_target_author_below_threshold_not_surfaced():
    claims = _triad_claims()
    session = _triad_session_with(
        claims,
        confidences={ModelId.SONNET: 0.9, ModelId.OPUS: 0.4},
        phase_3=(
            _critique(
                "crit_1",
                speaker=ModelId.SONNET,
                target_model=ModelId.OPUS,
                target_claim_ids=(claims[ModelId.OPUS][0].claim_id, claims[ModelId.OPUS][1].claim_id),
            ),
        ),
    )
    assert compute_surfaced_disagreements(session, confidence_threshold=0.7) == ()


def test_both_at_threshold_surfaces():
    """Boundary case: both at exactly threshold passes (>= comparison)."""
    claims = _triad_claims()
    session = _triad_session_with(
        claims,
        confidences={ModelId.SONNET: 0.7, ModelId.OPUS: 0.7},
        phase_3=(
            _critique(
                "crit_1",
                speaker=ModelId.SONNET,
                target_model=ModelId.OPUS,
                target_claim_ids=(claims[ModelId.OPUS][0].claim_id, claims[ModelId.OPUS][1].claim_id),
            ),
        ),
    )
    assert len(compute_surfaced_disagreements(session, confidence_threshold=0.7)) == 1


# --- v0 staging refusals -------------------------------------------------


def test_critique_with_one_target_claim_id_not_surfaced_in_v0():
    """v0 staging: single-target critiques remain in dialogue without artifact lift.
    DO NOT synthesize fake pairs to satisfy the substrate's ≥2 claim_ids requirement —
    that would feed the substrate's refusal with constructed data that meets cardinality
    without honoring architectural meaning."""
    claims = _triad_claims()
    session = _triad_session_with(
        claims,
        phase_3=(
            _critique(
                "crit_1",
                speaker=ModelId.SONNET,
                target_model=ModelId.OPUS,
                target_claim_ids=(claims[ModelId.OPUS][0].claim_id,),  # only one
            ),
        ),
    )
    assert compute_surfaced_disagreements(session, confidence_threshold=0.7) == ()


def test_phase_2_disagreement_not_surfaced_in_v0():
    """Phase 2 Disagreement structurally has 1 target_claim_id. Not lifted to
    SurfacedDisagreement in v0. Remains in Phase 2 cross-reading."""
    claims = _triad_claims()
    sonnet_reads_opus = CrossReading(
        reader_model=ModelId.SONNET,
        target_model=ModelId.OPUS,
        disagreements=(
            Disagreement(
                target_claim_id=claims[ModelId.OPUS][0].claim_id,
                reason="i disagree with opus alpha",
            ),
        ),
    )
    session = _triad_session_with(claims, phase_2=(sonnet_reads_opus,))
    assert compute_surfaced_disagreements(session, confidence_threshold=0.7) == ()


def test_non_critique_phase_3_channels_not_surfaced():
    """Augment and converge are not disagreement signals."""
    claims = _triad_claims()
    session = _triad_session_with(
        claims,
        phase_3=(
            DialogueTurn(
                turn_id="aug_1",
                speaker_model=ModelId.SONNET,
                channel="augment",
                target_model=ModelId.OPUS,
                target_claim_ids=(claims[ModelId.OPUS][0].claim_id, claims[ModelId.OPUS][1].claim_id),
                content="adding to opus",
            ),
            DialogueTurn(
                turn_id="con_1",
                speaker_model=ModelId.HAIKU,
                channel="converge",
                target_model=ModelId.OPUS,
                target_claim_ids=(claims[ModelId.OPUS][0].claim_id, claims[ModelId.OPUS][1].claim_id),
                content="agreeing with opus",
            ),
        ),
    )
    assert compute_surfaced_disagreements(session, confidence_threshold=0.7) == ()


# --- Note format ---------------------------------------------------------


def test_note_follows_template_format():
    """Engine-authored prose is templated, deterministic from inputs.
    Format: '{speaker_model} disagrees with {target_model}: {critique_content}'."""
    claims = _triad_claims()
    session = _triad_session_with(
        claims,
        phase_3=(
            _critique(
                "crit_1",
                speaker=ModelId.HAIKU,
                target_model=ModelId.SONNET,
                target_claim_ids=(claims[ModelId.SONNET][0].claim_id, claims[ModelId.SONNET][1].claim_id),
                content="sonnet's framing skips edge cases",
            ),
        ),
    )
    out = compute_surfaced_disagreements(session, confidence_threshold=0.7)
    assert len(out) == 1
    expected = (
        "claude-haiku-4-5-20251001 disagrees with claude-sonnet-5: "
        "sonnet's framing skips edge cases"
    )
    assert out[0].note == expected


# --- Determinism + no side effects ---------------------------------------


def test_compute_surfaced_disagreements_is_deterministic():
    claims = _triad_claims()
    session = _triad_session_with(
        claims,
        phase_3=(
            _critique(
                "crit_1",
                speaker=ModelId.SONNET,
                target_model=ModelId.OPUS,
                target_claim_ids=(claims[ModelId.OPUS][0].claim_id, claims[ModelId.OPUS][1].claim_id),
                content="critique a",
            ),
            _critique(
                "crit_2",
                speaker=ModelId.HAIKU,
                target_model=ModelId.SONNET,
                target_claim_ids=(claims[ModelId.SONNET][0].claim_id, claims[ModelId.SONNET][1].claim_id),
                content="critique b",
            ),
        ),
    )
    a = compute_surfaced_disagreements(session, confidence_threshold=0.7)
    b = compute_surfaced_disagreements(session, confidence_threshold=0.7)
    assert a == b


def test_compute_surfaced_disagreements_does_not_mutate_session():
    claims = _triad_claims()
    session = _triad_session_with(
        claims,
        phase_3=(
            _critique(
                "crit_1",
                speaker=ModelId.SONNET,
                target_model=ModelId.OPUS,
                target_claim_ids=(claims[ModelId.OPUS][0].claim_id, claims[ModelId.OPUS][1].claim_id),
            ),
        ),
    )
    snapshot_before = session.model_dump_json()
    compute_surfaced_disagreements(session, confidence_threshold=0.7)
    snapshot_after = session.model_dump_json()
    assert snapshot_before == snapshot_after


def test_rule_independence_vs_other_rules():
    """Running other rules between two compute_surfaced_disagreements calls
    does not change Rule 3's output."""
    from golden_lattice.synthesis.claim_trace import build_claim_trace
    from golden_lattice.synthesis.elevation import compute_elevations

    claims = _triad_claims()
    session = _triad_session_with(
        claims,
        phase_3=(
            _critique(
                "crit_1",
                speaker=ModelId.SONNET,
                target_model=ModelId.OPUS,
                target_claim_ids=(claims[ModelId.OPUS][0].claim_id, claims[ModelId.OPUS][1].claim_id),
            ),
        ),
    )
    first = compute_surfaced_disagreements(session, confidence_threshold=0.7)
    build_claim_trace(session)
    compute_elevations(session)
    second = compute_surfaced_disagreements(session, confidence_threshold=0.7)
    assert first == second


# --- Substrate-refusal closure -------------------------------------------


def test_surfaced_disagreements_flow_through_substrate_validators_when_folded_into_session():
    """Closure: a SurfacedDisagreement produced by Rule 3 satisfies
    Session._synthesis_surfaced_disagreements_resolve_claim_ids when folded
    into a complete Session."""
    from golden_lattice.synthesis.claim_trace import build_claim_trace

    claims = _triad_claims()
    session = _triad_session_with(
        claims,
        phase_3=(
            _critique(
                "crit_1",
                speaker=ModelId.SONNET,
                target_model=ModelId.OPUS,
                target_claim_ids=(claims[ModelId.OPUS][0].claim_id, claims[ModelId.OPUS][1].claim_id),
            ),
        ),
    )
    surfaced = compute_surfaced_disagreements(session, confidence_threshold=0.7)
    assert len(surfaced) == 1

    trace = build_claim_trace(session)
    synthesis = SynthesisArtifact(
        output="o",
        claim_trace=trace,
        synthesis_rules_applied=(
            SynthesisRule.IRREDUCIBILITY_PRESERVATION,
            SynthesisRule.DISAGREEMENT_SURFACING,
        ),
        surfaced_disagreements=surfaced,
    )
    full_session = Session(
        session_id=session.session_id,
        prompt=session.prompt,
        prompt_hash=session.prompt_hash,
        models_invited=session.models_invited,
        phase_1=session.phase_1,
        phase_3=session.phase_3,
        phase_4=synthesis,
    )
    assert full_session.phase_4 is synthesis
    assert len(full_session.phase_4.surfaced_disagreements) == 1


# --- v0 staging-artifact test --------------------------------------------


def test_phase_1_confidence_for_claim_raises_on_non_phase_1_claim():
    """Defensive helper: confidence semantics for Phase 2 missing claims are an
    open v1 architectural decision. v0 raises NotImplementedError so the
    deferral is structural, not implicit."""
    from golden_lattice.synthesis.disagreement import _phase_1_confidence_for_claim

    claims = _triad_claims()
    session = _triad_session_with(claims)
    # Real Phase 1 claim: returns confidence.
    assert _phase_1_confidence_for_claim(session, claims[ModelId.OPUS][0].claim_id) == 0.8
    # Unknown claim_id (could be a Phase 2 missing claim or just a ghost): raises.
    with pytest.raises(NotImplementedError, match="open v1 architectural"):
        _phase_1_confidence_for_claim(session, "not_a_phase_1_claim_id")


def test_v0_staging_artifact_claim_in_disagreement_may_also_be_omitted_or_elevated():
    """Documented v0 inconsistency: a claim involved in a SurfacedDisagreement
    is not yet visible to Rule 1 (could still be marked omitted) or Rule 2
    (could still be elevated). v1 composition resolves these inconsistencies.
    Test exists as a marker so v1 has to acknowledge it's fixing a known state."""
    from golden_lattice.synthesis.claim_trace import build_claim_trace

    claims = _triad_claims()
    session = _triad_session_with(
        claims,
        phase_3=(
            _critique(
                "crit_1",
                speaker=ModelId.SONNET,
                target_model=ModelId.OPUS,
                target_claim_ids=(claims[ModelId.OPUS][0].claim_id, claims[ModelId.OPUS][1].claim_id),
            ),
        ),
    )
    surfaced = compute_surfaced_disagreements(session, confidence_threshold=0.7)
    assert len(surfaced) == 1
    sd_claim_ids = set(surfaced[0].claim_ids)

    # Rule 1 v0 doesn't know about Rule 3's output. The claims involved in the
    # surfaced disagreement could STILL be omitted by Rule 1 if conditions
    # (self-flagged-weakest + no Phase 2 engagement) were met. Phase 3 critique
    # involvement is not Phase 2 engagement in v0.
    trace = build_claim_trace(session)
    traced_dispositions = {e.claim_id: e.disposition for e in trace}
    # In this session, Opus has no self-reflection artifact, so the claims are
    # all "present". The point of the test is the documented gap, not the
    # specific session outcome — verify the claims are traced (totality holds).
    for cid in sd_claim_ids:
        assert cid in traced_dispositions
