"""Tests for Rule 1 (irreducibility preservation) — build_claim_trace.

Coverage shape:
  - Trace is total: every Phase 1 claim gets one entry, no duplicates, no missing.
  - Default disposition is "present".
  - Low-confidence-isolated heuristic: weakest_claim flagged by author AND no
    peer corroboration → omitted with closed-vocabulary reason.
  - Heuristic does NOT fire when self-flagged but peer corroborates.
  - Heuristic does NOT fire when no self-reflection artifact exists.
  - Omission reasons follow the closed internal vocabulary format.
  - Determinism: same Session in, same trace out, byte-equal across runs.
  - No side effects: build_claim_trace doesn't mutate the input session.
  - Edge cases: all-omitted session is valid; all-present session is valid.
"""

from datetime import datetime, timezone

import pytest

from golden_lattice.memory_graph.base import (
    FocusTag,
    ModelId,
    Phase,
    claim_id_for,
)
from golden_lattice.memory_graph.schema import (
    Claim,
    ClaimRef,
    CrossReading,
    Disagreement,
    IndependentResponse,
    SelfReflectionArtifact,
    Session,
)
from golden_lattice.synthesis.claim_trace import (
    OMISSION_REASON_PREFIXES,
    build_claim_trace,
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
    *,
    confidence: float = 0.7,
    self_reflection_artifacts: tuple[SelfReflectionArtifact, ...] = (),
) -> IndependentResponse:
    return IndependentResponse(
        model_id=model,
        prompt_hash="h",
        response="r",
        focus_tag=FocusTag.CORRECTNESS,
        confidence=confidence,
        claims=claims,
        self_reflection_artifacts=self_reflection_artifacts,
        generation_started_at=NOW,
        generation_completed_at=NOW,
    )


def _build_dyad_session(
    opus_claims: tuple[Claim, ...],
    sonnet_claims: tuple[Claim, ...],
    *,
    opus_response_kwargs: dict | None = None,
    sonnet_response_kwargs: dict | None = None,
    phase_2: tuple[CrossReading, ...] = (),
) -> Session:
    opus_kwargs = opus_response_kwargs or {}
    sonnet_kwargs = sonnet_response_kwargs or {}
    return Session(
        session_id="t",
        prompt="p",
        prompt_hash="h",
        models_invited=(ModelId.OPUS, ModelId.SONNET),
        phase_1={
            ModelId.OPUS: _response(ModelId.OPUS, opus_claims, **opus_kwargs),
            ModelId.SONNET: _response(ModelId.SONNET, sonnet_claims, **sonnet_kwargs),
        },
        phase_2=phase_2,
    )


# --- Totality + default disposition ---------------------------------------


def test_trace_covers_every_phase_1_claim_exactly_once():
    o1 = _claim(ModelId.OPUS, "opus a")
    o2 = _claim(ModelId.OPUS, "opus b")
    s1 = _claim(ModelId.SONNET, "sonnet a")
    session = _build_dyad_session((o1, o2), (s1,))
    trace = build_claim_trace(session)
    traced_ids = [e.claim_id for e in trace]
    assert sorted(traced_ids) == sorted([o1.claim_id, o2.claim_id, s1.claim_id])
    assert len(traced_ids) == len(set(traced_ids))


def test_trace_default_disposition_is_present():
    o1 = _claim(ModelId.OPUS, "opus a")
    s1 = _claim(ModelId.SONNET, "sonnet a")
    session = _build_dyad_session((o1,), (s1,))
    trace = build_claim_trace(session)
    for entry in trace:
        assert entry.disposition == "present"
        assert entry.modified_text is None
        assert entry.omission_reason is None


def test_trace_total_length_matches_phase_1_claim_count():
    claims_per_model = 3
    opus_claims = tuple(_claim(ModelId.OPUS, f"opus {i}") for i in range(claims_per_model))
    sonnet_claims = tuple(_claim(ModelId.SONNET, f"sonnet {i}") for i in range(claims_per_model))
    session = _build_dyad_session(opus_claims, sonnet_claims)
    trace = build_claim_trace(session)
    expected = len(opus_claims) + len(sonnet_claims)
    assert len(trace) == expected


