"""Tests for Rule 2 (agreement elevation) — compute_elevations."""

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
    ClaimTraceEntry,
    DialogueTurn,
    IndependentResponse,
    Session,
    SynthesisArtifact,
)
from golden_lattice.synthesis.elevation import compute_elevations


NOW = datetime(2026, 5, 3, 12, 0, 0, tzinfo=timezone.utc)


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
        response="r",
        focus_tag=FocusTag.CORRECTNESS,
        confidence=0.7,
        claims=claims,
        generation_started_at=NOW,
        generation_completed_at=NOW,
    )


def _converge(
    turn_id: str,
    speaker: ModelId,
    target_claim_ids: tuple[str, ...] = (),
    target_model: ModelId | None = None,
) -> DialogueTurn:
    """Helper. Substrate requires target_model when target_claim_ids is non-empty
    and refuses target_model == speaker_model. Caller supplies target_model
    explicitly when claim refs are present."""
    return DialogueTurn(
        turn_id=turn_id,
        speaker_model=speaker,
        channel="converge",
        target_model=target_model,
        target_claim_ids=target_claim_ids,
        content="aligned",
    )


def _triad_session(
    phase_3: tuple[DialogueTurn, ...] = (),
) -> tuple[Session, dict[ModelId, Claim]]:
    claims = {
        ModelId.OPUS: _claim(ModelId.OPUS, "opus alpha"),
        ModelId.SONNET: _claim(ModelId.SONNET, "sonnet alpha"),
        ModelId.HAIKU: _claim(ModelId.HAIKU, "haiku alpha"),
    }
    session = Session(
        session_id="t",
        prompt="p",
        prompt_hash="h",
        models_invited=(ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU),
        phase_1={m: _response(m, (c,)) for m, c in claims.items()},
        phase_3=phase_3,
    )
    return session, claims


# --- Basic positive case --------------------------------------------------


def test_two_converge_turns_from_distinct_speakers_on_shared_claim_produce_elevation():
    _, claims = _triad_session()
    opus_id = claims[ModelId.OPUS].claim_id
    # Sonnet and Haiku each converge on Opus's claim (substrate: target_model
    # required when target_claim_ids non-empty; target_model != speaker).
    sonnet_converge = _converge("c_sonnet", ModelId.SONNET, (opus_id,), target_model=ModelId.OPUS)
    haiku_converge = _converge("c_haiku", ModelId.HAIKU, (opus_id,), target_model=ModelId.OPUS)
    session, _ = _triad_session(phase_3=(sonnet_converge, haiku_converge))
    elevations = compute_elevations(session)
    assert len(elevations) == 1
    elev = elevations[0]
    assert opus_id in elev.claim_ids
    assert set(elev.converge_turn_ids) == {"c_sonnet", "c_haiku"}


# --- Edge cases the audit named explicitly --------------------------------


def test_three_distinct_speakers_on_one_claim_produce_one_elevation_not_three():
    """Three speakers all converging on Sonnet's claim. Substrate refuses
    target_model == speaker, so we converge on Sonnet's claim from Opus + Haiku
    (and use a different speaker pattern for the third)."""
    _, claims = _triad_session()
    sonnet_id = claims[ModelId.SONNET].claim_id
    opus_id = claims[ModelId.OPUS].claim_id
    # Three converge turns on Sonnet's claim from Opus, Haiku, and Sonnet
    # (Sonnet can converge on someone else's claim citing Sonnet's id).
    # Wait — Sonnet can't target Sonnet. So the three speakers must all be
    # non-Sonnet. Use Opus's claim instead so Sonnet, Haiku, AND a third
    # distinct speaker would need to exist. With 3 invited models, only 2
    # distinct non-Opus speakers exist. So this test is bounded to 2 speakers
    # in a triad — the "three distinct speakers on one claim" requires N≥4.
    # Restate: in a triad, max distinct speakers converging on one peer's
    # claim is 2. The "all three distinct speakers" case here uses three
    # separate claims that all happen to be in one peer's response, with
    # union-merge bringing them together.
    o = _converge("c_opus", ModelId.OPUS, (sonnet_id,), target_model=ModelId.SONNET)
    h = _converge("c_haiku", ModelId.HAIKU, (sonnet_id,), target_model=ModelId.SONNET)
    # A third converge from Opus on Sonnet's claim (same claim, same speaker,
    # but a separate turn). Speaker set is still {Opus, Haiku} → 2 distinct.
    o2 = _converge("c_opus_2", ModelId.OPUS, (sonnet_id,), target_model=ModelId.SONNET)
    session, _ = _triad_session(phase_3=(o, h, o2))
    elevations = compute_elevations(session)
    assert len(elevations) == 1
    elev = elevations[0]
    assert set(elev.converge_turn_ids) == {"c_opus", "c_haiku", "c_opus_2"}
    assert sonnet_id in elev.claim_ids


