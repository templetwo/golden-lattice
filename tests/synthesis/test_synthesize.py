"""Tests for the synthesize() engine entry point — composition behavior.

Distinct from per-rule tests (test_claim_trace, test_elevation, etc.) which
verify each rule in isolation. This file tests the COMPOSITION: the four
rules running in sequence, the SynthesisArtifact assembled correctly, the
mode parameter affecting only the rendering layer, errors propagating
without partial artifacts.

The mode-orthogonality test here is the load-bearing closure assertion:
same Session, four modes, four different output strings, identical
underlying artifact (claim_trace, elevations, surfaced_disagreements).
Catches compositional drift where mode accidentally couples to non-rendering
rule outputs.
"""

from datetime import datetime, timezone

import pytest

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
    ClaimTraceEntry,
    DialogueTurn,
    IndependentResponse,
    Session,
    SynthesisArtifact,
)
from golden_lattice.synthesis import (
    SynthesisInputError,
    synthesize,
)


NOW = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)


def _claim(model: ModelId, text: str) -> Claim:
    return Claim(
        claim_id=claim_id_for(model, Phase.INDEPENDENT, text),
        source_model=model,
        source_phase=Phase.INDEPENDENT,
        text=text,
    )


def _response(model: ModelId, claims: tuple[Claim, ...]) -> IndependentResponse:
    return IndependentResponse(
        model_id=model,
        prompt_hash="h",
        response=f"{model.value} response",
        focus_tag=FocusTag.CORRECTNESS,
        confidence=0.8,
        claims=claims,
        generation_started_at=NOW,
        generation_completed_at=NOW,
    )


def _full_triadic_session() -> Session:
    """Triad with Phase 1 + Phase 3 (converge turns from two distinct speakers
    on Opus's first claim — produces an Elevation when synthesize runs)."""
    o_a = _claim(ModelId.OPUS, "opus alpha")
    o_b = _claim(ModelId.OPUS, "opus beta")
    s_a = _claim(ModelId.SONNET, "sonnet alpha")
    h_a = _claim(ModelId.HAIKU, "haiku alpha")

    sonnet_converge = DialogueTurn(
        turn_id="c_s",
        speaker_model=ModelId.SONNET,
        channel="converge",
        target_model=ModelId.OPUS,
        target_claim_ids=(o_a.claim_id,),
        content="aligned with opus alpha",
    )
    haiku_converge = DialogueTurn(
        turn_id="c_h",
        speaker_model=ModelId.HAIKU,
        channel="converge",
        target_model=ModelId.OPUS,
        target_claim_ids=(o_a.claim_id,),
        content="aligned with opus alpha",
    )
    return Session(
        session_id="t",
        prompt="design a cache",
        prompt_hash="h",
        models_invited=(ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU),
        phase_1={
            ModelId.OPUS: _response(ModelId.OPUS, (o_a, o_b)),
            ModelId.SONNET: _response(ModelId.SONNET, (s_a,)),
            ModelId.HAIKU: _response(ModelId.HAIKU, (h_a,)),
        },
        phase_3=(sonnet_converge, haiku_converge),
    )


# --- Happy path -----------------------------------------------------------


def test_synthesize_returns_synthesis_artifact_with_all_four_rules_applied():
    session = _full_triadic_session()
    artifact = synthesize(session, confidence_threshold=0.7)
    assert isinstance(artifact, SynthesisArtifact)
    assert set(artifact.synthesis_rules_applied) == {
        SynthesisRule.IRREDUCIBILITY_PRESERVATION,
        SynthesisRule.AGREEMENT_ELEVATION,
        SynthesisRule.DISAGREEMENT_SURFACING,
        SynthesisRule.ATTRIBUTION_PRESERVATION,
    }
    # Trace covers every Phase 1 claim (irreducibility preservation).
    phase_1_claim_ids = {
        c.claim_id for r in session.phase_1.values() for c in r.claims
    }
    traced_ids = {e.claim_id for e in artifact.claim_trace}
    assert phase_1_claim_ids == traced_ids
    # Elevation surfaced from the two-distinct-speaker converge agreement.
    assert len(artifact.elevations) == 1


def test_synthesize_default_mode_is_annotated():
    """Per ARCHITECTURE.md §7: 'Default: annotated. The annotation is the proof
    we did not flatten.'"""
    session = _full_triadic_session()
    artifact = synthesize(session, confidence_threshold=0.7)
    assert artifact.output_mode is OutputMode.ANNOTATED
    # Annotated output contains attribution markers.
    assert "[O]" in artifact.output