# --- Low-confidence-isolated heuristic -----------------------------------


def test_low_confidence_isolated_omits_when_self_flagged_and_no_peer_agreement():
    o1 = _claim(ModelId.OPUS, "opus strong")
    o2 = _claim(ModelId.OPUS, "opus weak")
    reflection = SelfReflectionArtifact(
        model_id=ModelId.OPUS,
        generated_at=NOW,
        strongest_claim_id=o1.claim_id,
        weakest_claim_id=o2.claim_id,
        tag_justification="opus picked correctness because of opus_strong",
    )
    s1 = _claim(ModelId.SONNET, "sonnet a")
    session = _build_dyad_session(
        (o1, o2),
        (s1,),
        opus_response_kwargs={
            "confidence": 0.4,
            "self_reflection_artifacts": (reflection,),
        },
    )
    trace = build_claim_trace(session)
    by_id = {e.claim_id: e for e in trace}
    assert by_id[o1.claim_id].disposition == "present"
    assert by_id[o2.claim_id].disposition == "omitted"
    assert by_id[o2.claim_id].omission_reason == "low_confidence_isolated:0.4"


def test_low_confidence_isolated_does_not_omit_when_peer_corroborates():
    o1 = _claim(ModelId.OPUS, "opus strong")
    o2 = _claim(ModelId.OPUS, "opus weak")
    reflection = SelfReflectionArtifact(
        model_id=ModelId.OPUS,
        generated_at=NOW,
        strongest_claim_id=o1.claim_id,
        weakest_claim_id=o2.claim_id,
        tag_justification="j",
    )
    s1 = _claim(ModelId.SONNET, "sonnet a")
    # Sonnet reads Opus and agrees with both claims.
    sonnet_reads_opus = CrossReading(
        reader_model=ModelId.SONNET,
        target_model=ModelId.OPUS,
        agreements=(
            ClaimRef(claim_id=o1.claim_id),
            ClaimRef(claim_id=o2.claim_id),
        ),
    )
    session = _build_dyad_session(
        (o1, o2),
        (s1,),
        opus_response_kwargs={
            "confidence": 0.4,
            "self_reflection_artifacts": (reflection,),
        },
        phase_2=(sonnet_reads_opus,),
    )
    trace = build_claim_trace(session)
    by_id = {e.claim_id: e for e in trace}
    # Self-flagged weakest BUT peer corroborated → not isolated, not omitted.
    assert by_id[o2.claim_id].disposition == "present"


def test_low_confidence_isolated_does_not_omit_when_peer_disagrees():
    """Engagement-as-corroboration: a peer that disagreed with the claim
    is still engaging with it. Disagreement is what the Lattice exists to
    surface; treating it as equivalent to silence would let alignment-collapse
    patterns inflate omission rates against the consistently-dissented-with peer."""
    o1 = _claim(ModelId.OPUS, "opus strong")
    o2 = _claim(ModelId.OPUS, "opus weak")
    reflection = SelfReflectionArtifact(
        model_id=ModelId.OPUS,
        generated_at=NOW,
        strongest_claim_id=o1.claim_id,
        weakest_claim_id=o2.claim_id,
        tag_justification="j",
    )
    s1 = _claim(ModelId.SONNET, "sonnet a")
    # Sonnet reads Opus and *disagrees* with claim o2 (no agreement).
    sonnet_reads_opus = CrossReading(
        reader_model=ModelId.SONNET,
        target_model=ModelId.OPUS,
        agreements=(ClaimRef(claim_id=o1.claim_id),),
        disagreements=(
            Disagreement(target_claim_id=o2.claim_id, reason="i think this is wrong"),
        ),
    )
    session = _build_dyad_session(
        (o1, o2),
        (s1,),
        opus_response_kwargs={
            "confidence": 0.4,
            "self_reflection_artifacts": (reflection,),
        },
        phase_2=(sonnet_reads_opus,),
    )
    trace = build_claim_trace(session)
    by_id = {e.claim_id: e for e in trace}
    # o2 was self-flagged weakest, but Sonnet engaged with it via disagreement.
    # Engagement is corroboration — claim stays present, not omitted.
    assert by_id[o2.claim_id].disposition == "present"