def test_two_distinct_speakers_with_non_overlapping_claim_ids_produce_no_elevation():
    """Per-spec ('agreement on a claim'): two unrelated convergences are NOT one elevation."""
    _, claims = _triad_session()
    opus_id = claims[ModelId.OPUS].claim_id
    sonnet_id = claims[ModelId.SONNET].claim_id
    # Sonnet converges on Opus's claim; Opus converges on Sonnet's claim.
    # Different claims → no overlap → no merge → each group has 1 speaker → no elevation.
    sonnet_converge = _converge("c_sonnet", ModelId.SONNET, (opus_id,), target_model=ModelId.OPUS)
    opus_converge = _converge("c_opus", ModelId.OPUS, (sonnet_id,), target_model=ModelId.SONNET)
    session, _ = _triad_session(phase_3=(sonnet_converge, opus_converge))
    elevations = compute_elevations(session)
    assert elevations == ()


# --- Negative cases -------------------------------------------------------


def test_general_converge_turns_without_target_claim_ids_are_not_consumed():
    """v0 staging: general-converge turns do not feed Rule 2."""
    s = _converge("c_sonnet", ModelId.SONNET)  # no target_claim_ids
    h = _converge("c_haiku", ModelId.HAIKU)
    session, _ = _triad_session(phase_3=(s, h))
    assert compute_elevations(session) == ()


def test_single_speaker_two_converges_on_same_claim_no_elevation():
    """Two converge turns from one speaker (both targeting another peer's claim)
    do NOT constitute cross-model agreement. Need ≥2 distinct speakers."""
    _, claims = _triad_session()
    sonnet_id = claims[ModelId.SONNET].claim_id
    o1 = _converge("c_o1", ModelId.OPUS, (sonnet_id,), target_model=ModelId.SONNET)
    o2 = _converge("c_o2", ModelId.OPUS, (sonnet_id,), target_model=ModelId.SONNET)
    session, _ = _triad_session(phase_3=(o1, o2))
    assert compute_elevations(session) == ()


def test_single_converge_turn_does_not_produce_elevation():
    _, claims = _triad_session()
    opus_id = claims[ModelId.OPUS].claim_id
    s = _converge("c_sonnet", ModelId.SONNET, (opus_id,), target_model=ModelId.OPUS)
    session, _ = _triad_session(phase_3=(s,))
    assert compute_elevations(session) == ()


def test_mixed_batch_anchored_and_general_converges_only_anchored_contribute():
    """Live-data shape: a Phase 3 with both anchored and general converges
    produces elevations only from the anchored subset. General converges
    remain in phase_3 as dialogue and do not pollute the elevation set.

    Pins the v0 staging boundary under realistic mixed conditions — the shape
    real models will produce after the converge-channel prompt revision invites
    two-form self-classification (claim-specific vs general). If this test
    fails after a future change, the v0 staging boundary has been crossed and
    the change must be deliberate, not accidental.
    """
    _, claims = _triad_session()
    opus_id = claims[ModelId.OPUS].claim_id

    # Anchored: Sonnet and Haiku both converge on Opus's claim.
    sonnet_anchored = _converge(
        "c_s_anchored", ModelId.SONNET, (opus_id,), target_model=ModelId.OPUS
    )
    haiku_anchored = _converge(
        "c_h_anchored", ModelId.HAIKU, (opus_id,), target_model=ModelId.OPUS
    )
    # General: same speakers also emit untargeted-general converges in the
    # same Phase 3 (substrate cap is 3 aggregate per speaker; 2 each is fine).
    sonnet_general = _converge("c_s_general", ModelId.SONNET)
    haiku_general = _converge("c_h_general", ModelId.HAIKU)

    session, _ = _triad_session(
        phase_3=(sonnet_anchored, sonnet_general, haiku_anchored, haiku_general)
    )
    elevations = compute_elevations(session)

    assert len(elevations) == 1
    elev = elevations[0]
    assert opus_id in elev.claim_ids
    assert set(elev.converge_turn_ids) == {"c_s_anchored", "c_h_anchored"}
    assert "c_s_general" not in elev.converge_turn_ids
    assert "c_h_general" not in elev.converge_turn_ids


def test_non_converge_channels_are_not_consumed():
    """Critique and augment turns are not converge — even if they target the same claim."""
    _, claims = _triad_session()
    opus_id = claims[ModelId.OPUS].claim_id
    sonnet_critique = DialogueTurn(
        turn_id="crit_s",
        speaker_model=ModelId.SONNET,
        channel="critique",
        target_model=ModelId.OPUS,
        target_claim_ids=(opus_id,),
        content="i critique opus",
    )
    haiku_augment = DialogueTurn(
        turn_id="aug_h",
        speaker_model=ModelId.HAIKU,
        channel="augment",
        target_model=ModelId.OPUS,
        target_claim_ids=(opus_id,),
        content="i augment",
    )
    session, _ = _triad_session(phase_3=(sonnet_critique, haiku_augment))
    assert compute_elevations(session) == ()


