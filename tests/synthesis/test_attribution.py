"""Tests for Rule 4 (attribution / output mode rendering) — render_output."""

from datetime import datetime, timezone

import pytest

from golden_lattice.memory_graph.base import (
    FocusTag,
    ModelId,
    OutputMode,
    Phase,
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
    SelfReflectionArtifact,
    Session,
)
from golden_lattice.synthesis.attribution import render_output
from golden_lattice.synthesis.claim_trace import build_claim_trace
from golden_lattice.synthesis.disagreement import compute_surfaced_disagreements
from golden_lattice.synthesis.elevation import compute_elevations


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
    claims: tuple[Claim, ...],
    *,
    confidence: float = 0.8,
    self_reflection_artifacts: tuple[SelfReflectionArtifact, ...] = (),
) -> IndependentResponse:
    return IndependentResponse(
        model_id=model,
        prompt_hash="h",
        response=f"{model.value} response prose",
        focus_tag=FocusTag.CORRECTNESS,
        confidence=confidence,
        claims=claims,
        self_reflection_artifacts=self_reflection_artifacts,
        generation_started_at=NOW,
        generation_completed_at=NOW,
    )


def _triad_session_three_claims_per_model() -> tuple[Session, dict[ModelId, list[Claim]]]:
    """Triadic session with three Phase 1 claims per model. No Phase 2/3 by default."""
    claims_by_model: dict[ModelId, list[Claim]] = {
        ModelId.OPUS: [
            _claim(ModelId.OPUS, "opus alpha"),
            _claim(ModelId.OPUS, "opus beta"),
            _claim(ModelId.OPUS, "opus gamma"),
        ],
        ModelId.SONNET: [
            _claim(ModelId.SONNET, "sonnet alpha"),
            _claim(ModelId.SONNET, "sonnet beta"),
            _claim(ModelId.SONNET, "sonnet gamma"),
        ],
        ModelId.HAIKU: [
            _claim(ModelId.HAIKU, "haiku alpha"),
            _claim(ModelId.HAIKU, "haiku beta"),
            _claim(ModelId.HAIKU, "haiku gamma"),
        ],
    }
    session = Session(
        session_id="t",
        prompt="design a cache",
        prompt_hash="h",
        models_invited=(ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU),
        phase_1={m: _response(m, tuple(cs)) for m, cs in claims_by_model.items()},
    )
    return session, claims_by_model


# --- Annotated mode (canonical proof-form) -------------------------------


def test_annotated_renders_each_claim_with_marker_in_trace_order():
    session, claims = _triad_session_three_claims_per_model()
    trace = build_claim_trace(session)
    out = render_output(
        session,
        mode=OutputMode.ANNOTATED,
        claim_trace=trace,
        elevations=(),
        surfaced_disagreements=(),
    )
    # claim_trace order is sorted by model.value then claim emission order.
    expected_lines = [
        "[H] haiku alpha",
        "[H] haiku beta",
        "[H] haiku gamma",
        "[O] opus alpha",
        "[O] opus beta",
        "[O] opus gamma",
        "[S] sonnet alpha",
        "[S] sonnet beta",
        "[S] sonnet gamma",
    ]
    assert out == "\n".join(expected_lines)


def test_annotated_renders_modified_text_for_modified_disposition():
    """When a claim_trace_entry has disposition='modified', annotated renders modified_text."""
    session, claims = _triad_session_three_claims_per_model()
    o_alpha = claims[ModelId.OPUS][0]
    custom_trace = (
        ClaimTraceEntry(
            claim_id=o_alpha.claim_id,
            disposition="modified",
            modified_text="opus alpha, sharpened by synthesis",
        ),
    )
    # Build a minimal session that only has Opus's first claim so totality holds.
    minimal = Session(
        session_id="t",
        prompt="p",
        prompt_hash="h",
        models_invited=(ModelId.OPUS, ModelId.SONNET),
        phase_1={
            ModelId.OPUS: _response(ModelId.OPUS, (o_alpha,)),
            ModelId.SONNET: _response(
                ModelId.SONNET,
                (claims[ModelId.SONNET][0],),
            ),
        },
    )
    full_trace = (
        custom_trace[0],
        ClaimTraceEntry(
            claim_id=claims[ModelId.SONNET][0].claim_id, disposition="present"
        ),
    )
    out = render_output(
        minimal,
        mode=OutputMode.ANNOTATED,
        claim_trace=full_trace,
        elevations=(),
        surfaced_disagreements=(),
    )
    assert "[O] opus alpha, sharpened by synthesis" in out
    assert "[S] sonnet alpha" in out