# --- Two-peer dispute → modified (Rule 1 v1 expansion) ------------------


def _build_triad_session(
    *,
    opus_claims: tuple[Claim, ...] = (),
    sonnet_claims: tuple[Claim, ...] = (),
    haiku_claims: tuple[Claim, ...] = (),
    opus_response_kwargs: dict | None = None,
    phase_2: tuple[CrossReading, ...] = (),
) -> Session:
    """N=3 session helper. Used by tests of the strict-triadic dispute rule."""
    ok = opus_response_kwargs or {}
    return Session(
        session_id="t",
        prompt="p",
        prompt_hash="h",
        models_invited=(ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU),
        phase_1={
            ModelId.OPUS: _response(ModelId.OPUS, opus_claims, **ok),
            ModelId.SONNET: _response(ModelId.SONNET, sonnet_claims),
            ModelId.HAIKU: _response(ModelId.HAIKU, haiku_claims),
        },
        phase_2=phase_2,
    )


def test_two_peer_dispute_marks_claim_modified_with_hedge():
    """When BOTH non-author peers cross-read disagree with a claim, the
    trace marks it 'modified' and modified_text contains the original
    claim text plus a templated hedge naming both disputers. Symmetric
    counter to §6's strict-triadic consensus rule: both-non-author-peer
    dispute is the strict-disagreement signal Rule 1 surfaces inline so
    the audit doesn't get dropped at the synthesis seam."""
    opus_disputed = _claim(ModelId.OPUS, "the 88x number is real")
    sonnet_reads = CrossReading(
        reader_model=ModelId.SONNET,
        target_model=ModelId.OPUS,
        disagreements=(
            Disagreement(
                target_claim_id=opus_disputed.claim_id,
                reason="secondary blog source, not primary research.",
            ),
        ),
    )
    haiku_reads = CrossReading(
        reader_model=ModelId.HAIKU,
        target_model=ModelId.OPUS,
        disagreements=(
            Disagreement(
                target_claim_id=opus_disputed.claim_id,
                reason="implausible at frontier-lab scale.",
            ),
        ),
    )
    session = _build_triad_session(
        opus_claims=(opus_disputed,),
        sonnet_claims=(_claim(ModelId.SONNET, "filler"),),
        haiku_claims=(_claim(ModelId.HAIKU, "filler"),),
        phase_2=(sonnet_reads, haiku_reads),
    )
    trace = build_claim_trace(session)
    by_id = {e.claim_id: e for e in trace}
    entry = by_id[opus_disputed.claim_id]
    assert entry.disposition == "modified"
    assert entry.modified_text is not None
    # Original claim text is preserved at the start of the modified text.
    assert entry.modified_text.startswith(opus_disputed.text)
    # Hedge surfaces the dispute marker and both peers' attributions.
    assert "DISPUTED" in entry.modified_text
    assert ModelId.SONNET.value in entry.modified_text
    assert ModelId.HAIKU.value in entry.modified_text


def test_single_peer_dispute_keeps_claim_present():
    """One peer disagrees, the other is silent → not the strict-triadic
    signal. Claim stays present, no modification."""
    opus_claim = _claim(ModelId.OPUS, "contested by only one peer")
    sonnet_reads = CrossReading(
        reader_model=ModelId.SONNET,
        target_model=ModelId.OPUS,
        disagreements=(
            Disagreement(
                target_claim_id=opus_claim.claim_id,
                reason="i don't buy it",
            ),
        ),
    )
    # Haiku is silent — no cross-reading targeting Opus.
    session = _build_triad_session(
        opus_claims=(opus_claim,),
        sonnet_claims=(_claim(ModelId.SONNET, "filler"),),
        haiku_claims=(_claim(ModelId.HAIKU, "filler"),),
        phase_2=(sonnet_reads,),
    )
    trace = build_claim_trace(session)
    by_id = {e.claim_id: e for e in trace}
    assert by_id[opus_claim.claim_id].disposition == "present"


