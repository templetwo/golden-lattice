"""Tests for Phase 2 structural tagging — recognition-from-within."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from golden_lattice.memory_graph.base import ModelId, Phase, claim_id_for
from golden_lattice.memory_graph.schema import (
    Claim,
    IndependentResponse,
    Session,
)
from golden_lattice.memory_graph.tagging import (
    TAG_VOCABULARY_VERSION,
    ClaimTags,
    ConsensusTag,
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
    """Build a 3-sibling session with one Phase 1 claim per model. Returns session + claim map."""
    claims = {
        ModelId.OPUS: _phase1_claim(ModelId.OPUS, "opus says alpha"),
        ModelId.SONNET: _phase1_claim(ModelId.SONNET, "sonnet says beta"),
        ModelId.HAIKU: _phase1_claim(ModelId.HAIKU, "haiku says gamma"),
    }
    session = Session(
        session_id="triad",
        prompt="p",
        prompt_hash="h",
        models_invited=(ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU),
        phase_1={
            m: _independent_response(m, "h", (c,))
            for m, c in claims.items()
        },
        phase_2_taggings=phase_2_taggings,
    )
    return session, claims


# --- ClaimTags / Phase2Tagging structural rules ---------------------------


def test_phase2_tagging_rejects_duplicate_peer_claim_ids():
    with pytest.raises(ValidationError, match="duplicate claim_id"):
        Phase2Tagging(
            tagger_model=ModelId.OPUS,
            peer_tags=(
                ClaimTags(claim_id="abc"),
                ClaimTags(claim_id="abc"),
            ),
        )


def test_phase2_tagging_rejects_duplicate_self_claim_ids():
    with pytest.raises(ValidationError, match="duplicate claim_id"):
        Phase2Tagging(
            tagger_model=ModelId.OPUS,
            self_tags=(
                ClaimTags(claim_id="abc"),
                ClaimTags(claim_id="abc"),
            ),
        )


def test_phase2_tagging_rejects_claim_in_both_peer_and_self():
    with pytest.raises(ValidationError, match="both peer_tags and self_tags"):
        Phase2Tagging(
            tagger_model=ModelId.OPUS,
            peer_tags=(ClaimTags(claim_id="abc"),),
            self_tags=(ClaimTags(claim_id="abc"),),
        )


def test_phase2_tagging_default_vocabulary_version_is_pinned():
    tagging = Phase2Tagging(tagger_model=ModelId.OPUS)
    assert tagging.vocabulary_version == TAG_VOCABULARY_VERSION


# --- ConsensusTag structural rules ----------------------------------------


def test_consensus_tag_requires_at_least_two_voters():
    with pytest.raises(ValidationError, match="at least 2 distinct voters"):
        ConsensusTag(
            claim_id="abc",
            dimension="edge_case",
            tag_value=EdgeCaseTag.BOUNDARY_CONDITION.value,
            consensus_voters=(ModelId.OPUS,),
        )


def test_consensus_tag_rejects_duplicate_voters():
    with pytest.raises(ValidationError, match="distinct"):
        ConsensusTag(
            claim_id="abc",
            dimension="edge_case",
            tag_value=EdgeCaseTag.BOUNDARY_CONDITION.value,
            consensus_voters=(ModelId.OPUS, ModelId.OPUS),
        )


def test_consensus_tag_rejects_invalid_dimension():
    with pytest.raises(ValidationError, match="dimension must be"):
        ConsensusTag(
            claim_id="abc",
            dimension="vibes",
            tag_value="anything",
            consensus_voters=(ModelId.OPUS, ModelId.SONNET),
        )


# --- Session-level tagging integration ------------------------------------


def test_session_rejects_tagging_from_uninvited_model():
    bogus_tagging = Phase2Tagging(tagger_model=ModelId.HAIKU)
    opus_claim = _phase1_claim(ModelId.OPUS, "x")
    sonnet_claim = _phase1_claim(ModelId.SONNET, "y")
    with pytest.raises(ValidationError, match="not in models_invited"):
        Session(
            session_id="dyad-with-foreign-tagger",
            prompt="p",
            prompt_hash="h",
            models_invited=(ModelId.OPUS, ModelId.SONNET),
            phase_1={
                ModelId.OPUS: _independent_response(ModelId.OPUS, "h", (opus_claim,)),
                ModelId.SONNET: _independent_response(ModelId.SONNET, "h", (sonnet_claim,)),
            },
            phase_2_taggings=(bogus_tagging,),
        )


def test_session_rejects_duplicate_taggings_from_same_model():
    _, claims = _triad_session()
    sonnet_id = claims[ModelId.SONNET].claim_id
    haiku_id = claims[ModelId.HAIKU].claim_id
    t1 = Phase2Tagging(
        tagger_model=ModelId.OPUS,
        peer_tags=(ClaimTags(claim_id=sonnet_id),),
    )
    t2 = Phase2Tagging(
        tagger_model=ModelId.OPUS,
        peer_tags=(ClaimTags(claim_id=haiku_id),),
    )
    with pytest.raises(ValidationError, match="at most one tagging"):
        _triad_session(phase_2_taggings=(t1, t2))


def test_session_rejects_tagging_referencing_unknown_claim():
    bad_tagging = Phase2Tagging(
        tagger_model=ModelId.OPUS,
        peer_tags=(ClaimTags(claim_id="ghost_claim_id"),),
    )
    with pytest.raises(ValidationError, match="unknown claim_id"):
        _triad_session(phase_2_taggings=(bad_tagging,))


def test_session_rejects_own_claim_in_peer_tags():
    _, claims = _triad_session()
    opus_id = claims[ModelId.OPUS].claim_id
    bad_tagging = Phase2Tagging(
        tagger_model=ModelId.OPUS,
        peer_tags=(ClaimTags(claim_id=opus_id),),
    )
    with pytest.raises(ValidationError, match="Own claims belong in self_tags"):
        _triad_session(phase_2_taggings=(bad_tagging,))


def test_session_rejects_peer_claim_in_self_tags():
    _, claims = _triad_session()
    sonnet_id = claims[ModelId.SONNET].claim_id
    bad_tagging = Phase2Tagging(
        tagger_model=ModelId.OPUS,
        self_tags=(ClaimTags(claim_id=sonnet_id),),
    )
    with pytest.raises(ValidationError, match="self_tags is for the tagger's own claims only"):
        _triad_session(phase_2_taggings=(bad_tagging,))


def test_session_accepts_well_formed_taggings():
    _, claims = _triad_session()
    opus_id = claims[ModelId.OPUS].claim_id
    sonnet_id = claims[ModelId.SONNET].claim_id
    haiku_id = claims[ModelId.HAIKU].claim_id

    opus_tagging = Phase2Tagging(
        tagger_model=ModelId.OPUS,
        peer_tags=(
            ClaimTags(
                claim_id=sonnet_id,
                edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,),
            ),
            ClaimTags(
                claim_id=haiku_id,
                structural_pattern_tags=(StructuralPatternTag.FRAMING_CHOICE,),
            ),
        ),
        self_tags=(
            ClaimTags(
                claim_id=opus_id,
                structural_pattern_tags=(StructuralPatternTag.DECOMPOSITION,),
            ),
        ),
    )
    sonnet_tagging = Phase2Tagging(
        tagger_model=ModelId.SONNET,
        peer_tags=(
            ClaimTags(
                claim_id=opus_id,
                structural_pattern_tags=(StructuralPatternTag.DECOMPOSITION,),
            ),
        ),
    )
    haiku_tagging = Phase2Tagging(
        tagger_model=ModelId.HAIKU,
        peer_tags=(
            ClaimTags(
                claim_id=sonnet_id,
                edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,),
            ),
        ),
    )

    session, _ = _triad_session(
        phase_2_taggings=(opus_tagging, sonnet_tagging, haiku_tagging),
    )
    assert len(session.phase_2_taggings) == 3
    taggers = {t.tagger_model for t in session.phase_2_taggings}
    assert taggers == {ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU}