def test_annotated_skips_omitted_claims_in_prose():
    """Omitted claims do not appear in output: str even though they remain in claim_trace."""
    session, claims = _triad_session_three_claims_per_model()
    o_alpha = claims[ModelId.OPUS][0]
    s_alpha = claims[ModelId.SONNET][0]
    trace = (
        ClaimTraceEntry(claim_id=o_alpha.claim_id, disposition="present"),
        ClaimTraceEntry(
            claim_id=s_alpha.claim_id,
            disposition="omitted",
            omission_reason="low_confidence_isolated:0.3",
        ),
    )
    minimal = Session(
        session_id="t",
        prompt="p",
        prompt_hash="h",
        models_invited=(ModelId.OPUS, ModelId.SONNET),
        phase_1={
            ModelId.OPUS: _response(ModelId.OPUS, (o_alpha,)),
            ModelId.SONNET: _response(ModelId.SONNET, (s_alpha,)),
        },
    )
    out = render_output(
        minimal,
        mode=OutputMode.ANNOTATED,
        claim_trace=trace,
        elevations=(),
        surfaced_disagreements=(),
    )
    assert "[O] opus alpha" in out
    assert "sonnet alpha" not in out
    assert "low_confidence_isolated" not in out  # reason not in prose


def test_annotated_no_trailers_in_output_str():
    """Elevations and surfaced_disagreements DO NOT appear in output: str.
    They live on SynthesisArtifact fields for queryable access."""
    session, claims = _triad_session_three_claims_per_model()
    trace = build_claim_trace(session)

    # Make a real Elevation by adding two converge turns.
    o_alpha = claims[ModelId.OPUS][0]
    sonnet_converge = DialogueTurn(
        turn_id="c_s",
        speaker_model=ModelId.SONNET,
        channel="converge",
        target_model=ModelId.OPUS,
        target_claim_ids=(o_alpha.claim_id,),
        content="aligned",
    )
    haiku_converge = DialogueTurn(
        turn_id="c_h",
        speaker_model=ModelId.HAIKU,
        channel="converge",
        target_model=ModelId.OPUS,
        target_claim_ids=(o_alpha.claim_id,),
        content="aligned",
    )
    session_with_p3 = Session(
        session_id="t",
        prompt="p",
        prompt_hash="h",
        models_invited=session.models_invited,
        phase_1=session.phase_1,
        phase_3=(sonnet_converge, haiku_converge),
    )
    elevations = compute_elevations(session_with_p3)
    out = render_output(
        session_with_p3,
        mode=OutputMode.ANNOTATED,
        claim_trace=trace,
        elevations=elevations,
        surfaced_disagreements=(),
    )
    assert len(elevations) == 1
    # Elevation content is not in output: str.
    assert "elevation" not in out.lower()
    assert "agreement" not in out.lower()
    # The claim is still there with its marker (it was elevated via Phase 3 but
    # in annotated rendering elevations are just artifact metadata, not prose).
    assert "[O] opus alpha" in out


# --- Layered mode --------------------------------------------------------


def test_layered_renders_per_model_sections_in_alphabetical_order():
    session, claims = _triad_session_three_claims_per_model()
    trace = build_claim_trace(session)
    out = render_output(
        session,
        mode=OutputMode.LAYERED,
        claim_trace=trace,
        elevations=(),
        surfaced_disagreements=(),
    )
    # Section headers in alphabetical model.value order: haiku < opus < sonnet.
    h_pos = out.index("=== claude-haiku-4-5 ===")
    o_pos = out.index("=== claude-opus-4-7 ===")
    s_pos = out.index("=== claude-sonnet-4-6 ===")
    assert h_pos < o_pos < s_pos


