"""A complete, small session with known values, for dashboard tests.

Every count the normalize and record tests assert on is fixed here. See the
dashboard redesign plan, Task 4 step 1, for the content this encodes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
    Elevation,
    IndependentResponse,
    SelfReflectionArtifact,
    Session,
    SessionMetrics,
    SurfacedDisagreement,
    SynthesisArtifact,
)

NOW = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)


def _claim(model: ModelId, text: str, phase: Phase = Phase.INDEPENDENT) -> Claim:
    return Claim(
        claim_id=claim_id_for(model, phase, text),
        source_model=model,
        source_phase=phase,
        text=text,
    )


def _response(model: ModelId, claims: tuple[Claim, ...]) -> IndependentResponse:
    return IndependentResponse(
        model_id=model,
        prompt_hash="h",
        response="prose",
        focus_tag=FocusTag.CORRECTNESS,
        confidence=0.8,
        claims=claims,
        self_reflection_artifacts=(
            SelfReflectionArtifact(
                model_id=model,
                generated_at=NOW + timedelta(seconds=1.5),
                strongest_claim_id=claims[0].claim_id,
                weakest_claim_id=claims[1].claim_id,
                tag_justification="why this tag",
            ),
        ),
        generation_started_at=NOW,
        generation_completed_at=NOW + timedelta(seconds=1),
    )


def make_full_session(
    *,
    legacy: bool = False,
    persist_metrics: bool = True,
    dyad: bool = False,
    session_id: str = "session_20260504_120000_abcd1234",
    created_offset_days: int = 0,
    shared_text: bool = False,
    unattached: bool = False,
    incomplete: bool = False,
) -> Session:
    """shared_text: Haiku's second claim reuses the text "opus one".
    unattached: adds a critique aimed only at a Phase 2 missing claim and a
    second surfaced disagreement naming "sonnet one".
    incomplete: no Phase 4 (a run that died before synthesis)."""
    if legacy:
        O, S, H = ModelId.LEGACY_OPUS_4_7, ModelId.LEGACY_SONNET_4_6, ModelId.LEGACY_HAIKU_4_5
    else:
        O, S, H = ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU

    o1, o2 = _claim(O, "opus one"), _claim(O, "opus two")
    s1, s2 = _claim(S, "sonnet one"), _claim(S, "sonnet two")
    h1, h2 = _claim(H, "haiku one"), _claim(H, "opus one" if shared_text else "haiku two")
    missing = _claim(S, "sonnet says opus missed this", Phase.CROSS_READING)

    s_reads_o = CrossReading(
        reader_model=S, target_model=O,
        agreements=(ClaimRef(claim_id=o2.claim_id),),
        disagreements=(Disagreement(target_claim_id=o1.claim_id, reason="sonnet disagrees with opus one"),),
        missing=(missing,),
    )
    o_reads_s = CrossReading(
        reader_model=O, target_model=S,
        disagreements=(Disagreement(target_claim_id=s1.claim_id, reason="opus disagrees with sonnet one"),),
    )
    t1 = DialogueTurn(turn_id="t1", speaker_model=O, channel="critique", target_model=S,
                      target_claim_ids=(s1.claim_id,), content="opus critiques sonnet one in dialogue")
    t5 = DialogueTurn(turn_id="t5", speaker_model=S, channel="augment", content="sonnet adds")

    if dyad:
        return Session(
            session_id=session_id, prompt="p", prompt_hash="h",
            created_at=NOW + timedelta(days=created_offset_days),
            models_invited=(O, S),
            phase_1={O: _response(O, (o1, o2)), S: _response(S, (s1, s2))},
            phase_2=(s_reads_o, o_reads_s),
            phase_3=(t1, t5),
            phase_4=SynthesisArtifact(
                output="\n".join(["[O] opus one", "[O] opus two", "[S] sonnet one"]),
                claim_trace=(
                    ClaimTraceEntry(claim_id=o1.claim_id, disposition="present"),
                    ClaimTraceEntry(claim_id=o2.claim_id, disposition="present"),
                    ClaimTraceEntry(claim_id=s1.claim_id, disposition="present"),
                    ClaimTraceEntry(claim_id=s2.claim_id, disposition="omitted",
                                    omission_reason="redundant with opus two"),
                ),
                synthesis_rules_applied=tuple(SynthesisRule),
            ),
            metrics=None,
        )

    phase_2 = (
        s_reads_o,
        CrossReading(reader_model=H, target_model=O,
                     agreements=(ClaimRef(claim_id=o1.claim_id), ClaimRef(claim_id=o2.claim_id))),
        o_reads_s,
        CrossReading(reader_model=H, target_model=S,
                     disagreements=(Disagreement(target_claim_id=s1.claim_id, reason="haiku disagrees with sonnet one"),)),
        CrossReading(reader_model=O, target_model=H, agreements=(ClaimRef(claim_id=h1.claim_id),)),
        CrossReading(reader_model=S, target_model=H,
                     agreements=(ClaimRef(claim_id=h1.claim_id), ClaimRef(claim_id=h2.claim_id))),
    )
    phase_3 = (
        t1,
        DialogueTurn(turn_id="t2", speaker_model=H, channel="critique", target_model=O,
                     target_claim_ids=(o2.claim_id,), content="haiku critiques opus two"),
        DialogueTurn(turn_id="t3", speaker_model=S, channel="converge", target_model=O,
                     target_claim_ids=(o2.claim_id,), content="agree"),
        DialogueTurn(turn_id="t4", speaker_model=H, channel="converge", target_model=O,
                     target_claim_ids=(o2.claim_id,), content="agree too"),
        t5,
    ) + ((DialogueTurn(turn_id="t6", speaker_model=H, channel="critique", target_model=S,
                       target_claim_ids=(missing.claim_id,), content="haiku critiques a missing claim"),)
         if unattached else ())
    hedged = (
        f"sonnet one [DISPUTED — {O.value}: 'opus disagrees with sonnet one'; "
        f"{H.value}: 'haiku disagrees with sonnet one']"
    )
    output = "\n".join(["[O] opus one", "[O] opus two", f"[S] {hedged}", "[H] haiku one", f"[H] {h2.text}"])
    metrics = (
        SessionMetrics(
            distinct_claim_share={O: 0.34, S: 0.33, H: 0.33},
            edge_case_coverage_share={O: 0.5, S: 0.1, H: 0.4},
            structural_pattern_share={O: 0.4, S: 0.3, H: 0.3},
        )
        if persist_metrics
        else None
    )
    return Session(
        session_id=session_id, prompt="p", prompt_hash="h",
        created_at=NOW + timedelta(days=created_offset_days),
        models_invited=(O, S, H),
        phase_1={O: _response(O, (o1, o2)), S: _response(S, (s1, s2)), H: _response(H, (h1, h2))},
        phase_2=phase_2,
        phase_3=phase_3,
        phase_4=SynthesisArtifact(
            output=output,
            claim_trace=(
                ClaimTraceEntry(claim_id=o1.claim_id, disposition="present"),
                ClaimTraceEntry(claim_id=o2.claim_id, disposition="present"),
                ClaimTraceEntry(claim_id=s1.claim_id, disposition="modified", modified_text=hedged),
                ClaimTraceEntry(claim_id=s2.claim_id, disposition="omitted",
                                omission_reason="redundant with opus two"),
                ClaimTraceEntry(claim_id=h1.claim_id, disposition="present"),
                ClaimTraceEntry(claim_id=h2.claim_id, disposition="present"),
            ),
            synthesis_rules_applied=tuple(SynthesisRule),
            elevations=(Elevation(claim_ids=(o2.claim_id,), converge_turn_ids=("t3", "t4")),),
            surfaced_disagreements=(
                SurfacedDisagreement(claim_ids=(s1.claim_id, o1.claim_id), note="opus and haiku both push back on sonnet one"),
            ) + ((SurfacedDisagreement(claim_ids=(s1.claim_id, o2.claim_id), note="a second note on sonnet one"),)
                 if unattached else ()),
        ) if not incomplete else None,
        metrics=metrics,
    )