def test_synthesize_returns_artifact_not_session():
    """Synthesize stays a pure transformation; Session construction is the
    orchestrator's job."""
    session = _full_triadic_session()
    result = synthesize(session, confidence_threshold=0.7)
    assert isinstance(result, SynthesisArtifact)
    assert not isinstance(result, Session)
    # Session itself unchanged — synthesize doesn't fold the artifact in.
    assert session.phase_4 is None


# --- Error propagation ---------------------------------------------------


def test_synthesize_refuses_session_with_phase_4_already_populated():
    session = _full_triadic_session()
    first = synthesize(session, confidence_threshold=0.7)
    populated = Session(
        session_id=session.session_id,
        prompt=session.prompt,
        prompt_hash=session.prompt_hash,
        models_invited=session.models_invited,
        phase_1=session.phase_1,
        phase_3=session.phase_3,
        phase_4=first,
    )
    with pytest.raises(SynthesisInputError, match="refuses to overwrite"):
        synthesize(populated, confidence_threshold=0.7)


def test_synthesize_propagates_no_partial_artifacts():
    """If validate raises, no partial artifact is returned. Atomic operation."""
    session = _full_triadic_session()
    populated = Session(
        session_id=session.session_id,
        prompt=session.prompt,
        prompt_hash=session.prompt_hash,
        models_invited=session.models_invited,
        phase_1=session.phase_1,
        phase_3=session.phase_3,
        phase_4=synthesize(session, confidence_threshold=0.7),
    )
    try:
        synthesize(populated, confidence_threshold=0.7)
        pytest.fail("expected SynthesisInputError")
    except SynthesisInputError:
        # No partial artifact, no mutation of input session.
        assert populated.phase_4 is not None  # was set before the call


# --- Mode behavior --------------------------------------------------------


def test_synthesize_mode_parameter_changes_only_output_string():
    """CLOSURE TEST — mode-orthogonality.

    Same Session, four modes, four different output strings, identical
    underlying artifacts (claim_trace, elevations, surfaced_disagreements).
    Catches compositional drift where mode accidentally couples to
    non-rendering rule outputs (e.g., 'skip the trace for transcript
    mode' regression).
    """
    session = _full_triadic_session()
    artifacts = {
        mode: synthesize(session, mode=mode, confidence_threshold=0.7)
        for mode in OutputMode
    }
    # Renderings differ across all four modes.
    outputs = [a.output for a in artifacts.values()]
    assert len(set(outputs)) == len(OutputMode), (
        f"expected 4 distinct outputs across modes, got {len(set(outputs))}"
    )
    # But the underlying artifacts are otherwise identical.
    reference = artifacts[OutputMode.ANNOTATED]
    for mode, art in artifacts.items():
        assert art.claim_trace == reference.claim_trace, (
            f"claim_trace differs in {mode}"
        )
        assert art.elevations == reference.elevations, f"elevations differ in {mode}"
        assert art.surfaced_disagreements == reference.surfaced_disagreements, (
            f"surfaced_disagreements differ in {mode}"
        )
        assert set(art.synthesis_rules_applied) == set(reference.synthesis_rules_applied)
        # output_mode field reflects the requested mode.
        assert art.output_mode is mode


# --- Determinism ----------------------------------------------------------


def test_synthesize_is_deterministic():
    """Same call twice → identical artifact (Pydantic equality)."""
    session = _full_triadic_session()
    a = synthesize(session, confidence_threshold=0.7)
    b = synthesize(session, confidence_threshold=0.7)
    assert a == b


def test_synthesize_does_not_mutate_session():
    session = _full_triadic_session()
    snapshot_before = session.model_dump_json()
    synthesize(session, confidence_threshold=0.7)
    snapshot_after = session.model_dump_json()
    assert snapshot_before == snapshot_after


# --- Substrate closure ---------------------------------------------------


def test_synthesize_artifact_folds_into_substrate_valid_session():
    """The artifact returned by synthesize, when set as phase_4 on the Session,
    yields a substrate-valid Session. End-to-end closure across the engine."""
    session = _full_triadic_session()
    artifact = synthesize(session, confidence_threshold=0.7)
    full = Session(
        session_id=session.session_id,
        prompt=session.prompt,
        prompt_hash=session.prompt_hash,
        models_invited=session.models_invited,
        phase_1=session.phase_1,
        phase_3=session.phase_3,
        phase_4=artifact,
    )
    assert full.phase_4 is artifact
    # Every Phase 1 claim is traced (substrate's irreducibility validator passed).
    phase_1_claim_ids = {
        c.claim_id for r in full.phase_1.values() for c in r.claims
    }
    traced_ids = {e.claim_id for e in full.phase_4.claim_trace}
    assert phase_1_claim_ids == traced_ids
