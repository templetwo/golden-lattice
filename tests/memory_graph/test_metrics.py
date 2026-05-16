"""Tests for pure-function metrics over a tagged Session."""

from datetime import datetime, timezone

import pytest

from golden_lattice.memory_graph.base import FocusTag, ModelId, Phase, claim_id_for
from golden_lattice.memory_graph.metrics import (
    EDGE_CASE_DIMENSION,
    STRUCTURAL_PATTERN_DIMENSION,
    compute_consensus_pair_distribution,
    compute_consensus_pair_skew,
    compute_consensus_tags,
    compute_dimension_consensus,
    compute_parity_shares,
    contested_peer_tags,
    contested_self_tags,
    interpret_parity_flags,
)
from golden_lattice.memory_graph.schema import (
    Claim,
    IndependentResponse,
    Session,
)
from golden_lattice.memory_graph.tagging import (
    ClaimTags,
    EdgeCaseTag,
    Phase2Tagging,
    StructuralPatternTag,
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


def _triad_session(
    phase_2_taggings: tuple[Phase2Tagging, ...] = (),
) -> tuple[Session, dict[ModelId, Claim]]:
    claims = {
        ModelId.OPUS: _phase1_claim(ModelId.OPUS, "opus alpha"),
        ModelId.SONNET: _phase1_claim(ModelId.SONNET, "sonnet beta"),
        ModelId.HAIKU: _phase1_claim(ModelId.HAIKU, "haiku gamma"),
    }
    session = Session(
        session_id="triad",
        prompt="p",
        prompt_hash="h",
        models_invited=(ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU),
        phase_1={m: _independent_response(m, "h", (c,)) for m, c in claims.items()},
        phase_2_taggings=phase_2_taggings,
    )
    return session, claims


def _dyad_session() -> tuple[Session, dict[ModelId, Claim]]:
    claims = {
        ModelId.OPUS: _phase1_claim(ModelId.OPUS, "opus alpha"),
        ModelId.SONNET: _phase1_claim(ModelId.SONNET, "sonnet beta"),
    }
    session = Session(
        session_id="dyad",
        prompt="p",
        prompt_hash="h",
        models_invited=(ModelId.OPUS, ModelId.SONNET),
        phase_1={m: _independent_response(m, "h", (c,)) for m, c in claims.items()},
    )
    return session, claims


# --- compute_consensus_tags ----------------------------------------------


def test_consensus_emits_when_two_peers_agree_on_subtype():
    _, claims = _triad_session()
    opus_id = claims[ModelId.OPUS].claim_id
    sonnet_tagging = Phase2Tagging(
        tagger_model=ModelId.SONNET,
        peer_tags=(
            ClaimTags(claim_id=opus_id, edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,)),
        ),
    )
    haiku_tagging = Phase2Tagging(
        tagger_model=ModelId.HAIKU,
        peer_tags=(
            ClaimTags(claim_id=opus_id, edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,)),
        ),
    )
    session, _ = _triad_session(phase_2_taggings=(sonnet_tagging, haiku_tagging))
    consensus = compute_consensus_tags(session)
    assert len(consensus) == 1
    ct = consensus[0]
    assert ct.claim_id == opus_id
    assert ct.dimension == EDGE_CASE_DIMENSION
    assert ct.tag_value == EdgeCaseTag.BOUNDARY_CONDITION.value
    assert set(ct.consensus_voters) == {ModelId.SONNET, ModelId.HAIKU}


def test_consensus_does_not_emit_for_single_peer_tag():
    _, claims = _triad_session()
    opus_id = claims[ModelId.OPUS].claim_id
    sonnet_tagging = Phase2Tagging(
        tagger_model=ModelId.SONNET,
        peer_tags=(
            ClaimTags(claim_id=opus_id, edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,)),
        ),
    )
    session, _ = _triad_session(phase_2_taggings=(sonnet_tagging,))
    assert compute_consensus_tags(session) == ()


def test_other_tag_is_excluded_from_consensus():
    _, claims = _triad_session()
    opus_id = claims[ModelId.OPUS].claim_id
    s = Phase2Tagging(
        tagger_model=ModelId.SONNET,
        peer_tags=(ClaimTags(claim_id=opus_id, edge_case_tags=(EdgeCaseTag.OTHER,)),),
    )
    h = Phase2Tagging(
        tagger_model=ModelId.HAIKU,
        peer_tags=(ClaimTags(claim_id=opus_id, edge_case_tags=(EdgeCaseTag.OTHER,)),),
    )
    session, _ = _triad_session(phase_2_taggings=(s, h))
    assert compute_consensus_tags(session) == ()