def test_no_dispute_keeps_claim_present_in_triad():
    """No peer disagrees → present, the v0 default. Verifies the new rule
    doesn't accidentally fire on unanimous-or-silent peers."""
    opus_claim = _claim(ModelId.OPUS, "uncontroversial")
    session = _build_triad_session(
        opus_claims=(opus_claim,),
        sonnet_claims=(_claim(ModelId.SONNET, "filler"),),
        haiku_claims=(_claim(ModelId.HAIKU, "filler"),),
    )
    trace = build_claim_trace(session)
    by_id = {e.claim_id: e for e in trace}
    assert by_id[opus_claim.claim_id].disposition == "present"


def test_two_peer_dispute_modification_is_deterministic_under_reordering():
    """Same disagreement inputs → byte-equal modified_text regardless of
    the order phase_2 cross-readings are stored in. Peer order in the
    hedge is sorted by model_id.value for determinism."""
    opus_claim = _claim(ModelId.OPUS, "claim text")
    sonnet_reads = CrossReading(
        reader_model=ModelId.SONNET, target_model=ModelId.OPUS,
        disagreements=(Disagreement(
            target_claim_id=opus_claim.claim_id, reason="sonnet's objection."
        ),),
    )
    haiku_reads = CrossReading(
        reader_model=ModelId.HAIKU, target_model=ModelId.OPUS,
        disagreements=(Disagreement(
            target_claim_id=opus_claim.claim_id, reason="haiku's objection."
        ),),
    )
    s1 = _build_triad_session(
        opus_claims=(opus_claim,),
        sonnet_claims=(_claim(ModelId.SONNET, "filler"),),
        haiku_claims=(_claim(ModelId.HAIKU, "filler"),),
        phase_2=(sonnet_reads, haiku_reads),
    )
    s2 = _build_triad_session(
        opus_claims=(opus_claim,),
        sonnet_claims=(_claim(ModelId.SONNET, "filler"),),
        haiku_claims=(_claim(ModelId.HAIKU, "filler"),),
        phase_2=(haiku_reads, sonnet_reads),
    )
    t1 = {e.claim_id: e for e in build_claim_trace(s1)}
    t2 = {e.claim_id: e for e in build_claim_trace(s2)}
    assert (
        t1[opus_claim.claim_id].modified_text
        == t2[opus_claim.claim_id].modified_text
    )


def test_two_peer_dispute_is_triadic_only():
    """N=2 has only one non-author peer; both-peers-disagreed cannot apply.
    Existing dyad behavior is preserved — single-peer disagreement keeps
    the claim present, no modification fires."""
    o1 = _claim(ModelId.OPUS, "claim")
    s1 = _claim(ModelId.SONNET, "other")
    sonnet_reads = CrossReading(
        reader_model=ModelId.SONNET, target_model=ModelId.OPUS,
        disagreements=(Disagreement(
            target_claim_id=o1.claim_id, reason="objection."
        ),),
    )
    session = _build_dyad_session((o1,), (s1,), phase_2=(sonnet_reads,))
    trace = build_claim_trace(session)
    by_id = {e.claim_id: e for e in trace}
    assert by_id[o1.claim_id].disposition == "present"


def test_low_confidence_isolated_does_not_fire_without_self_reflection():
    """No self-reflection artifact means no weakest_claim flagging — claim stays present."""
    o1 = _claim(ModelId.OPUS, "opus a")
    s1 = _claim(ModelId.SONNET, "sonnet a")
    session = _build_dyad_session((o1,), (s1,))  # no self_reflection_artifacts
    trace = build_claim_trace(session)
    by_id = {e.claim_id: e for e in trace}
    assert by_id[o1.claim_id].disposition == "present"


# --- Omission reason format ----------------------------------------------


