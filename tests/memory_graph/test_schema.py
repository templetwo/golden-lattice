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
    Elevation,
    FocusTag,
    IndependentResponse,
    ModelId,
    Phase,
    SelfReflectionArtifact,
    Session,
    SessionMetrics,
    SurfacedDisagreement,
    SynthesisArtifact,
    SynthesisRule,
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


def _critique_turn(i: int, speaker: ModelId, target: ModelId, claim_id: str) -> DialogueTurn:
    return DialogueTurn(
        turn_id=f"crit_{speaker.value}_{target.value}_{i}",
        speaker_model=speaker,
        channel="critique",
        target_model=target,
        target_claim_ids=(claim_id,),
        content=f"critique {i}",
    )


def test_dialogue_critique_per_peer_cap_refuses_four_against_one_peer():
    # _build_minimal_session has opus_claim and sonnet_claim.
    sonnet_claim = _phase1_claim(ModelId.SONNET, "sonnet claim")
    turns = tuple(
        _critique_turn(i, ModelId.OPUS, ModelId.SONNET, sonnet_claim.claim_id)
        for i in range(4)
    )
    with pytest.raises(ValidationError, match="critique cap exceeded"):
        _build_minimal_session(phase_3=turns)


def test_dialogue_critique_three_against_one_peer_allowed():
    sonnet_claim = _phase1_claim(ModelId.SONNET, "sonnet claim")
    turns = tuple(
        _critique_turn(i, ModelId.OPUS, ModelId.SONNET, sonnet_claim.claim_id)
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
        synthesis_rules_applied=(SynthesisRule.IRREDUCIBILITY_PRESERVATION,),
    )
    with pytest.raises(ValidationError, match="Irreducibility preservation violated"):
        _build_minimal_session(phase_4=incomplete_synthesis)


def test_self_reflection_strongest_must_differ_from_weakest():
    with pytest.raises(ValidationError, match="must differ"):
        SelfReflectionArtifact(
            model_id=ModelId.OPUS,
            generated_at=NOW,
            strongest_claim_id="abc",
            weakest_claim_id="abc",
            tag_justification="because reasons",
        )


def test_self_reflection_requires_non_empty_justification():
    with pytest.raises(ValidationError, match="non-empty"):
        SelfReflectionArtifact(
            model_id=ModelId.OPUS,
            generated_at=NOW,
            strongest_claim_id="abc",
            weakest_claim_id="def",
            tag_justification="   ",
        )


def test_independent_response_rejects_self_reflection_referencing_unknown_claim():
    own_claim = _phase1_claim(ModelId.OPUS, "alpha")
    bad_reflection = SelfReflectionArtifact(
        model_id=ModelId.OPUS,
        generated_at=NOW,
        strongest_claim_id=own_claim.claim_id,
        weakest_claim_id="ghost",
        tag_justification="i picked correctness because of X",
    )
    with pytest.raises(ValidationError, match="weakest_claim_id"):
        IndependentResponse(
            model_id=ModelId.OPUS,
            prompt_hash="h",
            response="r",
            focus_tag=FocusTag.CORRECTNESS,
            confidence=0.7,
            claims=(own_claim,),
            self_reflection_artifacts=(bad_reflection,),
            generation_started_at=NOW,
            generation_completed_at=NOW,
        )


def test_independent_response_accepts_well_formed_self_reflection():
    claim_a = _phase1_claim(ModelId.OPUS, "alpha")
    claim_b = _phase1_claim(ModelId.OPUS, "beta")
    reflection = SelfReflectionArtifact(
        model_id=ModelId.OPUS,
        generated_at=NOW,
        strongest_claim_id=claim_a.claim_id,
        weakest_claim_id=claim_b.claim_id,
        tag_justification="alpha is the strongest because X; beta is weaker because Y",
    )
    resp = IndependentResponse(
        model_id=ModelId.OPUS,
        prompt_hash="h",
        response="r",
        focus_tag=FocusTag.CORRECTNESS,
        confidence=0.7,
        claims=(claim_a, claim_b),
        self_reflection_artifacts=(reflection,),
        generation_started_at=NOW,
        generation_completed_at=NOW,
    )
    assert len(resp.self_reflection_artifacts) == 1
    assert resp.self_reflection_artifacts[0].strongest_claim_id == claim_a.claim_id


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
    """3 critique (vs Sonnet) + 3 augment + 3 converge from Opus is allowed."""
    sonnet_claim = _phase1_claim(ModelId.SONNET, "sonnet claim")
    speaker = ModelId.OPUS
    base_turns: list[DialogueTurn] = []
    for i in range(3):
        base_turns.append(
            _critique_turn(i, speaker, ModelId.SONNET, sonnet_claim.claim_id)
        )
    for i in range(3):
        base_turns.append(
            DialogueTurn(
                turn_id=f"aug_{i}",
                speaker_model=speaker,
                channel="augment",
                content=f"augment {i}",
            )
        )
    for i in range(3):
        base_turns.append(
            DialogueTurn(
                turn_id=f"con_{i}",
                speaker_model=speaker,
                channel="converge",
                content=f"converge {i}",
            )
        )
    session = _build_minimal_session(phase_3=tuple(base_turns))
    assert len(session.phase_3) == 9