def test_self_tags_do_not_count_toward_consensus():
    _, claims = _triad_session()
    opus_id = claims[ModelId.OPUS].claim_id
    opus_self = Phase2Tagging(
        tagger_model=ModelId.OPUS,
        self_tags=(ClaimTags(claim_id=opus_id, edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,)),),
    )
    sonnet = Phase2Tagging(
        tagger_model=ModelId.SONNET,
        peer_tags=(ClaimTags(claim_id=opus_id, edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,)),),
    )
    session, _ = _triad_session(phase_2_taggings=(opus_self, sonnet))
    assert compute_consensus_tags(session) == ()


# --- compute_dimension_consensus -----------------------------------------


def test_dimension_consensus_emits_even_when_subtypes_differ():
    """The key advantage of dimension-level consensus: subtype disagreement shouldn't silence dimension agreement."""
    _, claims = _triad_session()
    opus_id = claims[ModelId.OPUS].claim_id
    sonnet = Phase2Tagging(
        tagger_model=ModelId.SONNET,
        peer_tags=(ClaimTags(claim_id=opus_id, edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,)),),
    )
    haiku = Phase2Tagging(
        tagger_model=ModelId.HAIKU,
        peer_tags=(ClaimTags(claim_id=opus_id, edge_case_tags=(EdgeCaseTag.FAILURE_MODE,)),),
    )
    session, _ = _triad_session(phase_2_taggings=(sonnet, haiku))

    subtype_consensus = compute_consensus_tags(session)
    assert len(subtype_consensus) == 0  # subtypes differ

    dim_consensus = compute_dimension_consensus(session)
    assert len(dim_consensus) == 1
    assert dim_consensus[0].claim_id == opus_id
    assert dim_consensus[0].dimension == EDGE_CASE_DIMENSION
    assert set(dim_consensus[0].consensus_voters) == {ModelId.SONNET, ModelId.HAIKU}


def test_dimension_consensus_excludes_pure_other_tags():
    _, claims = _triad_session()
    opus_id = claims[ModelId.OPUS].claim_id
    s = Phase2Tagging(
        tagger_model=ModelId.SONNET,
        peer_tags=(ClaimTags(claim_id=opus_id, edge_case_tags=(EdgeCaseTag.OTHER,)),),
    )
    h = Phase2Tagging(
        tagger_model=ModelId.HAIKU,
        peer_tags=(ClaimTags(claim_id=opus_id, edge_case_tags=(EdgeCaseTag.OTHER,)),),
    )
    session, _ = _triad_session(phase_2_taggings=(s, h))
    assert compute_dimension_consensus(session) == ()


# --- compute_parity_shares -----------------------------------------------


def test_parity_shares_returns_none_for_dyad():
    session, _ = _dyad_session()
    assert compute_parity_shares(session) is None


def test_parity_shares_for_triad_returns_metrics():
    _, claims = _triad_session()
    opus_id = claims[ModelId.OPUS].claim_id
    sonnet_id = claims[ModelId.SONNET].claim_id
    haiku_id = claims[ModelId.HAIKU].claim_id

    sonnet = Phase2Tagging(
        tagger_model=ModelId.SONNET,
        peer_tags=(ClaimTags(claim_id=opus_id, edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,)),),
    )
    haiku = Phase2Tagging(
        tagger_model=ModelId.HAIKU,
        peer_tags=(ClaimTags(claim_id=opus_id, edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,)),),
    )
    session, _ = _triad_session(phase_2_taggings=(sonnet, haiku))
    metrics = compute_parity_shares(session)
    assert metrics is not None
    # All three Phase 1 claims are attributed equally — distinct shares ~0.33 each
    for m in (ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU):
        assert abs(metrics.distinct_claim_share[m] - 1 / 3) < 1e-9
    # Only Opus's claim got dimension consensus → Opus's share == 1.0 in edge_case
    assert metrics.edge_case_coverage_share[ModelId.OPUS] == 1.0
    assert metrics.edge_case_coverage_share[ModelId.SONNET] == 0.0
    assert metrics.edge_case_coverage_share[ModelId.HAIKU] == 0.0


