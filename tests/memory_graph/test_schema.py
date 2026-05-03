"""Tests for the Memory Graph schema. Each test exercises one structural refusal."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from golden_lattice.memory_graph.schema import (
    PARITY_THRESHOLD,
    Claim,
    ClaimRef,
    ClaimTraceEntry,
    CrossReading,
    DialogueTurn,
    Disagreement,
    FocusTag,
    IndependentResponse,
    ModelId,
    Phase,
    Session,
    SessionMetrics,
    SynthesisArtifact,
    claim_id_for,
)


NOW = datetime(2026, 5, 2, 12, 0, 0, tzinfo=timezone.utc)


def _phase1_claim(model: ModelId, text: str) -> Claim:
    return Claim(
        claim_id=claim_id_for(model, Phase.INDEPENDENT, text),
        source_model=model,
        source_phase=Phase.INDEPENDENT,
        text=text,
    )


def _independent_response(model: ModelId, prompt_hash: str, claims: tuple[Claim, ...]) -> IndependentResponse:
    return IndependentResponse(
        model_id=model,
        prompt_hash=prompt_hash,
        response="response text",
        focus_tag=FocusTag.CORRECTNESS,
        confidence=0.7,
        claims=claims,
        generation_started_at=NOW,
        generation_completed_at=NOW,
    )


# --- Claim ----------------------------------------------------------------


def test_claim_id_is_content_addressed():
    a = claim_id_for(ModelId.OPUS, Phase.INDEPENDENT, "hello")
    b = claim_id_for(ModelId.OPUS, Phase.INDEPENDENT, "hello")
    c = claim_id_for(ModelId.OPUS, Phase.INDEPENDENT, "different")
    assert a == b
    assert a != c


def test_phase_1_claim_with_parents_is_refused():
    with pytest.raises(ValidationError, match="Phase 1 claims cannot have parent_claim_ids"):
        Claim(
            claim_id=claim_id_for(ModelId.OPUS, Phase.INDEPENDENT, "x"),
            source_model=ModelId.OPUS,
            source_phase=Phase.INDEPENDENT,
            text="x",
            parent_claim_ids=("some_parent",),
        )


def test_synthesis_claim_is_refused():
    with pytest.raises(ValidationError, match="Synthesis is rule-based"):
        Claim(
            claim_id=claim_id_for(ModelId.OPUS, Phase.SYNTHESIS, "x"),
            source_model=ModelId.OPUS,
            source_phase=Phase.SYNTHESIS,
            text="x",
        )


def test_claim_id_must_match_content_hash():
    with pytest.raises(ValidationError, match="content hash"):
        Claim(
            claim_id="wrong_hash_value",
            source_model=ModelId.OPUS,
            source_phase=Phase.INDEPENDENT,
            text="x",
        )


# --- IndependentResponse --------------------------------------------------


def test_phase_1_claim_attributed_to_wrong_model_is_refused():
    foreign_claim = _phase1_claim(ModelId.SONNET, "x")
    with pytest.raises(ValidationError, match="attributed to"):
        _independent_response(ModelId.OPUS, "p", (foreign_claim,))


# --- CrossReading ---------------------------------------------------------


def test_cross_reading_self_is_refused():
    with pytest.raises(ValidationError, match="cannot cross-read its own response"):
        CrossReading(reader_model=ModelId.OPUS, target_model=ModelId.OPUS)


def test_cross_reading_missing_claim_phase_must_be_2():
    bad_claim = _phase1_claim(ModelId.SONNET, "noticed gap")
    with pytest.raises(ValidationError, match="source_phase=CROSS_READING"):
        CrossReading(
            reader_model=ModelId.SONNET,
            target_model=ModelId.HAIKU,
            missing=(bad_claim,),
        )


# --- ClaimTraceEntry ------------------------------------------------------


def test_modified_disposition_requires_modified_text():
    with pytest.raises(ValidationError, match="modified_text"):
        ClaimTraceEntry(claim_id="abc", disposition="modified")


def test_omitted_disposition_requires_omission_reason():
    with pytest.raises(ValidationError, match="omission_reason"):
        ClaimTraceEntry(claim_id="abc", disposition="omitted")


def test_present_disposition_carries_no_evidence():
    with pytest.raises(ValidationError, match="present but carries"):
        ClaimTraceEntry(claim_id="abc", disposition="present", modified_text="should not be here")


# --- DialogueTurn channel caps --------------------------------------------


def _build_minimal_session(
    phase_3: tuple[DialogueTurn, ...] = (),
    phase_4: SynthesisArtifact | None = None,
) -> Session:
    opus_claim = _phase1_claim(ModelId.OPUS, "opus claim")
    sonnet_claim = _phase1_claim(ModelId.SONNET, "sonnet claim")
    return Session(
        session_id="s1",
        prompt="p",
        prompt_hash="h1",
        models_invited=(ModelId.OPUS, ModelId.SONNET),
        phase_1={
            ModelId.OPUS: _independent_response(ModelId.OPUS, "h1", (opus_claim,)),
            ModelId.SONNET: _independent_response(ModelId.SONNET, "h1", (sonnet_claim,)),
        },
        phase_3=phase_3,
        phase_4=phase_4,
    )


def test_session_builds_when_well_formed():
    session = _build_minimal_session()
    assert session.session_id == "s1"
    assert len(session.all_claims()) == 2


def test_dialogue_channel_cap_is_enforced():
    turns = tuple(
        DialogueTurn(
            turn_id=f"t{i}",
            speaker_model=ModelId.OPUS,
            channel="critique",
            target_claim_id="some_id",
            content=f"turn {i}",
        )
        for i in range(4)
    )
    with pytest.raises(ValidationError, match="Phase 3 hard cap exceeded"):
        _build_minimal_session(phase_3=turns)


def test_dialogue_three_per_channel_is_allowed():
    turns = tuple(
        DialogueTurn(
            turn_id=f"t{i}",
            speaker_model=ModelId.OPUS,
            channel="critique",
            target_claim_id="some_id",
            content=f"turn {i}",
        )
        for i in range(3)
    )
    session = _build_minimal_session(phase_3=turns)
    assert len(session.phase_3) == 3


# --- Session-level invariants ---------------------------------------------


def test_session_requires_phase_1_for_every_invited_model():
    opus_claim = _phase1_claim(ModelId.OPUS, "x")
    with pytest.raises(ValidationError, match="missing Phase 1 responses"):
        Session(
            session_id="s2",
            prompt="p",
            prompt_hash="h",
            models_invited=(ModelId.OPUS, ModelId.SONNET),
            phase_1={ModelId.OPUS: _independent_response(ModelId.OPUS, "h", (opus_claim,))},
        )


def test_session_requires_consistent_prompt_hash_across_phase_1():
    opus_claim = _phase1_claim(ModelId.OPUS, "x")
    sonnet_claim = _phase1_claim(ModelId.SONNET, "y")
    with pytest.raises(ValidationError, match="Symmetric visibility"):
        Session(
            session_id="s3",
            prompt="p",
            prompt_hash="h_session",
            models_invited=(ModelId.OPUS, ModelId.SONNET),
            phase_1={
                ModelId.OPUS: _independent_response(ModelId.OPUS, "h_session", (opus_claim,)),
                ModelId.SONNET: _independent_response(ModelId.SONNET, "h_other", (sonnet_claim,)),
            },
        )


def test_session_requires_at_least_two_siblings():
    opus_claim = _phase1_claim(ModelId.OPUS, "x")
    with pytest.raises(ValidationError, match="at least two distinct siblings"):
        Session(
            session_id="s4",
            prompt="p",
            prompt_hash="h",
            models_invited=(ModelId.OPUS,),
            phase_1={ModelId.OPUS: _independent_response(ModelId.OPUS, "h", (opus_claim,))},
        )


def test_synthesis_must_trace_every_phase_1_claim():
    incomplete_synthesis = SynthesisArtifact(
        output="final",
        claim_trace=(),
        synthesis_rules_applied=("rule_a",),
    )
    with pytest.raises(ValidationError, match="Irreducibility preservation violated"):
        _build_minimal_session(phase_4=incomplete_synthesis)


def test_confidence_must_be_in_unit_interval():
    opus_claim = _phase1_claim(ModelId.OPUS, "x")
    with pytest.raises(ValidationError, match="outside"):
        IndependentResponse(
            model_id=ModelId.OPUS,
            prompt_hash="h",
            response="r",
            focus_tag=FocusTag.CORRECTNESS,
            confidence=1.5,
            claims=(opus_claim,),
            generation_started_at=NOW,
            generation_completed_at=NOW,
        )


def test_focus_tag_must_be_from_closed_vocabulary():
    opus_claim = _phase1_claim(ModelId.OPUS, "x")
    with pytest.raises(ValidationError):
        IndependentResponse(
            model_id=ModelId.OPUS,
            prompt_hash="h",
            response="r",
            focus_tag="vibes",  # type: ignore[arg-type]
            confidence=0.5,
            claims=(opus_claim,),
            generation_started_at=NOW,
            generation_completed_at=NOW,
        )


def test_session_rejects_cross_reading_with_unknown_agreement():
    opus_claim = _phase1_claim(ModelId.OPUS, "opus alpha")
    sonnet_claim = _phase1_claim(ModelId.SONNET, "sonnet alpha")
    bad_cr = CrossReading(
        reader_model=ModelId.OPUS,
        target_model=ModelId.SONNET,
        agreements=(ClaimRef(claim_id="ghost"),),
    )
    with pytest.raises(ValidationError, match="agrees with unknown claim_id"):
        Session(
            session_id="cr-ghost",
            prompt="p",
            prompt_hash="h",
            models_invited=(ModelId.OPUS, ModelId.SONNET),
            phase_1={
                ModelId.OPUS: _independent_response(ModelId.OPUS, "h", (opus_claim,)),
                ModelId.SONNET: _independent_response(ModelId.SONNET, "h", (sonnet_claim,)),
            },
            phase_2=(bad_cr,),
        )


def test_session_rejects_cross_reading_with_unknown_disagreement_target():
    opus_claim = _phase1_claim(ModelId.OPUS, "opus alpha")
    sonnet_claim = _phase1_claim(ModelId.SONNET, "sonnet alpha")
    bad_cr = CrossReading(
        reader_model=ModelId.OPUS,
        target_model=ModelId.SONNET,
        disagreements=(Disagreement(target_claim_id="ghost", reason="i disagree"),),
    )
    with pytest.raises(ValidationError, match="disagrees with unknown target_claim_id"):
        Session(
            session_id="cr-ghost-d",
            prompt="p",
            prompt_hash="h",
            models_invited=(ModelId.OPUS, ModelId.SONNET),
            phase_1={
                ModelId.OPUS: _independent_response(ModelId.OPUS, "h", (opus_claim,)),
                ModelId.SONNET: _independent_response(ModelId.SONNET, "h", (sonnet_claim,)),
            },
            phase_2=(bad_cr,),
        )


def test_session_accepts_cross_reading_with_resolved_claim_ids():
    opus_claim = _phase1_claim(ModelId.OPUS, "opus alpha")
    sonnet_claim = _phase1_claim(ModelId.SONNET, "sonnet alpha")
    cr = CrossReading(
        reader_model=ModelId.OPUS,
        target_model=ModelId.SONNET,
        agreements=(ClaimRef(claim_id=sonnet_claim.claim_id),),
        disagreements=(Disagreement(target_claim_id=sonnet_claim.claim_id, reason="r"),),
    )
    session = Session(
        session_id="cr-ok",
        prompt="p",
        prompt_hash="h",
        models_invited=(ModelId.OPUS, ModelId.SONNET),
        phase_1={
            ModelId.OPUS: _independent_response(ModelId.OPUS, "h", (opus_claim,)),
            ModelId.SONNET: _independent_response(ModelId.SONNET, "h", (sonnet_claim,)),
        },
        phase_2=(cr,),
    )
    assert len(session.phase_2) == 1


def test_session_resolves_cross_reading_against_phase_2_missing_claims():
    """A cross-reading can reference Phase 2 missing claims surfaced by another reader."""
    opus_claim = _phase1_claim(ModelId.OPUS, "opus alpha")
    sonnet_claim = _phase1_claim(ModelId.SONNET, "sonnet alpha")
    missing_text = "thing both missed"
    missing_claim = Claim(
        claim_id=claim_id_for(ModelId.SONNET, Phase.CROSS_READING, missing_text),
        source_model=ModelId.SONNET,
        source_phase=Phase.CROSS_READING,
        text=missing_text,
    )
    cr_sonnet_reads_opus = CrossReading(
        reader_model=ModelId.SONNET,
        target_model=ModelId.OPUS,
        missing=(missing_claim,),
    )
    cr_opus_agrees_with_missing = CrossReading(
        reader_model=ModelId.OPUS,
        target_model=ModelId.SONNET,
        agreements=(ClaimRef(claim_id=missing_claim.claim_id),),
    )
    session = Session(
        session_id="cr-resolve-p2",
        prompt="p",
        prompt_hash="h",
        models_invited=(ModelId.OPUS, ModelId.SONNET),
        phase_1={
            ModelId.OPUS: _independent_response(ModelId.OPUS, "h", (opus_claim,)),
            ModelId.SONNET: _independent_response(ModelId.SONNET, "h", (sonnet_claim,)),
        },
        phase_2=(cr_sonnet_reads_opus, cr_opus_agrees_with_missing),
    )
    assert len(session.phase_2) == 2
    assert len(session.all_claims()) == 3  # 2 phase 1 + 1 phase 2 missing


def test_dialogue_mixed_channels_each_capped_independently():
    """3 critique + 3 augment + 3 converge from one model is allowed; 4 on any single channel fails."""
    speaker = ModelId.OPUS
    base_turns = []
    for channel in ("critique", "augment", "converge"):
        for i in range(3):
            base_turns.append(
                DialogueTurn(
                    turn_id=f"{channel}_{i}",
                    speaker_model=speaker,
                    channel=channel,  # type: ignore[arg-type]
                    target_claim_id="some_id",
                    content=f"{channel} {i}",
                )
            )
    session = _build_minimal_session(phase_3=tuple(base_turns))
    assert len(session.phase_3) == 9


def test_synthesis_with_complete_trace_is_allowed():
    opus_claim = _phase1_claim(ModelId.OPUS, "opus claim")
    sonnet_claim = _phase1_claim(ModelId.SONNET, "sonnet claim")
    synthesis = SynthesisArtifact(
        output="final",
        claim_trace=(
            ClaimTraceEntry(claim_id=opus_claim.claim_id, disposition="present"),
            ClaimTraceEntry(
                claim_id=sonnet_claim.claim_id,
                disposition="modified",
                modified_text="sonnet claim, sharpened",
            ),
        ),
        synthesis_rules_applied=("rule_a",),
    )
    session = _build_minimal_session(phase_4=synthesis)
    assert session.phase_4 is not None
    assert len(session.phase_4.claim_trace) == 2


# --- SessionMetrics -------------------------------------------------------


def test_metrics_flag_parity_below_threshold():
    metrics = SessionMetrics(
        distinct_claim_share={ModelId.OPUS: 0.6, ModelId.SONNET: 0.3, ModelId.HAIKU: 0.1},
        edge_case_coverage_share={ModelId.OPUS: 0.4, ModelId.SONNET: 0.4, ModelId.HAIKU: 0.2},
        structural_pattern_share={ModelId.OPUS: 0.4, ModelId.SONNET: 0.4, ModelId.HAIKU: 0.2},
    )
    assert metrics.parity_below_threshold is True
    violations = metrics.parity_violations
    assert any(label == "distinct_claim_share" and model is ModelId.HAIKU for label, model, _ in violations)


def test_metrics_pass_when_above_threshold():
    metrics = SessionMetrics(
        distinct_claim_share={ModelId.OPUS: 0.4, ModelId.SONNET: 0.35, ModelId.HAIKU: 0.25},
        edge_case_coverage_share={ModelId.OPUS: 0.4, ModelId.SONNET: 0.35, ModelId.HAIKU: 0.25},
        structural_pattern_share={ModelId.OPUS: 0.4, ModelId.SONNET: 0.35, ModelId.HAIKU: 0.25},
    )
    assert metrics.parity_below_threshold is False
    assert PARITY_THRESHOLD == 0.15


def test_metrics_refuse_invalid_share():
    with pytest.raises(ValidationError, match="outside"):
        SessionMetrics(
            distinct_claim_share={ModelId.OPUS: 1.5},
            edge_case_coverage_share={},
            structural_pattern_share={},
        )