def test_dialogue_critique_six_against_two_peers_allowed():
    """Per-spec: critique cap is per-peer. 3 vs Sonnet + 3 vs Haiku from Opus = 6 total, allowed."""
    # Triadic session needed for two distinct critique targets.
    opus_claim = _phase1_claim(ModelId.OPUS, "opus claim")
    sonnet_claim = _phase1_claim(ModelId.SONNET, "sonnet claim")
    haiku_claim = _phase1_claim(ModelId.HAIKU, "haiku claim")
    turns: list[DialogueTurn] = []
    for i in range(3):
        turns.append(_critique_turn(i, ModelId.OPUS, ModelId.SONNET, sonnet_claim.claim_id))
    for i in range(3):
        turns.append(_critique_turn(i, ModelId.OPUS, ModelId.HAIKU, haiku_claim.claim_id))
    session = Session(
        session_id="triad-critique",
        prompt="p",
        prompt_hash="h",
        models_invited=(ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU),
        phase_1={
            ModelId.OPUS: _independent_response(ModelId.OPUS, "h", (opus_claim,)),
            ModelId.SONNET: _independent_response(ModelId.SONNET, "h", (sonnet_claim,)),
            ModelId.HAIKU: _independent_response(ModelId.HAIKU, "h", (haiku_claim,)),
        },
        phase_3=tuple(turns),
    )
    assert len(session.phase_3) == 6


def test_critique_without_target_model_refused():
    """Empty target_claim_ids isolates the critique-specific check from the
    'target_claim_ids without target_model' general coherence check."""
    with pytest.raises(ValidationError, match="critique channel requires target_model"):
        DialogueTurn(
            turn_id="t1",
            speaker_model=ModelId.OPUS,
            channel="critique",
            target_claim_ids=(),
            content="c",
        )


def test_critique_with_empty_target_claim_ids_refused():
    with pytest.raises(ValidationError, match="critique channel requires non-empty target_claim_ids"):
        DialogueTurn(
            turn_id="t1",
            speaker_model=ModelId.OPUS,
            channel="critique",
            target_model=ModelId.SONNET,
            target_claim_ids=(),
            content="c",
        )


def test_target_model_equals_speaker_refused():
    with pytest.raises(ValidationError, match="cannot be its own target_model"):
        DialogueTurn(
            turn_id="t1",
            speaker_model=ModelId.OPUS,
            channel="augment",
            target_model=ModelId.OPUS,
            content="c",
        )


def test_target_claim_ids_without_target_model_refused():
    with pytest.raises(ValidationError, match="cannot be specified without target_model"):
        DialogueTurn(
            turn_id="t1",
            speaker_model=ModelId.OPUS,
            channel="augment",
            target_claim_ids=("some_id",),
            content="c",
        )


def test_dialogue_target_claim_ids_must_resolve():
    sonnet_claim = _phase1_claim(ModelId.SONNET, "sonnet claim")
    bad_turn = DialogueTurn(
        turn_id="t1",
        speaker_model=ModelId.OPUS,
        channel="critique",
        target_model=ModelId.SONNET,
        target_claim_ids=("ghost",),
        content="c",
    )
    with pytest.raises(ValidationError, match="unknown target_claim_id"):
        _build_minimal_session(phase_3=(bad_turn,))


def test_augment_with_target_model_and_empty_claim_ids_allowed():
    """Augment can name a peer (general statement about their position) without specific claim refs."""
    turn = DialogueTurn(
        turn_id="t1",
        speaker_model=ModelId.OPUS,
        channel="augment",
        target_model=ModelId.SONNET,
        target_claim_ids=(),
        content="sonnet's framing needs more on X",
    )
    session = _build_minimal_session(phase_3=(turn,))
    assert len(session.phase_3) == 1