def test_layered_within_section_uses_phase_1_emission_order():
    """Layered mode preserves peer-voice — within Opus's section, Opus's claims
    appear in the order Opus emitted them in Phase 1, NOT in claim_trace order."""
    session, claims = _triad_session_three_claims_per_model()
    trace = build_claim_trace(session)
    out = render_output(
        session,
        mode=OutputMode.LAYERED,
        claim_trace=trace,
        elevations=(),
        surfaced_disagreements=(),
    )
    # Within Opus's section, "opus alpha" appears before "opus beta" before "opus gamma".
    opus_section_start = out.index("=== claude-opus-4-7 ===")
    opus_section_end = out.index("=== claude-sonnet-4-6 ===")
    opus_section = out[opus_section_start:opus_section_end]
    a_pos = opus_section.index("opus alpha")
    b_pos = opus_section.index("opus beta")
    g_pos = opus_section.index("opus gamma")
    assert a_pos < b_pos < g_pos


def test_layered_renders_no_attribution_markers():
    session, claims = _triad_session_three_claims_per_model()
    trace = build_claim_trace(session)
    out = render_output(
        session,
        mode=OutputMode.LAYERED,
        claim_trace=trace,
        elevations=(),
        surfaced_disagreements=(),
    )
    # Layered uses section headers, not inline markers. No [O], [S], [H] in body.
    assert "[O]" not in out
    assert "[S]" not in out
    assert "[H]" not in out


def test_layered_renders_original_claim_text_for_modified_disposition():
    """Layered mode shows what each model SAID (original claim.text), not what
    synthesis USED (modified_text). Diverges deliberately from annotated."""
    session, claims = _triad_session_three_claims_per_model()
    o_alpha = claims[ModelId.OPUS][0]
    s_alpha = claims[ModelId.SONNET][0]
    trace = (
        ClaimTraceEntry(
            claim_id=o_alpha.claim_id,
            disposition="modified",
            modified_text="opus alpha, sharpened by synthesis",
        ),
        ClaimTraceEntry(claim_id=s_alpha.claim_id, disposition="present"),
    )
    minimal = Session(
        session_id="t",
        prompt="p",
        prompt_hash="h",
        models_invited=(ModelId.OPUS, ModelId.SONNET),
        phase_1={
            ModelId.OPUS: _response(ModelId.OPUS, (o_alpha,)),
            ModelId.SONNET: _response(ModelId.SONNET, (s_alpha,)),
        },
    )
    out = render_output(
        minimal,
        mode=OutputMode.LAYERED,
        claim_trace=trace,
        elevations=(),
        surfaced_disagreements=(),
    )
    # Layered shows original "opus alpha", NOT the modified text.
    assert "opus alpha" in out
    assert "sharpened by synthesis" not in out
    assert "sonnet alpha" in out


def test_layered_skips_empty_sections():
    """If a model's claims are all omitted, its section is not rendered."""
    o_claim = _claim(ModelId.OPUS, "opus only")
    s_claim = _claim(ModelId.SONNET, "sonnet only")
    trace = (
        ClaimTraceEntry(claim_id=o_claim.claim_id, disposition="present"),
        ClaimTraceEntry(
            claim_id=s_claim.claim_id,
            disposition="omitted",
            omission_reason="low_confidence_isolated:0.3",
        ),
    )
    session = Session(
        session_id="t",
        prompt="p",
        prompt_hash="h",
        models_invited=(ModelId.OPUS, ModelId.SONNET),
        phase_1={
            ModelId.OPUS: _response(ModelId.OPUS, (o_claim,)),
            ModelId.SONNET: _response(ModelId.SONNET, (s_claim,)),
        },
    )
    out = render_output(
        session,
        mode=OutputMode.LAYERED,
        claim_trace=trace,
        elevations=(),
        surfaced_disagreements=(),
    )
    assert "=== claude-opus-4-7 ===" in out
    assert "=== claude-sonnet-4-6 ===" not in out  # all sonnet claims omitted


# --- Unified mode --------------------------------------------------------


def test_unified_strips_markers_keeps_content_and_order():
    session, claims = _triad_session_three_claims_per_model()
    trace = build_claim_trace(session)
    annotated = render_output(
        session,
        mode=OutputMode.ANNOTATED,
        claim_trace=trace,
        elevations=(),
        surfaced_disagreements=(),
    )
    unified = render_output(
        session,
        mode=OutputMode.UNIFIED,
        claim_trace=trace,
        elevations=(),
        surfaced_disagreements=(),
    )
    # Unified is annotated minus markers.
    assert "[O]" not in unified
    assert "[S]" not in unified
    assert "[H]" not in unified
    # Same content lines, same order. Strip markers from annotated and compare.
    stripped = "\n".join(
        line.split(" ", 1)[1] if line.startswith("[") else line
        for line in annotated.split("\n")
    )
    assert unified == stripped