def test_omission_reasons_follow_closed_internal_vocabulary():
    o1 = _claim(ModelId.OPUS, "strong")
    o2 = _claim(ModelId.OPUS, "weak")
    reflection = SelfReflectionArtifact(
        model_id=ModelId.OPUS,
        generated_at=NOW,
        strongest_claim_id=o1.claim_id,
        weakest_claim_id=o2.claim_id,
        tag_justification="j",
    )
    s1 = _claim(ModelId.SONNET, "sonnet a")
    session = _build_dyad_session(
        (o1, o2),
        (s1,),
        opus_response_kwargs={
            "confidence": 0.3,
            "self_reflection_artifacts": (reflection,),
        },
    )
    trace = build_claim_trace(session)
    for entry in trace:
        if entry.disposition == "omitted":
            assert entry.omission_reason is not None
            matches_prefix = any(
                entry.omission_reason.startswith(prefix)
                for prefix in OMISSION_REASON_PREFIXES
            )
            assert matches_prefix, (
                f"omission_reason {entry.omission_reason!r} does not match any "
                f"prefix in OMISSION_REASON_PREFIXES."
            )
            assert ":" in entry.omission_reason


# --- Edge cases ----------------------------------------------------------


def test_session_with_partial_omissions_keeps_trace_total():
    """Partial-omission session: the v0 heuristic only omits each model's
    self-flagged-weakest with no peer corroboration. Strongest stays present.
    Trace is total either way."""
    o1 = _claim(ModelId.OPUS, "opus first")
    o2 = _claim(ModelId.OPUS, "opus second")
    s1 = _claim(ModelId.SONNET, "sonnet first")
    s2 = _claim(ModelId.SONNET, "sonnet second")
    o_reflection = SelfReflectionArtifact(
        model_id=ModelId.OPUS,
        generated_at=NOW,
        strongest_claim_id=o1.claim_id,
        weakest_claim_id=o2.claim_id,
        tag_justification="j",
    )
    s_reflection = SelfReflectionArtifact(
        model_id=ModelId.SONNET,
        generated_at=NOW,
        strongest_claim_id=s1.claim_id,
        weakest_claim_id=s2.claim_id,
        tag_justification="j",
    )
    session = _build_dyad_session(
        (o1, o2),
        (s1, s2),
        opus_response_kwargs={
            "confidence": 0.4,
            "self_reflection_artifacts": (o_reflection,),
        },
        sonnet_response_kwargs={
            "confidence": 0.4,
            "self_reflection_artifacts": (s_reflection,),
        },
    )
    trace = build_claim_trace(session)
    by_id = {e.claim_id: e for e in trace}
    # Strongest claims stay present.
    assert by_id[o1.claim_id].disposition == "present"
    assert by_id[s1.claim_id].disposition == "present"
    # Each model's self-flagged-weakest with no peer corroboration → omitted.
    assert by_id[o2.claim_id].disposition == "omitted"
    assert by_id[s2.claim_id].disposition == "omitted"
    # Trace is total — every Phase 1 claim is accounted for.
    assert {e.claim_id for e in trace} == {
        o1.claim_id, o2.claim_id, s1.claim_id, s2.claim_id
    }


def test_session_with_no_omissions_is_valid():
    """Vanilla session with no self-reflection artifacts → all-present trace."""
    o1 = _claim(ModelId.OPUS, "a")
    s1 = _claim(ModelId.SONNET, "b")
    session = _build_dyad_session((o1,), (s1,))
    trace = build_claim_trace(session)
    assert all(e.disposition == "present" for e in trace)


# --- Determinism + no side effects ---------------------------------------


def test_build_claim_trace_is_deterministic():
    """Pure function. Same Session in, same trace out, byte-equal across runs."""
    o1 = _claim(ModelId.OPUS, "a")
    o2 = _claim(ModelId.OPUS, "b")
    s1 = _claim(ModelId.SONNET, "c")
    reflection = SelfReflectionArtifact(
        model_id=ModelId.OPUS,
        generated_at=NOW,
        strongest_claim_id=o1.claim_id,
        weakest_claim_id=o2.claim_id,
        tag_justification="j",
    )
    session = _build_dyad_session(
        (o1, o2),
        (s1,),
        opus_response_kwargs={
            "confidence": 0.4,
            "self_reflection_artifacts": (reflection,),
        },
    )
    trace_a = build_claim_trace(session)
    trace_b = build_claim_trace(session)
    assert trace_a == trace_b


