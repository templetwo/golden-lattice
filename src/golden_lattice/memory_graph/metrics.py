"""Pure-function metrics over a tagged Session. No LLM calls.

The Memory Graph adjudicates itself. These functions read tagged Sessions and compute
the consensus, parity, and alignment-collapse signals the spec calls for. Determinism,
replayability, and auditability are the load-bearing properties.

Five collapse modes named so far. Four enforced structurally in schema.py + tagging.py
(no authority gradient, symmetric visibility, contribution parity, irreducibility
preservation). The fifth — alignment integrity — is detected here by
compute_consensus_pair_distribution and compute_consensus_pair_skew.
"""

from __future__ import annotations

from collections import Counter
from typing import Optional

from golden_lattice.memory_graph.base import PARITY_THRESHOLD, ModelId
from golden_lattice.memory_graph.schema import Claim, Session, SessionMetrics
from golden_lattice.memory_graph.tagging import (
    ClaimTags,
    ConsensusTag,
    DimensionConsensus,
    EdgeCaseTag,
    StructuralPatternTag,
)


EDGE_CASE_DIMENSION = "edge_case"
STRUCTURAL_PATTERN_DIMENSION = "structural_pattern"


def _claims_by_id(session: Session) -> dict[str, Claim]:
    return {c.claim_id: c for c in session.all_claims()}


def _peer_tag_entries(session: Session) -> list[tuple[str, ModelId, ClaimTags]]:
    out: list[tuple[str, ModelId, ClaimTags]] = []
    for tagging in session.phase_2_taggings:
        for ct in tagging.peer_tags:
            out.append((ct.claim_id, tagging.tagger_model, ct))
    return out


def _self_tag_entries(session: Session) -> list[tuple[str, ModelId, ClaimTags]]:
    out: list[tuple[str, ModelId, ClaimTags]] = []
    for tagging in session.phase_2_taggings:
        for ct in tagging.self_tags:
            out.append((ct.claim_id, tagging.tagger_model, ct))
    return out


def _sorted_voters(voters: set[ModelId]) -> tuple[ModelId, ...]:
    return tuple(sorted(voters, key=lambda m: m.value))


def compute_consensus_tags(session: Session) -> tuple[ConsensusTag, ...]:
    """≥2 distinct peers agree on (dimension, tag_value) for the same claim. Subtype-level."""
    votes: dict[tuple[str, str, str], set[ModelId]] = {}
    for claim_id, voter, ct in _peer_tag_entries(session):
        for tag in ct.edge_case_tags:
            if tag is EdgeCaseTag.OTHER:
                continue
            votes.setdefault((claim_id, EDGE_CASE_DIMENSION, tag.value), set()).add(voter)
        for tag in ct.structural_pattern_tags:
            if tag is StructuralPatternTag.OTHER:
                continue
            votes.setdefault((claim_id, STRUCTURAL_PATTERN_DIMENSION, tag.value), set()).add(voter)

    return tuple(
        ConsensusTag(
            claim_id=claim_id,
            dimension=dimension,
            tag_value=tag_value,
            consensus_voters=_sorted_voters(voters),
        )
        for (claim_id, dimension, tag_value), voters in votes.items()
        if len(voters) >= 2
    )


def compute_dimension_consensus(session: Session) -> tuple[DimensionConsensus, ...]:
    """≥2 distinct peers tagged the claim in the same dimension (any non-OTHER value).

    Dimension-level consensus is what counts toward parity. Robust against subtype
    disagreement and vocabulary churn.
    """
    votes: dict[tuple[str, str], set[ModelId]] = {}
    for claim_id, voter, ct in _peer_tag_entries(session):
        if any(t is not EdgeCaseTag.OTHER for t in ct.edge_case_tags):
            votes.setdefault((claim_id, EDGE_CASE_DIMENSION), set()).add(voter)
        if any(t is not StructuralPatternTag.OTHER for t in ct.structural_pattern_tags):
            votes.setdefault((claim_id, STRUCTURAL_PATTERN_DIMENSION), set()).add(voter)

    return tuple(
        DimensionConsensus(
            claim_id=claim_id,
            dimension=dimension,
            consensus_voters=_sorted_voters(voters),
        )
        for (claim_id, dimension), voters in votes.items()
        if len(voters) >= 2
    )