def test_parity_shares_threshold_is_recorded_in_metrics():
    _, _ = _triad_session()
    session, _ = _triad_session()
    metrics = compute_parity_shares(session, threshold=0.18)
    assert metrics is not None
    assert metrics.parity_threshold == 0.18


def test_parity_below_threshold_uses_session_threshold_not_module_constant():
    _, claims = _triad_session()
    opus_id = claims[ModelId.OPUS].claim_id
    sonnet = Phase2Tagging(
        tagger_model=ModelId.SONNET,
        peer_tags=(ClaimTags(claim_id=opus_id, edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,)),),
    )
    haiku = Phase2Tagging(
        tagger_model=ModelId.HAIKU,
        peer_tags=(ClaimTags(claim_id=opus_id, edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,)),),
    )
    session, _ = _triad_session(phase_2_taggings=(sonnet, haiku))
    metrics = compute_parity_shares(session, threshold=0.40)
    assert metrics is not None
    # distinct_claim_share is 1/3 ≈ 0.333 per model — below 0.40 threshold
    assert metrics.parity_below_threshold is True


# --- compute_consensus_pair_distribution + skew --------------------------


def test_pair_distribution_counts_consensus_voter_groups():
    _, claims = _triad_session()
    opus_id = claims[ModelId.OPUS].claim_id
    sonnet_id = claims[ModelId.SONNET].claim_id

    # Sonnet and Haiku consensus on opus_id (boundary_condition)
    s1 = Phase2Tagging(
        tagger_model=ModelId.SONNET,
        peer_tags=(ClaimTags(claim_id=opus_id, edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,)),),
    )
    h1 = Phase2Tagging(
        tagger_model=ModelId.HAIKU,
        peer_tags=(
            ClaimTags(claim_id=opus_id, edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,)),
            ClaimTags(claim_id=sonnet_id, structural_pattern_tags=(StructuralPatternTag.DECOMPOSITION,)),
        ),
    )
    o1 = Phase2Tagging(
        tagger_model=ModelId.OPUS,
        peer_tags=(
            ClaimTags(claim_id=sonnet_id, structural_pattern_tags=(StructuralPatternTag.DECOMPOSITION,)),
        ),
    )
    session, _ = _triad_session(phase_2_taggings=(s1, h1, o1))
    dist = compute_consensus_pair_distribution(session)
    # opus_id boundary_condition → {Sonnet, Haiku}
    # sonnet_id decomposition → {Opus, Haiku}
    assert dist[frozenset({ModelId.SONNET, ModelId.HAIKU})] == 1
    assert dist[frozenset({ModelId.OPUS, ModelId.HAIKU})] == 1


def test_skew_empty_distribution_is_zero():
    assert compute_consensus_pair_skew({}) == 0.0


def test_skew_even_distribution_is_one():
    dist = {
        frozenset({ModelId.OPUS, ModelId.SONNET}): 5,
        frozenset({ModelId.OPUS, ModelId.HAIKU}): 5,
        frozenset({ModelId.SONNET, ModelId.HAIKU}): 5,
    }
    assert compute_consensus_pair_skew(dist) == 1.0


def test_skew_returns_inf_when_min_count_is_zero():
    """Defensive: dict literally containing a 0 count returns inf."""
    dist = {
        frozenset({ModelId.OPUS, ModelId.SONNET}): 5,
        frozenset({ModelId.OPUS, ModelId.HAIKU}): 0,
    }
    assert compute_consensus_pair_skew(dist) == float("inf")


def test_skew_dominant_pair_exceeds_one():
    dist = {
        frozenset({ModelId.OPUS, ModelId.SONNET}): 10,
        frozenset({ModelId.OPUS, ModelId.HAIKU}): 1,
        frozenset({ModelId.SONNET, ModelId.HAIKU}): 1,
    }
    assert compute_consensus_pair_skew(dist) == 10.0


# --- contested_peer_tags / contested_self_tags ---------------------------


def test_contested_peer_tag_emits_when_only_one_peer_tagged():
    _, claims = _triad_session()
    opus_id = claims[ModelId.OPUS].claim_id
    sonnet = Phase2Tagging(
        tagger_model=ModelId.SONNET,
        peer_tags=(ClaimTags(claim_id=opus_id, edge_case_tags=(EdgeCaseTag.ADVERSARIAL_INPUT,)),),
    )
    session, _ = _triad_session(phase_2_taggings=(sonnet,))
    contested = contested_peer_tags(session)
    assert len(contested) == 1
    claim_id, dim, value, voter = contested[0]
    assert claim_id == opus_id
    assert dim == EDGE_CASE_DIMENSION
    assert value == EdgeCaseTag.ADVERSARIAL_INPUT.value
    assert voter is ModelId.SONNET