def test_augment_aggregate_cap_three_all_targeting_one_peer_allowed():
    """Augment cap is aggregate: 3 augments all targeting Sonnet from Opus is allowed."""
    turns = tuple(
        DialogueTurn(
            turn_id=f"aug_{i}",
            speaker_model=ModelId.OPUS,
            channel="augment",
            target_model=ModelId.SONNET,
            content=f"augment {i}",
        )
        for i in range(3)
    )
    session = _build_minimal_session(phase_3=turns)
    assert len(session.phase_3) == 3


def test_augment_aggregate_cap_three_mixed_targets_allowed():
    """3 augments with mixed targets (Sonnet, Sonnet, None) is allowed under the aggregate cap."""
    turns = (
        DialogueTurn(
            turn_id="aug_0",
            speaker_model=ModelId.OPUS,
            channel="augment",
            target_model=ModelId.SONNET,
            content="a0",
        ),
        DialogueTurn(
            turn_id="aug_1",
            speaker_model=ModelId.OPUS,
            channel="augment",
            content="a1",
        ),
        DialogueTurn(
            turn_id="aug_2",
            speaker_model=ModelId.OPUS,
            channel="augment",
            target_model=ModelId.SONNET,
            content="a2",
        ),
    )
    session = _build_minimal_session(phase_3=turns)
    assert len(session.phase_3) == 3


def test_augment_four_aggregate_refused_regardless_of_target_distribution():
    """Augment cap is aggregate. 4 turns from Opus across mixed targets must be refused."""
    turns = (
        DialogueTurn(
            turn_id="aug_0",
            speaker_model=ModelId.OPUS,
            channel="augment",
            target_model=ModelId.SONNET,
            content="a0",
        ),
        DialogueTurn(
            turn_id="aug_1",
            speaker_model=ModelId.OPUS,
            channel="augment",
            content="a1",
        ),
        DialogueTurn(
            turn_id="aug_2",
            speaker_model=ModelId.OPUS,
            channel="augment",
            target_model=ModelId.SONNET,
            content="a2",
        ),
        DialogueTurn(
            turn_id="aug_3",
            speaker_model=ModelId.OPUS,
            channel="augment",
            content="a3",
        ),
    )
    with pytest.raises(ValidationError, match="augment cap exceeded"):
        _build_minimal_session(phase_3=turns)


def test_converge_aggregate_cap_three_all_targeting_one_peer_allowed():
    turns = tuple(
        DialogueTurn(
            turn_id=f"con_{i}",
            speaker_model=ModelId.OPUS,
            channel="converge",
            target_model=ModelId.SONNET,
            content=f"converge {i}",
        )
        for i in range(3)
    )
    session = _build_minimal_session(phase_3=turns)
    assert len(session.phase_3) == 3


def test_converge_aggregate_cap_three_mixed_targets_allowed():
    turns = (
        DialogueTurn(
            turn_id="con_0",
            speaker_model=ModelId.OPUS,
            channel="converge",
            target_model=ModelId.SONNET,
            content="c0",
        ),
        DialogueTurn(
            turn_id="con_1",
            speaker_model=ModelId.OPUS,
            channel="converge",
            content="c1",
        ),
        DialogueTurn(
            turn_id="con_2",
            speaker_model=ModelId.OPUS,
            channel="converge",
            target_model=ModelId.SONNET,
            content="c2",
        ),
    )
    session = _build_minimal_session(phase_3=turns)
    assert len(session.phase_3) == 3


def test_converge_four_aggregate_refused_regardless_of_target_distribution():
    turns = tuple(
        DialogueTurn(
            turn_id=f"con_{i}",
            speaker_model=ModelId.OPUS,
            channel="converge",
            target_model=ModelId.SONNET if i % 2 == 0 else None,
            content=f"c{i}",
        )
        for i in range(4)
    )
    with pytest.raises(ValidationError, match="converge cap exceeded"):
        _build_minimal_session(phase_3=turns)


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
        synthesis_rules_applied=(SynthesisRule.IRREDUCIBILITY_PRESERVATION,),
    )
    session = _build_minimal_session(phase_4=synthesis)
    assert session.phase_4 is not None
    assert len(session.phase_4.claim_trace) == 2


