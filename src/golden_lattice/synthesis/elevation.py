"""Rule 2 — agreement elevation.

Operationalizes ARCHITECTURE.md §5.4 rule 2: cross-model agreement
(appearing in 2+ converge channels) is elevated. Produces Elevation
artifacts that the substrate validates via Session._synthesis_elevations_
well_formed (≥2 distinct speakers among cited turns; converge_turn_ids
resolve to phase_3 turns with channel='converge'; claim_ids resolve).

Heuristic for v0:

  A converge turn with non-empty target_claim_ids is an elevation candidate.
  Converge turns from distinct speakers whose target_claim_ids share at
  least one claim get merged into a single Elevation citing all those
  converge_turn_ids. The shared claim_ids form the elevated set.

  General-converge turns (target_claim_ids empty) do NOT feed elevation —
  the spec phrasing 'agreement on a claim' requires specificity, and the
  Elevation schema requires non-empty claim_ids. General-converge turns
  remain in the Session as Phase 3 dialogue and may be represented
  differently in v1 synthesis.

V0 STAGING — what's deliberately deferred:

  - General-converge turns (no target_claim_ids) are not consumed.
  - Phase 2 cross-reading agreements are not consumed; only Phase 3
    converge turns feed elevation in v0.
  - Cross-phase signal alignment (e.g., Phase 2 agreement reinforcing a
    Phase 3 converge) is not yet folded.
  - V1 may introduce: general-converge representation, cross-phase
    composition, and feedback into Rule 1's omission decisions
    (redundant_with_elevated_agreement).

KERNEL-SYSCALL-SHAPE DESIGN:

  compute_elevations is callable in isolation by any future userspace
  surface (CLI, API, evaluation harness, research tool) that wants to
  compute elevations on a Session without running full synthesis. The
  signature accepts a Session and returns a tuple of Elevations. No
  hidden engine state, no implicit composition order. Pure function.
"""

from __future__ import annotations

from golden_lattice.memory_graph.base import ModelId
from golden_lattice.memory_graph.schema import (
    DialogueTurn,
    Elevation,
    Session,
)


def compute_elevations(session: Session) -> tuple[Elevation, ...]:
    """Compute Elevation artifacts from Phase 3 converge turns.

    Pure function, deterministic, callable in isolation. v0 considers only
    converge turns with non-empty target_claim_ids; general-converge turns
    are not consumed.

    Algorithm:
      1. Filter Phase 3 to converge turns with non-empty target_claim_ids.
      2. Group converge turns by overlapping claim_id sets via union-find:
         turns whose target_claim_ids share at least one claim are merged
         into the same group, with the group's claim_id set being the union.
      3. For each group with at least 2 distinct speaker_models, emit one
         Elevation citing all the group's converge_turn_ids and the union
         of their claim_ids.
      4. Sort emitted Elevations by (min(converge_turn_ids), claim_ids) for
         determinism — see comment at the sort site.

    Note on target_model: target_model on a converge turn is informational
    for elevation purposes. Convergence is recognition of shared claims,
    not pairwise acknowledgment. Two speakers converging on the same
    claim_ids constitute cross-model agreement regardless of which peer
    each names as target_model. The substrate enforces target_model !=
    speaker_model and target_model required when target_claim_ids is
    non-empty; Rule 2 grouping is driven by speaker_model and claim_ids
    only.

    Returns elevations in deterministic order.
    """
    targeted_converges = [
        turn
        for turn in session.phase_3
        if turn.channel == "converge" and turn.target_claim_ids
    ]
    if not targeted_converges:
        return ()

    # Sort turns by turn_id for deterministic group-merge order. turn_id is
    # caller-assigned by the orchestrator (content-stable in practice but the
    # substrate doesn't enforce content-addressing on turn_id).
    sorted_turns = sorted(targeted_converges, key=lambda t: t.turn_id)

    groups = _merge_overlapping_claim_groups(sorted_turns)

    elevations: list[Elevation] = []
    for turn_ids, claim_ids, speakers in groups:
        if len(speakers) < 2:
            continue  # Substrate would refuse anyway; pre-filter for clarity.
        elevations.append(
            Elevation(
                claim_ids=tuple(sorted(claim_ids)),
                converge_turn_ids=tuple(sorted(turn_ids)),
            )
        )

    # Sort with a secondary key on claim_ids: turn_ids are unique under valid
    # Session construction so primary key alone suffices today, but secondary
    # is defensive — if a future caller constructs Elevations with overlapping
    # turn_id sets, the spec-determined claim_ids tiebreaker keeps ordering
    # deterministic without relying on Python sort-stability + input-order
    # implementation details.
    return tuple(
        sorted(elevations, key=lambda e: (min(e.converge_turn_ids), e.claim_ids))
    )


def _merge_overlapping_claim_groups(
    turns: list[DialogueTurn],
) -> list[tuple[set[str], set[str], set[ModelId]]]:
    """Union-find over converge turns: turns whose target_claim_ids share at
    least one claim end up in the same group.

    Returns a list of (turn_ids, claim_ids, speaker_models) per group.
    """
    # Each group: (set of turn_ids, set of claim_ids, set of speaker_models).
    groups: list[tuple[set[str], set[str], set[ModelId]]] = []

    for turn in turns:
        turn_claim_set = set(turn.target_claim_ids)
        # Find groups whose claim_ids overlap with this turn's claim_ids.
        overlapping_indices = [
            i for i, (_, gclaims, _) in enumerate(groups)
            if gclaims & turn_claim_set
        ]
        if not overlapping_indices:
            groups.append(
                ({turn.turn_id}, turn_claim_set, {turn.speaker_model})
            )
            continue
        # Merge this turn into the first overlapping group, then absorb any
        # other overlapping groups into it (claim sets may transitively connect).
        primary = overlapping_indices[0]
        primary_turns, primary_claims, primary_speakers = groups[primary]
        primary_turns.add(turn.turn_id)
        primary_claims |= turn_claim_set
        primary_speakers.add(turn.speaker_model)
        # Absorb other overlapping groups.
        for idx in sorted(overlapping_indices[1:], reverse=True):
            t, c, s = groups.pop(idx)
            primary_turns |= t
            primary_claims |= c
            primary_speakers |= s
    return groups