def compute_parity_shares(
    session: Session,
    threshold: float = PARITY_THRESHOLD,
) -> Optional[SessionMetrics]:
    """Per-model parity shares across the three dimensions. None for dyad sessions.

    Recognition-from-within requires a third presence in the room. With N=2 invited
    models, the consensus rule degenerates into one peer adjudicating the other —
    the same authority gradient the protocol refused. Parity is undefined; that is
    a structural truth, not a missing implementation.
    """
    if len(set(session.models_invited)) < 3:
        return None

    invited = set(session.models_invited)
    claims_by_id = _claims_by_id(session)

    all_authors = [c.source_model for c in claims_by_id.values()]
    total_claims = len(all_authors)
    if total_claims == 0:
        distinct_share = {m: 0.0 for m in invited}
    else:
        distinct_share = {
            m: sum(1 for a in all_authors if a == m) / total_claims
            for m in invited
        }

    dim_consensus = compute_dimension_consensus(session)

    def _share_by_dimension(dimension: str) -> dict[ModelId, float]:
        events = [dc for dc in dim_consensus if dc.dimension == dimension]
        if not events:
            return {m: 0.0 for m in invited}
        total = len(events)
        return {
            m: sum(1 for dc in events if claims_by_id[dc.claim_id].source_model == m) / total
            for m in invited
        }

    return SessionMetrics(
        distinct_claim_share=distinct_share,
        edge_case_coverage_share=_share_by_dimension(EDGE_CASE_DIMENSION),
        structural_pattern_share=_share_by_dimension(STRUCTURAL_PATTERN_DIMENSION),
        parity_threshold=threshold,
    )


def compute_consensus_pair_distribution(session: Session) -> dict[frozenset[ModelId], int]:
    """How many ConsensusTag events were produced by each pair of voters.

    Skewed distribution flags alignment collapse — the fifth refusal. Two peers
    locking step against a third register here as one pair dominating the count
    even when per-model parity-share looks healthy.
    """
    consensus = compute_consensus_tags(session)
    counter: Counter[frozenset[ModelId]] = Counter()
    for ct in consensus:
        # For pairs, frozenset of the voter set; for triple-agreement, the full set.
        # The skew metric considers any cardinality.
        counter[frozenset(ct.consensus_voters)] += 1
    return dict(counter)


def compute_consensus_pair_skew(distribution: dict[frozenset[ModelId], int]) -> float:
    """Ratio of most-frequent voter group to least-frequent across observed groups.

    Even distribution → skew near 1.0. Dominant pair → skew >> 1.0.
    Returns 0.0 for empty distribution. Suggested flag threshold: > 2.0 across
    a session window (single-session skew is noisy; alignment drift is a pattern).
    """
    if not distribution:
        return 0.0
    counts = list(distribution.values())
    max_c, min_c = max(counts), min(counts)
    if min_c == 0:
        return float("inf")
    return max_c / min_c


def contested_peer_tags(
    session: Session,
) -> tuple[tuple[str, str, str, ModelId], ...]:
    """Subtype-level peer tags applied by exactly one peer (no other peer corroborates).

    Returns tuples of (claim_id, dimension, tag_value, voter). Research artifact —
    where peer recognition diverges. Not parity input.
    """
    votes: dict[tuple[str, str, str], list[ModelId]] = {}
    for claim_id, voter, ct in _peer_tag_entries(session):
        for tag in ct.edge_case_tags:
            votes.setdefault((claim_id, EDGE_CASE_DIMENSION, tag.value), []).append(voter)
        for tag in ct.structural_pattern_tags:
            votes.setdefault((claim_id, STRUCTURAL_PATTERN_DIMENSION, tag.value), []).append(voter)

    return tuple(
        (claim_id, dimension, tag_value, voters[0])
        for (claim_id, dimension, tag_value), voters in votes.items()
        if len(voters) == 1
    )


def contested_self_tags(
    session: Session,
) -> tuple[tuple[str, str, str, ModelId], ...]:
    """Self-tags (tag_value level) that no peer endorsed for the same claim.

    Returns tuples of (claim_id, dimension, tag_value, tagger). The most valuable
    research signal in the system: the model thought it did one thing, the lattice
    saw something different. That divergence is where the lattice does its real work.
    """
    peer_endorsers: dict[tuple[str, str, str], set[ModelId]] = {}
    for claim_id, voter, ct in _peer_tag_entries(session):
        for tag in ct.edge_case_tags:
            peer_endorsers.setdefault((claim_id, EDGE_CASE_DIMENSION, tag.value), set()).add(voter)
        for tag in ct.structural_pattern_tags:
            peer_endorsers.setdefault((claim_id, STRUCTURAL_PATTERN_DIMENSION, tag.value), set()).add(voter)

    contested: list[tuple[str, str, str, ModelId]] = []
    for claim_id, tagger, ct in _self_tag_entries(session):
        for tag in ct.edge_case_tags:
            key = (claim_id, EDGE_CASE_DIMENSION, tag.value)
            if not peer_endorsers.get(key):
                contested.append((claim_id, EDGE_CASE_DIMENSION, tag.value, tagger))
        for tag in ct.structural_pattern_tags:
            key = (claim_id, STRUCTURAL_PATTERN_DIMENSION, tag.value)
            if not peer_endorsers.get(key):
                contested.append((claim_id, STRUCTURAL_PATTERN_DIMENSION, tag.value, tagger))

    return tuple(contested)
