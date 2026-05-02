"""Tests for pure-function metrics over a tagged Session."""

from datetime import datetime, timezone

import pytest

from golden_lattice.memory_graph.base import ModelId, Phase, claim_id_for
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