def test_synthesis_rules_applied_rejects_unknown_rule():
    with pytest.raises(ValidationError):
        SynthesisArtifact(
            output="final",
            claim_trace=(),
            synthesis_rules_applied=("invented_rule",),  # type: ignore[arg-type]
        )


# --- Elevation -----------------------------------------------------------


def test_elevation_requires_at_least_two_converge_turn_ids():
    with pytest.raises(ValidationError, match="at least 2 converge_turn_ids"):
        Elevation(claim_ids=("c1",), converge_turn_ids=("t1",))


def test_elevation_requires_non_empty_claim_ids():
    with pytest.raises(ValidationError, match="at least one claim_id"):
        Elevation(claim_ids=(), converge_turn_ids=("t1", "t2"))


def test_elevation_rejects_duplicate_converge_turn_ids():
    with pytest.raises(ValidationError, match="must be distinct"):
        Elevation(claim_ids=("c1",), converge_turn_ids=("t1", "t1"))


def test_session_rejects_elevation_with_unknown_claim_id():
    opus_claim = _phase1_claim(ModelId.OPUS, "opus claim")
    sonnet_claim = _phase1_claim(ModelId.SONNET, "sonnet claim")
    converge_a = DialogueTurn(
        turn_id="conv_a",
        speaker_model=ModelId.OPUS,
        channel="converge",
        content="aligned",
    )
    converge_b = DialogueTurn(
        turn_id="conv_b",
        speaker_model=ModelId.SONNET,
        channel="converge",
        content="aligned",
    )
    bad_elev = Elevation(claim_ids=("ghost",), converge_turn_ids=("conv_a", "conv_b"))
    synthesis = SynthesisArtifact(
        output="o",
        claim_trace=(
            ClaimTraceEntry(claim_id=opus_claim.claim_id, disposition="present"),
            ClaimTraceEntry(claim_id=sonnet_claim.claim_id, disposition="present"),
        ),
        synthesis_rules_applied=(SynthesisRule.AGREEMENT_ELEVATION,),
        elevations=(bad_elev,),
    )
    with pytest.raises(ValidationError, match="Elevation cites unknown claim_id"):
        _build_minimal_session(phase_3=(converge_a, converge_b), phase_4=synthesis)


def test_session_rejects_elevation_citing_non_converge_turn():
    opus_claim = _phase1_claim(ModelId.OPUS, "opus claim")
    sonnet_claim = _phase1_claim(ModelId.SONNET, "sonnet claim")
    converge_turn = DialogueTurn(
        turn_id="conv_a",
        speaker_model=ModelId.OPUS,
        channel="converge",
        content="aligned",
    )
    augment_turn = DialogueTurn(
        turn_id="aug_a",
        speaker_model=ModelId.SONNET,
        channel="augment",
        content="adding",
    )
    bad_elev = Elevation(
        claim_ids=(opus_claim.claim_id,),
        converge_turn_ids=("conv_a", "aug_a"),
    )
    synthesis = SynthesisArtifact(
        output="o",
        claim_trace=(
            ClaimTraceEntry(claim_id=opus_claim.claim_id, disposition="present"),
            ClaimTraceEntry(claim_id=sonnet_claim.claim_id, disposition="present"),
        ),
        synthesis_rules_applied=(SynthesisRule.AGREEMENT_ELEVATION,),
        elevations=(bad_elev,),
    )
    with pytest.raises(ValidationError, match="not a Phase 3 turn with channel='converge'"):
        _build_minimal_session(phase_3=(converge_turn, augment_turn), phase_4=synthesis)


def test_session_rejects_elevation_with_only_one_distinct_speaker():
    """Self-elevation refusal: 2 converge turns from one speaker do NOT constitute
    cross-model agreement. Invariant 1 (no authority gradient) at the synthesis layer."""
    opus_claim = _phase1_claim(ModelId.OPUS, "opus claim")
    sonnet_claim = _phase1_claim(ModelId.SONNET, "sonnet claim")
    opus_conv_a = DialogueTurn(
        turn_id="opus_conv_a",
        speaker_model=ModelId.OPUS,
        channel="converge",
        content="i agree",
    )
    opus_conv_b = DialogueTurn(
        turn_id="opus_conv_b",
        speaker_model=ModelId.OPUS,
        channel="converge",
        content="and again",
    )
    bad_elev = Elevation(
        claim_ids=(opus_claim.claim_id,),
        converge_turn_ids=("opus_conv_a", "opus_conv_b"),
    )
    synthesis = SynthesisArtifact(
        output="o",
        claim_trace=(
            ClaimTraceEntry(claim_id=opus_claim.claim_id, disposition="present"),
            ClaimTraceEntry(claim_id=sonnet_claim.claim_id, disposition="present"),
        ),
        synthesis_rules_applied=(SynthesisRule.AGREEMENT_ELEVATION,),
        elevations=(bad_elev,),
    )
    with pytest.raises(ValidationError, match="at least 2 distinct"):
        _build_minimal_session(phase_3=(opus_conv_a, opus_conv_b), phase_4=synthesis)