# --- Transitive grouping --------------------------------------------------


def test_transitive_overlap_merges_groups():
    """If turn A cites {X, Y} and turn B cites {Y, Z}, they share Y → one group with {X, Y, Z}.
    target_model on each turn must be a non-self peer that authored at least one
    of the cited claims. Sonnet citing {opus_id, sonnet_id}: Sonnet can't target
    Sonnet, so target_model must be Opus (who authored opus_id). For union-find
    to verify across turns, target_model identity doesn't matter — only
    speaker_model identity and the claim_id sets feed elevation logic."""
    _, claims = _triad_session()
    opus_id = claims[ModelId.OPUS].claim_id
    sonnet_id = claims[ModelId.SONNET].claim_id
    haiku_id = claims[ModelId.HAIKU].claim_id

    # Substrate constraint: target_model must differ from speaker. We pick a
    # target_model that owns one of the cited claims; for elevation purposes
    # it's informational only.
    a = _converge("c_a", ModelId.SONNET, (opus_id, sonnet_id), target_model=ModelId.OPUS)
    b = _converge("c_b", ModelId.HAIKU, (sonnet_id, haiku_id), target_model=ModelId.SONNET)
    c = _converge("c_c", ModelId.OPUS, (haiku_id,), target_model=ModelId.HAIKU)
    session, _ = _triad_session(phase_3=(a, b, c))
    elevations = compute_elevations(session)
    assert len(elevations) == 1
    elev = elevations[0]
    assert set(elev.converge_turn_ids) == {"c_a", "c_b", "c_c"}
    assert set(elev.claim_ids) == {opus_id, sonnet_id, haiku_id}


# --- Determinism + no side effects ----------------------------------------


def test_compute_elevations_is_deterministic():
    _, claims = _triad_session()
    opus_id = claims[ModelId.OPUS].claim_id
    sonnet_id = claims[ModelId.SONNET].claim_id
    a = _converge("c_a", ModelId.SONNET, (opus_id,), target_model=ModelId.OPUS)
    b = _converge("c_b", ModelId.HAIKU, (opus_id,), target_model=ModelId.OPUS)
    c = _converge("c_c", ModelId.OPUS, (sonnet_id,), target_model=ModelId.SONNET)
    d = _converge("c_d", ModelId.HAIKU, (sonnet_id,), target_model=ModelId.SONNET)
    session, _ = _triad_session(phase_3=(a, b, c, d))
    out_a = compute_elevations(session)
    out_b = compute_elevations(session)
    assert out_a == out_b


def test_compute_elevations_does_not_mutate_session():
    _, claims = _triad_session()
    opus_id = claims[ModelId.OPUS].claim_id
    s = _converge("c_s", ModelId.SONNET, (opus_id,), target_model=ModelId.OPUS)
    h = _converge("c_h", ModelId.HAIKU, (opus_id,), target_model=ModelId.OPUS)
    session, _ = _triad_session(phase_3=(s, h))
    snapshot_before = session.model_dump_json()
    compute_elevations(session)
    snapshot_after = session.model_dump_json()
    assert snapshot_before == snapshot_after


def test_compute_elevations_independent_of_build_claim_trace():
    """Rule independence: build_claim_trace called between two compute_elevations
    calls does not change compute_elevations output."""
    from golden_lattice.synthesis.claim_trace import build_claim_trace

    _, claims = _triad_session()
    opus_id = claims[ModelId.OPUS].claim_id
    s = _converge("c_s", ModelId.SONNET, (opus_id,), target_model=ModelId.OPUS)
    h = _converge("c_h", ModelId.HAIKU, (opus_id,), target_model=ModelId.OPUS)
    session, _ = _triad_session(phase_3=(s, h))
    out_first = compute_elevations(session)
    build_claim_trace(session)  # exercise Rule 1 between Rule 2 calls
    out_second = compute_elevations(session)
    assert out_first == out_second


# --- Substrate-refusal closure --------------------------------------------