def test_unified_no_generative_connective_text():
    """No 'Additionally,', 'Moreover,', etc. — disjointedness is the architectural cost."""
    session, claims = _triad_session_three_claims_per_model()
    trace = build_claim_trace(session)
    out = render_output(
        session,
        mode=OutputMode.UNIFIED,
        claim_trace=trace,
        elevations=(),
        surfaced_disagreements=(),
    )
    forbidden = ("Additionally,", "Moreover,", "Furthermore,", "In addition,")
    for token in forbidden:
        assert token not in out


def test_unified_uses_modified_text_for_modified_disposition():
    """Unified is annotated minus markers — uses modified_text where annotated does."""
    o_claim = _claim(ModelId.OPUS, "opus original")
    s_claim = _claim(ModelId.SONNET, "sonnet original")
    trace = (
        ClaimTraceEntry(
            claim_id=o_claim.claim_id,
            disposition="modified",
            modified_text="opus modified by synthesis",
        ),
        ClaimTraceEntry(claim_id=s_claim.claim_id, disposition="present"),
    )
    session = Session(
        session_id="t",
        prompt="p",
        prompt_hash="h",
        models_invited=(ModelId.OPUS, ModelId.SONNET),
        phase_1={
            ModelId.OPUS: _response(ModelId.OPUS, (o_claim,)),
            ModelId.SONNET: _response(ModelId.SONNET, (s_claim,)),
        },
    )
    out = render_output(
        session,
        mode=OutputMode.UNIFIED,
        claim_trace=trace,
        elevations=(),
        surfaced_disagreements=(),
    )
    assert "opus modified by synthesis" in out
    assert "opus original" not in out


# --- Transcript mode -----------------------------------------------------


def test_transcript_renders_phase_1_response_with_focus_tag_and_confidence():
    session, claims = _triad_session_three_claims_per_model()
    trace = build_claim_trace(session)
    out = render_output(
        session,
        mode=OutputMode.TRANSCRIPT,
        claim_trace=trace,
        elevations=(),
        surfaced_disagreements=(),
    )
    assert "=== Phase 1 ===" in out
    assert "claude-opus-4-7" in out
    assert "focus_tag: correctness" in out
    assert "confidence: 0.8" in out
    assert "opus alpha" in out


def test_transcript_does_not_render_phase_4_artifacts_in_output():
    """Transcript runs full pipeline (artifact carries claim_trace, elevations,
    surfaced_disagreements) but output: str is Phase 1-3 only."""
    session, claims = _triad_session_three_claims_per_model()
    o_alpha = claims[ModelId.OPUS][0]
    sonnet_converge = DialogueTurn(
        turn_id="c_s",
        speaker_model=ModelId.SONNET,
        channel="converge",
        target_model=ModelId.OPUS,
        target_claim_ids=(o_alpha.claim_id,),
        content="aligned",
    )
    haiku_converge = DialogueTurn(
        turn_id="c_h",
        speaker_model=ModelId.HAIKU,
        channel="converge",
        target_model=ModelId.OPUS,
        target_claim_ids=(o_alpha.claim_id,),
        content="aligned",
    )
    session_with_p3 = Session(
        session_id="t",
        prompt="p",
        prompt_hash="h",
        models_invited=session.models_invited,
        phase_1=session.phase_1,
        phase_3=(sonnet_converge, haiku_converge),
    )
    trace = build_claim_trace(session_with_p3)
    elevations = compute_elevations(session_with_p3)
    out = render_output(
        session_with_p3,
        mode=OutputMode.TRANSCRIPT,
        claim_trace=trace,
        elevations=elevations,
        surfaced_disagreements=(),
    )
    # Phase 4 artifact terms not in transcript output.
    assert "claim_trace" not in out
    assert "elevation" not in out.lower()
    assert "surfaced" not in out
    # Phase 3 IS rendered raw.
    assert "=== Phase 3 ===" in out
    assert "c_s" in out
    assert "aligned" in out