def test_contested_peer_tag_does_not_emit_when_two_peers_agree():
    _, claims = _triad_session()
    opus_id = claims[ModelId.OPUS].claim_id
    s = Phase2Tagging(
        tagger_model=ModelId.SONNET,
        peer_tags=(ClaimTags(claim_id=opus_id, edge_case_tags=(EdgeCaseTag.ADVERSARIAL_INPUT,)),),
    )
    h = Phase2Tagging(
        tagger_model=ModelId.HAIKU,
        peer_tags=(ClaimTags(claim_id=opus_id, edge_case_tags=(EdgeCaseTag.ADVERSARIAL_INPUT,)),),
    )
    session, _ = _triad_session(phase_2_taggings=(s, h))
    assert contested_peer_tags(session) == ()


def test_contested_self_tag_when_no_peer_endorses():
    _, claims = _triad_session()
    opus_id = claims[ModelId.OPUS].claim_id
    opus_self = Phase2Tagging(
        tagger_model=ModelId.OPUS,
        self_tags=(
            ClaimTags(
                claim_id=opus_id,
                structural_pattern_tags=(StructuralPatternTag.FRAME_SHIFT,),
            ),
        ),
    )
    session, _ = _triad_session(phase_2_taggings=(opus_self,))
    contested = contested_self_tags(session)
    assert len(contested) == 1
    claim_id, dim, value, tagger = contested[0]
    assert claim_id == opus_id
    assert dim == STRUCTURAL_PATTERN_DIMENSION
    assert value == StructuralPatternTag.FRAME_SHIFT.value
    assert tagger is ModelId.OPUS


def test_contested_self_tag_silent_when_peer_endorses_same_value():
    _, claims = _triad_session()
    opus_id = claims[ModelId.OPUS].claim_id
    opus_self = Phase2Tagging(
        tagger_model=ModelId.OPUS,
        self_tags=(
            ClaimTags(
                claim_id=opus_id,
                structural_pattern_tags=(StructuralPatternTag.FRAME_SHIFT,),
            ),
        ),
    )
    sonnet = Phase2Tagging(
        tagger_model=ModelId.SONNET,
        peer_tags=(
            ClaimTags(
                claim_id=opus_id,
                structural_pattern_tags=(StructuralPatternTag.FRAME_SHIFT,),
            ),
        ),
    )
    session, _ = _triad_session(phase_2_taggings=(opus_self, sonnet))
    assert contested_self_tags(session) == ()


def test_contested_self_tag_when_peer_endorses_different_value():
    """Self saw FRAME_SHIFT, peer saw DECOMPOSITION — both should be contested per their own value."""
    _, claims = _triad_session()
    opus_id = claims[ModelId.OPUS].claim_id
    opus_self = Phase2Tagging(
        tagger_model=ModelId.OPUS,
        self_tags=(
            ClaimTags(
                claim_id=opus_id,
                structural_pattern_tags=(StructuralPatternTag.FRAME_SHIFT,),
            ),
        ),
    )
    sonnet = Phase2Tagging(
        tagger_model=ModelId.SONNET,
        peer_tags=(
            ClaimTags(
                claim_id=opus_id,
                structural_pattern_tags=(StructuralPatternTag.DECOMPOSITION,),
            ),
        ),
    )
    session, _ = _triad_session(phase_2_taggings=(opus_self, sonnet))
    self_contested = contested_self_tags(session)
    assert len(self_contested) == 1
    assert self_contested[0][2] == StructuralPatternTag.FRAME_SHIFT.value

    peer_contested = contested_peer_tags(session)
    assert len(peer_contested) == 1
    assert peer_contested[0][2] == StructuralPatternTag.DECOMPOSITION.value


# --- interpret_parity_flags ----------------------------------------------
#
# A panel reads what these tests assert. The architecture flags via
# compute_parity_shares; this function names the operative reading so the
# panel does not have to decode the histogram inline.


def _multi_claim_response(
    model: ModelId, prompt_hash: str, texts: tuple[str, ...]
) -> IndependentResponse:
    claims = tuple(_phase1_claim(model, t) for t in texts)
    return _independent_response(model, prompt_hash, claims)


