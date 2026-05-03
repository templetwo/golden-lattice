"""Phase 2 structural tagging — recognition-from-within as the parity measurement primitive.

Each model, having seen all Phase 1 responses, produces a single classification pass
over every Phase 1 claim (own and peers') and every Phase 2 missing claim. Tags come
from closed vocabularies. Consensus tagging (≥2 of N models agree) determines what
counts toward parity. Self-tags are logged but excluded from parity counting.

The architectural commitment: the peers recognize each other's contributions, and
that recognition is what counts as parity. No external arbiter. No single judge.
The structure adjudicates itself.

This file extends the Memory Graph schema. It does not replace anything in schema.py.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, model_validator

from golden_lattice.memory_graph.base import (
    EDGE_CASE_DIMENSION,
    STRUCTURAL_PATTERN_DIMENSION,
    Dimension,
    ModelId,
)


TAG_VOCABULARY_VERSION = "v0.1"
_VALID_DIMENSIONS = (EDGE_CASE_DIMENSION, STRUCTURAL_PATTERN_DIMENSION)


class EdgeCaseTag(str, Enum):
    BOUNDARY_CONDITION = "boundary_condition"
    FAILURE_MODE = "failure_mode"
    ADVERSARIAL_INPUT = "adversarial_input"
    ASSUMPTION_VIOLATION = "assumption_violation"
    SCALING_LIMIT = "scaling_limit"
    INTERACTION_EFFECT = "interaction_effect"
    OTHER = "other"


class StructuralPatternTag(str, Enum):
    FRAMING_CHOICE = "framing_choice"
    DECOMPOSITION = "decomposition"
    ABSTRACTION_LEVEL = "abstraction_level"
    CONSTRAINT_INTRODUCTION = "constraint_introduction"
    TRADEOFF_SURFACE = "tradeoff_surface"
    FRAME_SHIFT = "frame_shift"
    OTHER = "other"


class ClaimTags(BaseModel):
    """One tagger's view of one claim. Empty tag tuples mean 'not flagged in this dimension'."""

    model_config = ConfigDict(frozen=True)

    claim_id: str
    edge_case_tags: tuple[EdgeCaseTag, ...] = ()
    structural_pattern_tags: tuple[StructuralPatternTag, ...] = ()

    @model_validator(mode="after")
    def _no_duplicate_tag_values(self) -> "ClaimTags":
        if len(self.edge_case_tags) != len(set(self.edge_case_tags)):
            raise ValueError(
                f"ClaimTags for {self.claim_id} has duplicate edge_case_tags. "
                "A single tagger cannot tag the same value twice on one claim."
            )
        if len(self.structural_pattern_tags) != len(set(self.structural_pattern_tags)):
            raise ValueError(
                f"ClaimTags for {self.claim_id} has duplicate structural_pattern_tags."
            )
        return self


class Phase2Tagging(BaseModel):
    """One model's classification pass over all visible claims. Produced during Phase 2.

    A tagging covers Phase 1 claims (own + peers') and Phase 2 missing claims surfaced
    by any reader's CrossReading. The tagger is acting as a peer recognizer, not a judge.

    Self-tags are tags this model put on its own claims. They are logged but excluded
    from parity-share consensus counting (parity uses peer-tagging only — ≥2 *other*
    models must agree).
    """

    model_config = ConfigDict(frozen=True)

    tagger_model: ModelId
    vocabulary_version: str = TAG_VOCABULARY_VERSION
    peer_tags: tuple[ClaimTags, ...] = ()
    self_tags: tuple[ClaimTags, ...] = ()
    generated_at: Optional[datetime] = None

    @model_validator(mode="after")
    def _no_duplicate_claim_ids_within_partition(self) -> "Phase2Tagging":
        peer_ids = [t.claim_id for t in self.peer_tags]
        self_ids = [t.claim_id for t in self.self_tags]
        if len(peer_ids) != len(set(peer_ids)):
            raise ValueError("peer_tags contains duplicate claim_id entries.")
        if len(self_ids) != len(set(self_ids)):
            raise ValueError("self_tags contains duplicate claim_id entries.")
        if set(peer_ids) & set(self_ids):
            raise ValueError(
                "A claim_id appears in both peer_tags and self_tags. "
                "A claim is either own or peer, not both."
            )
        return self

    def all_tagged_claim_ids(self) -> set[str]:
        return {t.claim_id for t in self.peer_tags} | {t.claim_id for t in self.self_tags}


class ConsensusTag(BaseModel):
    """A tag that ≥2 peer models agreed on for a given claim, dimension, and specific tag value.

    Subtype-level consensus. Finer-grained research artifact. consensus_voters records
    *which* peers agreed — load-bearing for alignment collapse detection. Skewed pair
    distribution across consensus_voters flags 2-against-1 drift even when per-model
    parity share looks healthy.
    """

    model_config = ConfigDict(frozen=True)

    claim_id: str
    dimension: Dimension
    tag_value: str  # the actual enum value string
    consensus_voters: tuple[ModelId, ...]

    @model_validator(mode="after")
    def _voters_are_distinct_and_sufficient(self) -> "ConsensusTag":
        _validate_consensus_voters(self.consensus_voters, self.dimension)
        return self


class DimensionConsensus(BaseModel):
    """≥2 peer models agreed the claim belongs in this dimension at all (any non-OTHER value).

    Dimension-level consensus is what counts toward parity. The lattice agreed the claim
    is an edge case (or structural pattern); they may have disagreed on the precise subtype.
    Treating subtype-disagreement-but-dimension-agreement as 'no consensus' would silence
    a meta-recognition the lattice did achieve. This artifact captures the meta-recognition.

    Robust against vocabulary churn — DimensionConsensus stays comparable across vocabulary
    versions; ConsensusTag only compares within a version.
    """

    model_config = ConfigDict(frozen=True)

    claim_id: str
    dimension: Dimension
    consensus_voters: tuple[ModelId, ...]

    @model_validator(mode="after")
    def _voters_are_distinct_and_sufficient(self) -> "DimensionConsensus":
        _validate_consensus_voters(self.consensus_voters, self.dimension)
        return self


def _validate_consensus_voters(
    voters: tuple[ModelId, ...], dimension: str
) -> None:
    """Shared invariant for ConsensusTag and DimensionConsensus."""
    if len(voters) < 2:
        raise ValueError(
            "consensus requires at least 2 distinct voters. "
            "Recognition-from-within requires more than one peer."
        )
    if len(set(voters)) != len(voters):
        raise ValueError("consensus_voters must be distinct ModelIds.")
    if dimension not in _VALID_DIMENSIONS:
        raise ValueError(
            f"dimension must be one of {_VALID_DIMENSIONS}, got {dimension!r}."
        )