def test_transcript_includes_self_reflection_artifacts():
    """Per spec: transcript includes 'self-reflection artifacts.'"""
    o_a = _claim(ModelId.OPUS, "opus a")
    o_b = _claim(ModelId.OPUS, "opus b")
    reflection = SelfReflectionArtifact(
        model_id=ModelId.OPUS,
        generated_at=NOW,
        strongest_claim_id=o_a.claim_id,
        weakest_claim_id=o_b.claim_id,
        tag_justification="opus correctness justification",
    )
    s_a = _claim(ModelId.SONNET, "sonnet a")
    session = Session(
        session_id="t",
        prompt="p",
        prompt_hash="h",
        models_invited=(ModelId.OPUS, ModelId.SONNET),
        phase_1={
            ModelId.OPUS: _response(
                ModelId.OPUS, (o_a, o_b), self_reflection_artifacts=(reflection,)
            ),
            ModelId.SONNET: _response(ModelId.SONNET, (s_a,)),
        },
    )
    trace = build_claim_trace(session)
    out = render_output(
        session,
        mode=OutputMode.TRANSCRIPT,
        claim_trace=trace,
        elevations=(),
        surfaced_disagreements=(),
    )
    assert "self_reflection" in out
    assert "opus correctness justification" in out


# --- Determinism + no side effects --------------------------------------


def test_render_output_is_deterministic_per_mode():
    session, claims = _triad_session_three_claims_per_model()
    trace = build_claim_trace(session)
    for mode in OutputMode:
        a = render_output(
            session, mode=mode, claim_trace=trace, elevations=(), surfaced_disagreements=()
        )
        b = render_output(
            session, mode=mode, claim_trace=trace, elevations=(), surfaced_disagreements=()
        )
        assert a == b, f"non-deterministic in {mode}"


def test_render_output_does_not_mutate_session():
    session, claims = _triad_session_three_claims_per_model()
    trace = build_claim_trace(session)
    snapshot_before = session.model_dump_json()
    for mode in OutputMode:
        render_output(
            session, mode=mode, claim_trace=trace, elevations=(), surfaced_disagreements=()
        )
    snapshot_after = session.model_dump_json()
    assert snapshot_before == snapshot_after


# --- Substrate-refusal closure / full pipeline integration --------------


def test_full_pipeline_produces_substrate_valid_synthesis_artifact_per_mode():
    """CLOSURE TEST — architecturally significant.

    The synthesis engine's four rules + four output modes compose into
    substrate-valid Sessions. Failure here indicates COMPOSITIONAL DRIFT,
    not a unit-level bug. Debug at the integration boundary, not the
    rule-level boundary, when this fails.

    This is the first end-to-end closure assertion across the synthesis
    engine: until this test, each rule was tested in isolation against the
    substrate's individual refusals. This one verifies the rules compose
    into a substrate-valid whole.
    """
    from golden_lattice.memory_graph.base import SynthesisRule
    from golden_lattice.memory_graph.schema import SynthesisArtifact

    session, claims = _triad_session_three_claims_per_model()
    o_alpha = claims[ModelId.OPUS][0]
    sonnet_converge = DialogueTurn(
        turn_id="c_s",
        speaker_model=ModelId.SONNET,
        channel="converge",
        target_model=ModelId.OPUS,
        target_claim_ids=(o_alpha.claim_id,),
        content="aligned",
    )
    haiku_converge = DialogueTurn(
        turn_id="c_h",
        speaker_model=ModelId.HAIKU,
        channel="converge",
        target_model=ModelId.OPUS,
        target_claim_ids=(o_alpha.claim_id,),
        content="aligned",
    )
    session_full = Session(
        session_id="t",
        prompt="p",
        prompt_hash="h",
        models_invited=session.models_invited,
        phase_1=session.phase_1,
        phase_3=(sonnet_converge, haiku_converge),
    )
    trace = build_claim_trace(session_full)
    elevations = compute_elevations(session_full)
    surfaced = compute_surfaced_disagreements(session_full, confidence_threshold=0.7)

    for mode in OutputMode:
        rendered = render_output(
            session_full,
            mode=mode,
            claim_trace=trace,
            elevations=elevations,
            surfaced_disagreements=surfaced,
        )
        artifact = SynthesisArtifact(
            output=rendered,
            output_mode=mode,
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
        full = Session(
            session_id=session_full.session_id,
            prompt=session_full.prompt,
            prompt_hash=session_full.prompt_hash,
            models_invited=session_full.models_invited,
            phase_1=session_full.phase_1,
            phase_3=session_full.phase_3,
            phase_4=artifact,
        )
        assert full.phase_4 is artifact
        assert full.phase_4.output_mode is mode