def _build_triad_with_claim_counts(
    *,
    opus_texts: tuple[str, ...],
    sonnet_texts: tuple[str, ...],
    haiku_texts: tuple[str, ...],
    phase_2_taggings: tuple[Phase2Tagging, ...] = (),
) -> Session:
    return Session(
        session_id="multi-claim",
        prompt="p",
        prompt_hash="h",
        models_invited=(ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU),
        phase_1={
            ModelId.OPUS: _multi_claim_response(ModelId.OPUS, "h", opus_texts),
            ModelId.SONNET: _multi_claim_response(ModelId.SONNET, "h", sonnet_texts),
            ModelId.HAIKU: _multi_claim_response(ModelId.HAIKU, "h", haiku_texts),
        },
        phase_2_taggings=phase_2_taggings,
    )


def _fold_metrics(session: Session, threshold: float = 0.15) -> Session:
    metrics = compute_parity_shares(session, threshold=threshold)
    return session.model_copy(update={"metrics": metrics})


def test_interpret_returns_empty_for_session_without_metrics():
    session, _ = _triad_session()
    assert session.metrics is None
    assert interpret_parity_flags(session) == ()


def test_interpret_returns_empty_when_metrics_has_no_violations():
    # Each of three models has one claim, and both peers tag that claim
    # with BOUNDARY_CONDITION → every model contributes 1/3 of dim_consensus
    # events. All three dimensions have shares 1/3, comfortably above 0.15.
    _, claims = _triad_session()
    opus_id = claims[ModelId.OPUS].claim_id
    sonnet_id = claims[ModelId.SONNET].claim_id
    haiku_id = claims[ModelId.HAIKU].claim_id
    opus = Phase2Tagging(
        tagger_model=ModelId.OPUS,
        peer_tags=(
            ClaimTags(claim_id=sonnet_id, edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,),
                      structural_pattern_tags=(StructuralPatternTag.DECOMPOSITION,)),
            ClaimTags(claim_id=haiku_id, edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,),
                      structural_pattern_tags=(StructuralPatternTag.DECOMPOSITION,)),
        ),
    )
    sonnet = Phase2Tagging(
        tagger_model=ModelId.SONNET,
        peer_tags=(
            ClaimTags(claim_id=opus_id, edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,),
                      structural_pattern_tags=(StructuralPatternTag.DECOMPOSITION,)),
            ClaimTags(claim_id=haiku_id, edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,),
                      structural_pattern_tags=(StructuralPatternTag.DECOMPOSITION,)),
        ),
    )
    haiku = Phase2Tagging(
        tagger_model=ModelId.HAIKU,
        peer_tags=(
            ClaimTags(claim_id=opus_id, edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,),
                      structural_pattern_tags=(StructuralPatternTag.DECOMPOSITION,)),
            ClaimTags(claim_id=sonnet_id, edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,),
                      structural_pattern_tags=(StructuralPatternTag.DECOMPOSITION,)),
        ),
    )
    session, _ = _triad_session(phase_2_taggings=(opus, sonnet, haiku))
    metrics = compute_parity_shares(session)
    assert metrics is not None
    assert not metrics.parity_below_threshold
    session = session.model_copy(update={"metrics": metrics})
    assert interpret_parity_flags(session) == ()