def test_build_claim_trace_does_not_mutate_session():
    """No side effects on input. Rule independence — when other rules call
    build_claim_trace between their own runs, this rule must leave session unchanged."""
    o1 = _claim(ModelId.OPUS, "a")
    s1 = _claim(ModelId.SONNET, "b")
    session = _build_dyad_session((o1,), (s1,))
    snapshot_before = session.model_dump_json()
    build_claim_trace(session)
    snapshot_after = session.model_dump_json()
    assert snapshot_before == snapshot_after


# --- Trace-as-irreducibility-preservation property ------------------------


def test_trace_passes_substrate_irreducibility_refusal_when_folded_into_session():
    """Closure test: trace flows through into a substrate-validated SynthesisArtifact
    and a complete Session containing it. Positive case for the substrate's negative
    refusal at _synthesis_traces_every_phase_1_claim. End-to-end."""
    from golden_lattice.memory_graph.base import SynthesisRule
    from golden_lattice.memory_graph.schema import SynthesisArtifact

    o1 = _claim(ModelId.OPUS, "a")
    s1 = _claim(ModelId.SONNET, "b")
    session = _build_dyad_session((o1,), (s1,))
    trace = build_claim_trace(session)
    synthesis = SynthesisArtifact(
        output="o",
        claim_trace=trace,
        synthesis_rules_applied=(SynthesisRule.IRREDUCIBILITY_PRESERVATION,),
    )
    full_session = Session(
        session_id=session.session_id,
        prompt=session.prompt,
        prompt_hash=session.prompt_hash,
        models_invited=session.models_invited,
        phase_1=session.phase_1,
        phase_4=synthesis,
    )
    assert full_session.phase_4 is synthesis
    # Trace is preserved through Session construction.
    assert full_session.phase_4.claim_trace == trace
    # Every Phase 1 claim has a trace entry in the folded session.
    phase_1_claim_ids = {
        c.claim_id for r in full_session.phase_1.values() for c in r.claims
    }
    traced_ids = {e.claim_id for e in full_session.phase_4.claim_trace}
    assert phase_1_claim_ids == traced_ids


def test_trace_with_omissions_passes_substrate_irreducibility_refusal_when_folded():
    """Partial-omission variant of the closure test: trace with a mix of present
    and omitted dispositions still satisfies the substrate's totality requirement."""
    from golden_lattice.memory_graph.base import SynthesisRule
    from golden_lattice.memory_graph.schema import SynthesisArtifact

    o1 = _claim(ModelId.OPUS, "strong")
    o2 = _claim(ModelId.OPUS, "weak")
    reflection = SelfReflectionArtifact(
        model_id=ModelId.OPUS,
        generated_at=NOW,
        strongest_claim_id=o1.claim_id,
        weakest_claim_id=o2.claim_id,
        tag_justification="j",
    )
    s1 = _claim(ModelId.SONNET, "sonnet a")
    session = _build_dyad_session(
        (o1, o2),
        (s1,),
        opus_response_kwargs={
            "confidence": 0.3,
            "self_reflection_artifacts": (reflection,),
        },
    )
    trace = build_claim_trace(session)
    # Mixed dispositions: o1 present, o2 omitted, s1 present.
    dispositions = {e.claim_id: e.disposition for e in trace}
    assert dispositions[o1.claim_id] == "present"
    assert dispositions[o2.claim_id] == "omitted"
    assert dispositions[s1.claim_id] == "present"

    synthesis = SynthesisArtifact(
        output="o",
        claim_trace=trace,
        synthesis_rules_applied=(SynthesisRule.IRREDUCIBILITY_PRESERVATION,),
    )
    full_session = Session(
        session_id=session.session_id,
        prompt=session.prompt,
        prompt_hash=session.prompt_hash,
        models_invited=session.models_invited,
        phase_1=session.phase_1,
        phase_4=synthesis,
    )
    # Mixed-disposition trace still satisfies substrate's totality validator.
    assert full_session.phase_4.claim_trace == trace