def test_session_accepts_well_formed_elevation_with_two_distinct_speakers():
    opus_claim = _phase1_claim(ModelId.OPUS, "opus claim")
    sonnet_claim = _phase1_claim(ModelId.SONNET, "sonnet claim")
    converge_opus = DialogueTurn(
        turn_id="conv_opus",
        speaker_model=ModelId.OPUS,
        channel="converge",
        content="aligned",
    )
    converge_sonnet = DialogueTurn(
        turn_id="conv_sonnet",
        speaker_model=ModelId.SONNET,
        channel="converge",
        content="aligned",
    )
    elev = Elevation(
        claim_ids=(opus_claim.claim_id,),
        converge_turn_ids=("conv_opus", "conv_sonnet"),
    )
    synthesis = SynthesisArtifact(
        output="o",
        claim_trace=(
            ClaimTraceEntry(claim_id=opus_claim.claim_id, disposition="present"),
            ClaimTraceEntry(claim_id=sonnet_claim.claim_id, disposition="present"),
        ),
        synthesis_rules_applied=(
            SynthesisRule.IRREDUCIBILITY_PRESERVATION,
            SynthesisRule.AGREEMENT_ELEVATION,
        ),
        elevations=(elev,),
    )
    session = _build_minimal_session(
        phase_3=(converge_opus, converge_sonnet), phase_4=synthesis
    )
    assert session.phase_4 is not None
    assert len(session.phase_4.elevations) == 1


# --- SurfacedDisagreement ------------------------------------------------


def test_surfaced_disagreement_requires_at_least_two_claim_ids():
    with pytest.raises(ValidationError, match="at least 2 claim_ids"):
        SurfacedDisagreement(claim_ids=("c1",), note="conflict")


def test_surfaced_disagreement_requires_non_empty_note():
    with pytest.raises(ValidationError, match="note must be non-empty"):
        SurfacedDisagreement(claim_ids=("c1", "c2"), note="   ")


def test_session_rejects_surfaced_disagreement_citing_unknown_claim_id():
    opus_claim = _phase1_claim(ModelId.OPUS, "opus claim")
    sonnet_claim = _phase1_claim(ModelId.SONNET, "sonnet claim")
    bad_sd = SurfacedDisagreement(
        claim_ids=(opus_claim.claim_id, "ghost"),
        note="conflict on framing",
    )
    synthesis = SynthesisArtifact(
        output="o",
        claim_trace=(
            ClaimTraceEntry(claim_id=opus_claim.claim_id, disposition="present"),
            ClaimTraceEntry(claim_id=sonnet_claim.claim_id, disposition="present"),
        ),
        synthesis_rules_applied=(SynthesisRule.DISAGREEMENT_SURFACING,),
        surfaced_disagreements=(bad_sd,),
    )
    with pytest.raises(ValidationError, match="SurfacedDisagreement cites unknown claim_id"):
        _build_minimal_session(phase_4=synthesis)


def test_session_accepts_well_formed_surfaced_disagreement():
    opus_claim = _phase1_claim(ModelId.OPUS, "opus claim")
    sonnet_claim = _phase1_claim(ModelId.SONNET, "sonnet claim")
    sd = SurfacedDisagreement(
        claim_ids=(opus_claim.claim_id, sonnet_claim.claim_id),
        note="opus and sonnet diverge on the LRU vs LFU question",
    )
    synthesis = SynthesisArtifact(
        output="o",
        claim_trace=(
            ClaimTraceEntry(claim_id=opus_claim.claim_id, disposition="present"),
            ClaimTraceEntry(claim_id=sonnet_claim.claim_id, disposition="present"),
        ),
        synthesis_rules_applied=(SynthesisRule.DISAGREEMENT_SURFACING,),
        surfaced_disagreements=(sd,),
    )
    session = _build_minimal_session(phase_4=synthesis)
    assert session.phase_4 is not None
    assert len(session.phase_4.surfaced_disagreements) == 1


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