def test_interpret_peer_divergence_when_n1_dominates():
    # 5 Opus claims, 5 Sonnet, 5 Haiku.
    # Sonnet and Haiku's claims get full consensus → those models get
    # edge_case dim_consensus events. Opus's claims each receive coverage
    # from exactly ONE peer — never both. n=1 dominates for Opus.
    opus_texts = tuple(f"opus claim {i}" for i in range(5))
    sonnet_texts = tuple(f"sonnet claim {i}" for i in range(5))
    haiku_texts = tuple(f"haiku claim {i}" for i in range(5))

    base = _build_triad_with_claim_counts(
        opus_texts=opus_texts,
        sonnet_texts=sonnet_texts,
        haiku_texts=haiku_texts,
    )
    opus_ids = [c.claim_id for c in base.phase_1[ModelId.OPUS].claims]
    sonnet_ids = [c.claim_id for c in base.phase_1[ModelId.SONNET].claims]
    haiku_ids = [c.claim_id for c in base.phase_1[ModelId.HAIKU].claims]

    # Sonnet's peer_tags: cover all Haiku claims (boundary_condition),
    # cover Opus claims 0-2 (boundary_condition), skip Opus 3-4.
    sonnet_peer_tags = []
    for cid in haiku_ids:
        sonnet_peer_tags.append(
            ClaimTags(claim_id=cid, edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,))
        )
    for cid in opus_ids[:3]:
        sonnet_peer_tags.append(
            ClaimTags(claim_id=cid, edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,))
        )

    # Haiku's peer_tags: cover all Sonnet claims (boundary_condition),
    # cover Opus claims 3-4 (boundary_condition), skip Opus 0-2.
    haiku_peer_tags = []
    for cid in sonnet_ids:
        haiku_peer_tags.append(
            ClaimTags(claim_id=cid, edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,))
        )
    for cid in opus_ids[3:]:
        haiku_peer_tags.append(
            ClaimTags(claim_id=cid, edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,))
        )

    # Opus's peer_tags: cover both Sonnet and Haiku claims.
    opus_peer_tags = []
    for cid in sonnet_ids + haiku_ids:
        opus_peer_tags.append(
            ClaimTags(claim_id=cid, edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,))
        )

    taggings = (
        Phase2Tagging(tagger_model=ModelId.OPUS, peer_tags=tuple(opus_peer_tags)),
        Phase2Tagging(tagger_model=ModelId.SONNET, peer_tags=tuple(sonnet_peer_tags)),
        Phase2Tagging(tagger_model=ModelId.HAIKU, peer_tags=tuple(haiku_peer_tags)),
    )
    session = _build_triad_with_claim_counts(
        opus_texts=opus_texts,
        sonnet_texts=sonnet_texts,
        haiku_texts=haiku_texts,
        phase_2_taggings=taggings,
    )
    session = _fold_metrics(session)
    assert session.metrics is not None
    assert session.metrics.parity_below_threshold

    flags = interpret_parity_flags(session)
    edge_flags = [
        f for f in flags
        if f.dimension_label == "edge_case_coverage_share"
        and f.source_model is ModelId.OPUS
    ]
    assert len(edge_flags) == 1
    f = edge_flags[0]
    assert f.reading == "peer_divergence"
    assert f.histogram_n_zero == 0
    assert f.histogram_n_one == 5
    assert f.histogram_n_two == 0
    assert f.total_claims == 5
    assert f.other_only_entries == 0


def test_interpret_not_recognized_when_n0_dominates():
    # Opus's claims receive zero peer tags in edge_case dimension. Other
    # models' claims still produce consensus so the dim_consensus pool is
    # nonempty → Opus's share is zero → violation. Histogram is all n=0.
    opus_texts = tuple(f"opus claim {i}" for i in range(4))
    sonnet_texts = tuple(f"sonnet claim {i}" for i in range(4))
    haiku_texts = tuple(f"haiku claim {i}" for i in range(4))

    base = _build_triad_with_claim_counts(
        opus_texts=opus_texts, sonnet_texts=sonnet_texts, haiku_texts=haiku_texts
    )
    sonnet_ids = [c.claim_id for c in base.phase_1[ModelId.SONNET].claims]
    haiku_ids = [c.claim_id for c in base.phase_1[ModelId.HAIKU].claims]

    # Peers cover each other's claims (full edge_case coverage), but no peer
    # tags any Opus claim in edge_case.
    sonnet_tags = tuple(
        ClaimTags(claim_id=cid, edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,))
        for cid in haiku_ids
    )
    haiku_tags = tuple(
        ClaimTags(claim_id=cid, edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,))
        for cid in sonnet_ids
    )
    opus_tags = tuple(
        ClaimTags(claim_id=cid, edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,))
        for cid in sonnet_ids + haiku_ids
    )

    taggings = (
        Phase2Tagging(tagger_model=ModelId.OPUS, peer_tags=opus_tags),
        Phase2Tagging(tagger_model=ModelId.SONNET, peer_tags=sonnet_tags),
        Phase2Tagging(tagger_model=ModelId.HAIKU, peer_tags=haiku_tags),
    )
    session = _build_triad_with_claim_counts(
        opus_texts=opus_texts, sonnet_texts=sonnet_texts, haiku_texts=haiku_texts,
        phase_2_taggings=taggings,
    )
    session = _fold_metrics(session)
    assert session.metrics is not None

    flags = interpret_parity_flags(session)
    edge_opus = [
        f for f in flags
        if f.dimension_label == "edge_case_coverage_share"
        and f.source_model is ModelId.OPUS
    ]
    assert len(edge_opus) == 1
    f = edge_opus[0]
    assert f.reading == "not_recognized"
    assert f.histogram_n_zero == 4
    assert f.histogram_n_one == 0
    assert f.histogram_n_two == 0
    assert f.other_only_entries == 0