def test_v0_staging_artifact_claim_can_be_both_omitted_by_rule_1_and_elevated_by_rule_2():
    """Documented v0 inconsistency: Rule 1's omission heuristic doesn't yet consume
    Rule 2's output. The same claim CAN appear as omitted (low_confidence_isolated)
    in claim_trace AND as part of an Elevation in elevations.

    This is structural in v0 because rules compose independently. v1 composition
    will resolve the inconsistency (a claim part of an Elevation should not be
    marked low_confidence_isolated — engagement-as-corroboration extends to
    elevation participation).

    The test exists as a marker so v1 has to acknowledge it's fixing a known
    state rather than silently changing behavior. Documented exceptions are
    different from undocumented bugs.
    """
    from golden_lattice.memory_graph.schema import (
        Claim,
        IndependentResponse,
        SelfReflectionArtifact,
    )
    from golden_lattice.synthesis.claim_trace import build_claim_trace

    # Build a claim that will be:
    #   - flagged as low-confidence-isolated by Rule 1 (author's weakest, no peer
    #     agreement, no peer disagreement either — fully unengaged via Phase 2)
    #   - elevated by Rule 2 via Phase 3 converge turns from 2 distinct peers
    o_strong = _claim(ModelId.OPUS, "opus strong claim")
    o_weak_but_elevated = _claim(ModelId.OPUS, "opus weak claim that gets elevated")
    s1 = _claim(ModelId.SONNET, "sonnet alpha")
    h1 = _claim(ModelId.HAIKU, "haiku alpha")
    opus_reflection = SelfReflectionArtifact(
        model_id=ModelId.OPUS,
        generated_at=NOW,
        strongest_claim_id=o_strong.claim_id,
        weakest_claim_id=o_weak_but_elevated.claim_id,
        tag_justification="opus strong is strongest; weak is weakest",
    )

    # Phase 3: Sonnet and Haiku both converge on Opus's weak claim.
    sonnet_converge = _converge(
        "c_s", ModelId.SONNET, (o_weak_but_elevated.claim_id,), target_model=ModelId.OPUS
    )
    haiku_converge = _converge(
        "c_h", ModelId.HAIKU, (o_weak_but_elevated.claim_id,), target_model=ModelId.OPUS
    )

    session = Session(
        session_id="t",
        prompt="p",
        prompt_hash="h",
        models_invited=(ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU),
        phase_1={
            ModelId.OPUS: IndependentResponse(
                model_id=ModelId.OPUS,
                prompt_hash="h",
                response="r",
                focus_tag=FocusTag.CORRECTNESS,
                confidence=0.3,
                claims=(o_strong, o_weak_but_elevated),
                self_reflection_artifacts=(opus_reflection,),
                generation_started_at=NOW,
                generation_completed_at=NOW,
            ),
            ModelId.SONNET: _response(ModelId.SONNET, (s1,)),
            ModelId.HAIKU: _response(ModelId.HAIKU, (h1,)),
        },
        # Phase 2 deliberately empty — no peer engagement via cross-reading on
        # o_weak_but_elevated. Phase 3 converge is the only engagement signal,
        # and v0 Rule 1 doesn't consume Phase 3.
        phase_3=(sonnet_converge, haiku_converge),
    )

    trace = build_claim_trace(session)
    elevations = compute_elevations(session)

    # Rule 1 (v0): the weak claim has no Phase 2 engagement → marked omitted.
    by_id = {e.claim_id: e for e in trace}
    assert by_id[o_weak_but_elevated.claim_id].disposition == "omitted"
    assert by_id[o_weak_but_elevated.claim_id].omission_reason.startswith(
        "low_confidence_isolated:"
    )

    # Rule 2 (v0): the same claim has 2-distinct-speaker converge agreement → elevated.
    assert len(elevations) == 1
    assert o_weak_but_elevated.claim_id in elevations[0].claim_ids

    # The inconsistency: same claim is both "omitted-because-isolated" AND
    # "elevated-because-cross-model-agreement" within the same v0 synthesis.
    # v1 composition resolves this. v0 documents it.


def test_elevations_flow_through_substrate_validators_when_folded_into_session():
    """Closure: an Elevation produced by compute_elevations satisfies
    Session._synthesis_elevations_well_formed when folded into a SynthesisArtifact
    and complete Session."""
    session_p1, claims = _triad_session()
    opus_id = claims[ModelId.OPUS].claim_id
    sonnet_converge = _converge("c_sonnet", ModelId.SONNET, (opus_id,), target_model=ModelId.OPUS)
    haiku_converge = _converge("c_haiku", ModelId.HAIKU, (opus_id,), target_model=ModelId.OPUS)
    session, _ = _triad_session(phase_3=(sonnet_converge, haiku_converge))

    elevations = compute_elevations(session)
    assert len(elevations) == 1

    # Build claim_trace (Rule 1) so the synthesis is irreducibility-complete.
    from golden_lattice.synthesis.claim_trace import build_claim_trace

    trace = build_claim_trace(session)
    synthesis = SynthesisArtifact(
        output="o",
        claim_trace=trace,
        synthesis_rules_applied=(
            SynthesisRule.IRREDUCIBILITY_PRESERVATION,
            SynthesisRule.AGREEMENT_ELEVATION,
        ),
        elevations=elevations,
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
    assert len(full_session.phase_4.elevations) == 1
