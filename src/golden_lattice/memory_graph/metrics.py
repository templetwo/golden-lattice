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
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from golden_lattice.memory_graph.base import (
    EDGE_CASE_DIMENSION,
    PARITY_THRESHOLD,
    STRUCTURAL_PATTERN_DIMENSION,
    Dimension,
    ModelId,
    Phase,
)
from golden_lattice.memory_graph.schema import Claim, Session, SessionMetrics
from golden_lattice.memory_graph.tagging import (
    ClaimTags,
    ConsensusTag,
    DimensionConsensus,
    EdgeCaseTag,
    StructuralPatternTag,
)


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

    def _share_by_dimension(dimension: Dimension) -> dict[ModelId, float]:
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


FlagReading = Literal[
    "peer_divergence",
    "vocabulary_failed",
    "not_recognized",
    "low_claim_volume",
    "ambiguous",
]


class FlagInterpretation(BaseModel):
    """One reading per parity violation in a SessionMetrics object.

    The architecture is built to flag, not to explain. Three readings the panel
    must hold open without prejudging when a consensus-dimension violation fires:

      peer_divergence   — n=1 dominates: peers each cover claims in this
                          dimension but disagree on which. The vocabulary worked
                          per-peer, the peers diverged per-claim.
      vocabulary_failed — OTHER-only entries dominate: peers saw the dimension
                          applies but no subtype fit. ARCHITECTURE.md §5.2
                          vocabulary-fitness signal.
      not_recognized    — n=0 dominates: peers did not see claims as
                          dimension-relevant at all. Real recognition asymmetry.

    A fourth reading is reserved for the non-consensus violation:

      low_claim_volume  — distinct_claim_share fell below threshold. The model
                          contributed too few Phase 1 claims relative to peers.
                          Histogram fields are zero by convention; the share
                          is the whole story.

    ambiguous           — No signal exceeds the dominance threshold. The panel
                          shows histogram + share and lets the reader decide.
    """

    model_config = ConfigDict(frozen=True)

    source_model: ModelId
    dimension_label: str
    share: float
    reading: FlagReading
    histogram_n_zero: int = 0
    histogram_n_one: int = 0
    histogram_n_two: int = 0
    other_only_entries: int = 0
    total_claims: int = 0


_DIMENSION_BY_LABEL: dict[str, Dimension] = {
    "edge_case_coverage_share": EDGE_CASE_DIMENSION,
    "structural_pattern_share": STRUCTURAL_PATTERN_DIMENSION,
}

_DOMINANCE_THRESHOLD = 0.4


def _classify_consensus_flag(
    n_zero: int,
    n_one: int,
    other_only_entries: int,
    total_claims: int,
    n_peers: int,
) -> FlagReading:
    """Pick the strongest signal among the three consensus-flag readings.

    Compares normalized signals over the model's Phase 1 claims:
      zero_frac        = n_zero / total_claims
      one_frac         = n_one  / total_claims
      other_only_frac  = other_only_entries / (total_claims * n_peers)

    n=2 is the passing-case bucket and not a flag reading, so it does not
    enter classification. Vocabulary check fires first: it is per-entry
    rather than per-claim and can be the dominant signal even when the
    per-claim histogram looks ordinary. Otherwise the larger of
    {zero_frac, one_frac} names the reading, provided it exceeds
    _DOMINANCE_THRESHOLD. Below threshold → ambiguous.
    """
    if total_claims == 0 or n_peers <= 0:
        return "ambiguous"

    zero_frac = n_zero / total_claims
    one_frac = n_one / total_claims
    other_only_frac = other_only_entries / (total_claims * n_peers)

    # OTHER-only entries force n_cover to zero (the OTHER value is excluded
    # from cover-counting), so vocabulary_failed and not_recognized peak
    # together when OTHER-only dominates. Prefer the more informative read:
    # "peers saw the dimension applies but no subtype fit" is a strict
    # superset of "peers did not cover the claim."
    if other_only_frac >= 0.5 and other_only_frac >= max(one_frac, zero_frac):
        return "vocabulary_failed"

    if one_frac > zero_frac and one_frac >= _DOMINANCE_THRESHOLD:
        return "peer_divergence"
    if zero_frac > one_frac and zero_frac >= _DOMINANCE_THRESHOLD:
        return "not_recognized"
    return "ambiguous"