def test_interpret_vocabulary_failed_when_other_only_dominates():
    # Both peers tag every Opus claim with edge_case_tags=(OTHER,) only.
    # other_only_entries dominates; per-claim n_cover stays at 0 (OTHER is
    # excluded from consensus), so Opus's edge_case share is zero.
    opus_texts = tuple(f"opus claim {i}" for i in range(4))
    sonnet_texts = tuple(f"sonnet claim {i}" for i in range(4))
    haiku_texts = tuple(f"haiku claim {i}" for i in range(4))

    base = _build_triad_with_claim_counts(
        opus_texts=opus_texts, sonnet_texts=sonnet_texts, haiku_texts=haiku_texts
    )
    opus_ids = [c.claim_id for c in base.phase_1[ModelId.OPUS].claims]
    sonnet_ids = [c.claim_id for c in base.phase_1[ModelId.SONNET].claims]
    haiku_ids = [c.claim_id for c in base.phase_1[ModelId.HAIKU].claims]

    sonnet_tags = tuple(
        ClaimTags(claim_id=cid, edge_case_tags=(EdgeCaseTag.OTHER,))
        for cid in opus_ids
    ) + tuple(
        ClaimTags(claim_id=cid, edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,))
        for cid in haiku_ids
    )
    haiku_tags = tuple(
        ClaimTags(claim_id=cid, edge_case_tags=(EdgeCaseTag.OTHER,))
        for cid in opus_ids
    ) + tuple(
        ClaimTags(claim_id=cid, edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,))
        for cid in sonnet_ids
    )
    opus_tags = tuple(
        ClaimTags(claim_id=cid, edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,))
        for cid in sonnet_ids + haiku_ids
    )

    taggings = (
        Phase2Tagging(tagger_model=ModelId.OPUS, peer_tags=opus_tags),
        Phase2Tagging(tagger_model=ModelId.SONNET, peer_tags=sonnet_tags),
        Phase2Tagging(tagger_model=ModelId.HAIKU, peer_tags=haiku_tags),
    )
    session = _build_triad_with_claim_counts(
        opus_texts=opus_texts, sonnet_texts=sonnet_texts, haiku_texts=haiku_texts,
        phase_2_taggings=taggings,
    )
    session = _fold_metrics(session)
    assert session.metrics is not None

    flags = interpret_parity_flags(session)
    edge_opus = [
        f for f in flags
        if f.dimension_label == "edge_case_coverage_share"
        and f.source_model is ModelId.OPUS
    ]
    assert len(edge_opus) == 1
    f = edge_opus[0]
    assert f.reading == "vocabulary_failed"
    assert f.histogram_n_zero == 4
    assert f.other_only_entries == 8  # 2 peers × 4 Opus claims


def test_interpret_low_claim_volume_for_distinct_share_violation():
    # Opus authors 1 claim, the others 10 each → Opus distinct_share = 1/21
    # ≈ 0.048 < 0.15.
    opus_texts = ("opus only claim",)
    sonnet_texts = tuple(f"sonnet {i}" for i in range(10))
    haiku_texts = tuple(f"haiku {i}" for i in range(10))
    session = _build_triad_with_claim_counts(
        opus_texts=opus_texts, sonnet_texts=sonnet_texts, haiku_texts=haiku_texts,
    )
    session = _fold_metrics(session)
    assert session.metrics is not None

    flags = interpret_parity_flags(session)
    distinct = [f for f in flags if f.dimension_label == "distinct_claim_share"]
    opus_flag = [f for f in distinct if f.source_model is ModelId.OPUS]
    assert len(opus_flag) == 1
    f = opus_flag[0]
    assert f.reading == "low_claim_volume"
    assert f.histogram_n_zero == 0
    assert f.histogram_n_one == 0
    assert f.histogram_n_two == 0
    assert f.share < 0.15


def test_interpret_ambiguous_when_no_signal_dominates():
    # Opus has 4 claims: 1 with n=2, 2 with n=1, 1 with n=0. Threshold for
    # peer_divergence/not_recognized requires the dominant frac ≥ 0.4, which
    # 2/4=0.5 satisfies, but one_frac and zero_frac are 0.5 and 0.25 — one
    # wins. Test with a tighter spread: 1 n=0, 1 n=1, 2 n=2 → one_frac=0.25,
    # zero_frac=0.25 → ambiguous.
    opus_texts = tuple(f"opus {i}" for i in range(4))
    sonnet_texts = tuple(f"sonnet {i}" for i in range(4))
    haiku_texts = tuple(f"haiku {i}" for i in range(4))
    base = _build_triad_with_claim_counts(
        opus_texts=opus_texts, sonnet_texts=sonnet_texts, haiku_texts=haiku_texts,
    )
    opus_ids = [c.claim_id for c in base.phase_1[ModelId.OPUS].claims]
    sonnet_ids = [c.claim_id for c in base.phase_1[ModelId.SONNET].claims]
    haiku_ids = [c.claim_id for c in base.phase_1[ModelId.HAIKU].claims]

    # Opus claim 0: n=0 (no peer covers); 1: n=1 (only Sonnet); 2: n=2; 3: n=2.
    sonnet_tags = (
        ClaimTags(claim_id=opus_ids[1], edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,)),
        ClaimTags(claim_id=opus_ids[2], edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,)),
        ClaimTags(claim_id=opus_ids[3], edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,)),
    ) + tuple(
        ClaimTags(claim_id=cid, edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,))
        for cid in haiku_ids
    )
    haiku_tags = (
        ClaimTags(claim_id=opus_ids[2], edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,)),
        ClaimTags(claim_id=opus_ids[3], edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,)),
    ) + tuple(
        ClaimTags(claim_id=cid, edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,))
        for cid in sonnet_ids
    )
    opus_tags = tuple(
        ClaimTags(claim_id=cid, edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,))
        for cid in sonnet_ids + haiku_ids
    )

    taggings = (
        Phase2Tagging(tagger_model=ModelId.OPUS, peer_tags=opus_tags),
        Phase2Tagging(tagger_model=ModelId.SONNET, peer_tags=sonnet_tags),
        Phase2Tagging(tagger_model=ModelId.HAIKU, peer_tags=haiku_tags),
    )
    # Use a high threshold to force Opus into violation despite n=2 dominance.
    session = _build_triad_with_claim_counts(
        opus_texts=opus_texts, sonnet_texts=sonnet_texts, haiku_texts=haiku_texts,
        phase_2_taggings=taggings,
    )
    session = _fold_metrics(session, threshold=0.30)
    assert session.metrics is not None

    flags = interpret_parity_flags(session)
    edge_opus = [
        f for f in flags
        if f.dimension_label == "edge_case_coverage_share"
        and f.source_model is ModelId.OPUS
    ]
    assert len(edge_opus) == 1
    f = edge_opus[0]
    # 1 n=0, 1 n=1, 2 n=2 → zero_frac=0.25, one_frac=0.25 → below 0.4 → ambiguous
    assert f.reading == "ambiguous"
    assert f.histogram_n_zero == 1
    assert f.histogram_n_one == 1
    assert f.histogram_n_two == 2


def test_interpret_lucumi_real_session_returns_peer_divergence():
    """Integration: load the real persisted Lucumí session, recompute
    metrics, and confirm the function reads (Opus, edge_case_coverage_share)
    as peer_divergence with histogram n=0:1 n=1:5 n=2:1.

    This is the same shape disambiguated by hand in chronicle #411. The
    test locks the function's classifier against real data so the M1 panel
    can trust its output.
    """
    from pathlib import Path
    from golden_lattice.memory_graph.store import JsonFileSessionStore

    sessions_dir = Path(__file__).parents[2] / "sessions"
    if not (sessions_dir / "session_20260504_071848_19b0600f.session.json").exists():
        pytest.skip("Lucumí session file not present in this checkout.")

    store = JsonFileSessionStore(sessions_dir)
    raw = store.load("session_20260504_071848_19b0600f")
    metrics = compute_parity_shares(raw)
    session = raw.model_copy(update={"metrics": metrics})

    flags = interpret_parity_flags(session)
    opus_edge = [
        f for f in flags
        if f.dimension_label == "edge_case_coverage_share"
        and f.source_model is ModelId.OPUS
    ]
    assert len(opus_edge) == 1
    f = opus_edge[0]
    assert f.reading == "peer_divergence"
    assert (f.histogram_n_zero, f.histogram_n_one, f.histogram_n_two) == (1, 5, 1)
    assert f.other_only_entries == 0
    assert f.total_claims == 7