def _peer_recognition_histogram(
    session: Session,
    source_model: ModelId,
    dimension: Dimension,
) -> tuple[int, int, int, int, int]:
    """Per-claim peer-recognition histogram for one (source_model, dimension).

    Returns (n_zero, n_one, n_two, other_only_entries, total_claims).

    For each Phase 1 claim authored by source_model, counts how many distinct
    non-self peer-taggers covered it (any non-OTHER tag in dimension) versus
    marked it OTHER-only (only OTHER in dimension). Self-tags excluded — they
    do not enter parity, per ARCHITECTURE.md §6.
    """
    # Index peer_tags by (tagger, claim_id) for fast lookup.
    peer_tag_index: dict[tuple[ModelId, str], ClaimTags] = {}
    for tagging in session.phase_2_taggings:
        for ct in tagging.peer_tags:
            peer_tag_index[(tagging.tagger_model, ct.claim_id)] = ct

    invited = tuple(session.models_invited)
    peers = tuple(m for m in invited if m is not source_model)

    own_claims = [
        c
        for r in session.phase_1.values()
        for c in r.claims
        if c.source_model is source_model and c.source_phase is Phase.INDEPENDENT
    ]

    n_zero = n_one = n_two = 0
    other_only_entries = 0

    for claim in own_claims:
        peers_covering = 0
        for peer in peers:
            ct = peer_tag_index.get((peer, claim.claim_id))
            if ct is None:
                continue
            tags_in_dim = (
                ct.edge_case_tags
                if dimension == EDGE_CASE_DIMENSION
                else ct.structural_pattern_tags
            )
            if not tags_in_dim:
                continue
            other_value = (
                EdgeCaseTag.OTHER
                if dimension == EDGE_CASE_DIMENSION
                else StructuralPatternTag.OTHER
            )
            has_substantive = any(t is not other_value for t in tags_in_dim)
            if has_substantive:
                peers_covering += 1
            else:
                other_only_entries += 1

        if peers_covering == 0:
            n_zero += 1
        elif peers_covering == 1:
            n_one += 1
        else:
            n_two += 1

    return n_zero, n_one, n_two, other_only_entries, len(own_claims)


def interpret_parity_flags(session: Session) -> tuple[FlagInterpretation, ...]:
    """For each parity violation in session.metrics, return a labeled reading.

    Pure sync, no LLM calls. The panel reads the result; it does not compute.
    Empty tuple when metrics is None (dyad) or no violations.

    Three readings the architecture must hold open are distinguished by the
    per-claim peer-recognition histogram; see _classify_consensus_flag and
    FlagInterpretation docstrings. A fourth reading is reserved for
    distinct_claim_share violations, which are not consensus-derived.
    """
    if session.metrics is None:
        return ()

    n_peers = len(set(session.models_invited)) - 1
    out: list[FlagInterpretation] = []

    for label, model_id, share in session.metrics.parity_violations:
        if label == "distinct_claim_share":
            out.append(
                FlagInterpretation(
                    source_model=model_id,
                    dimension_label=label,
                    share=share,
                    reading="low_claim_volume",
                )
            )
            continue

        dimension = _DIMENSION_BY_LABEL.get(label)
        if dimension is None:
            out.append(
                FlagInterpretation(
                    source_model=model_id,
                    dimension_label=label,
                    share=share,
                    reading="ambiguous",
                )
            )
            continue

        n_zero, n_one, n_two, other_only, total = _peer_recognition_histogram(
            session, model_id, dimension
        )
        reading = _classify_consensus_flag(
            n_zero, n_one, other_only, total, n_peers
        )
        out.append(
            FlagInterpretation(
                source_model=model_id,
                dimension_label=label,
                share=share,
                reading=reading,
                histogram_n_zero=n_zero,
                histogram_n_one=n_one,
                histogram_n_two=n_two,
                other_only_entries=other_only,
                total_claims=total,
            )
        )

    return tuple(out)


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
