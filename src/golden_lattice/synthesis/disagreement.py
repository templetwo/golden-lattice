"""Rule 3 — disagreement surfacing.

Operationalizes ARCHITECTURE.md §5.4 rule 3: productive disagreement (high-
confidence conflict) is surfaced, not hidden. Produces SurfacedDisagreement
artifacts that the substrate validates via Session._synthesis_surfaced_
disagreements_resolve_claim_ids and the SurfacedDisagreement constructor's
≥2 claim_ids requirement.

ENGINE-AUTHORED DEFINITION OF "PRODUCTIVE" (v0):

The spec uses "productive" without defining. Engine convention narrows the
term to a three-component definition:

  1. Both-sides high confidence — both the disagreeing peer and the
     disagreed-with claim's author have Phase 1 confidence >= threshold.
     Hedged-vs-hedged is mutual uncertainty, not productive disagreement.

  2. Structural pairing — the disagreement names ≥2 specific claims
     genuinely in opposition. The substrate's claim_ids: ≥2 requirement
     is encoding that disagreement-as-surfaced-artifact requires at least
     two specific propositions in tension.

  3. Currency — the disagreement is from Phase 2 cross-reading or Phase 3
     critique, both of which are direct disagreement signals from the
     current session.

Each component is a deliberate narrowing. Future review may revise.

V0 STAGING — what's deliberately deferred:

  - Phase 3 critique with EXACTLY 1 target_claim_id: NOT surfaced. The
    disagreer named what they disagree with but not what they think is
    true instead. The disagreement has not yet matured into structural
    opposition between specific claims. Remains in Phase 3 dialogue.
    DO NOT synthesize fake pairs to satisfy the cardinality requirement —
    that would feed the substrate's refusal mechanism with constructed
    data that meets the cardinality without honoring its architectural
    meaning.

  - Phase 2 Disagreement (always 1 target_claim_id structurally): NOT
    surfaced for the same reason. Remains in Phase 2 cross-reading.

  - Cross-source aggregation: a claim disagreed-with from both Phase 2
    and Phase 3 produces (in v0) at most one SurfacedDisagreement from
    the Phase 3 path; v1 may merge.

  - Feedback into Rule 1 (claims involved in surfaced disagreements
    should be present, not omitted) and Rule 2 (disagreed claims
    probably shouldn't be elevated): deferred to v1 composition.

V1 may introduce a separate substrate type (e.g., ContestedClaim,
UnpairedDisagreement) for single-target disagreements that don't yet
meet the SurfacedDisagreement structural bar. That would be a substrate
amendment, not a Rule 3 heuristic.

ENGINE-AUTHORED PROSE BOUNDARY:

This is the first rule where engine-authored prose enters the artifact —
SurfacedDisagreement.note. The discipline is templated, not generative:
the note's structure is determined by inputs; only variable substitutions
change. Same shape as omission_reason in Rule 1. The no-LLM-in-synthesis
commitment stays honored — engine fills templates, doesn't author novel
prose.

Note template:
  "{speaker_model} disagrees with {target_model}: {critique_content}"

Future readers can recognize the template structure and verify no
generative judgment is happening.
"""

from __future__ import annotations

from typing import Optional

from golden_lattice.memory_graph.base import ModelId
from golden_lattice.memory_graph.schema import (
    DialogueTurn,
    Session,
    SurfacedDisagreement,
)


def compute_surfaced_disagreements(
    session: Session,
    *,
    confidence_threshold: float,
) -> tuple[SurfacedDisagreement, ...]:
    """Compute SurfacedDisagreement artifacts from Phase 3 critique turns.

    Pure function, deterministic, callable in isolation. Required keyword-
    only confidence_threshold per engine discipline — defaults invisibly
    shape behavior; required parameters force deliberate choice at the
    call site.

    v0 surfaces only Phase 3 critique turns with ≥2 target_claim_ids and
    both-sides confidence >= threshold. Single-target critiques and
    Phase 2 Disagreements remain in dialogue without artifact lift.

    Returns SurfacedDisagreements in deterministic order: sorted by
    (min(claim_ids), note).
    """
    candidates: list[SurfacedDisagreement] = []

    for turn in session.phase_3:
        if turn.channel != "critique":
            continue
        if len(turn.target_claim_ids) < 2:
            continue
        if turn.target_model is None:
            # Substrate refuses critique without target_model; defensive skip.
            continue

        speaker_confidence = _phase_1_confidence_for_model(session, turn.speaker_model)
        target_confidence = _phase_1_confidence_for_model(session, turn.target_model)
        if speaker_confidence is None or target_confidence is None:
            continue
        if speaker_confidence < confidence_threshold:
            continue
        if target_confidence < confidence_threshold:
            continue

        note = _format_disagreement_note(turn)
        candidates.append(
            SurfacedDisagreement(
                claim_ids=tuple(sorted(turn.target_claim_ids)),
                note=note,
            )
        )

    return tuple(
        sorted(candidates, key=lambda sd: (min(sd.claim_ids), sd.note))
    )


def _phase_1_confidence_for_claim(session: Session, claim_id: str) -> Optional[float]:
    """Look up the Phase 1 confidence value for the model that authored this claim.

    Returns None if the claim_id is not found in any Phase 1 response — this
    can happen for Phase 2 missing claims, which have source_phase=CROSS_READING
    and don't carry Phase 1 confidence. The caller should treat None as
    'not eligible for confidence-threshold gating'.
    """
    for model, response in session.phase_1.items():
        for claim in response.claims:
            if claim.claim_id == claim_id:
                return response.confidence
    return None


def _phase_1_confidence_for_model(session: Session, model: ModelId) -> Optional[float]:
    """Look up the Phase 1 confidence value for a model. Returns None if the
    model has no Phase 1 response (shouldn't happen for invited models; defensive)."""
    response = session.phase_1.get(model)
    if response is None:
        return None
    return response.confidence


def _format_disagreement_note(critique: DialogueTurn) -> str:
    """Engine-authored prose, templated. The note's structure is determined
    by the input; only variable substitutions change. No generative judgment."""
    assert critique.target_model is not None  # Substrate enforces for critique.
    return (
        f"{critique.speaker_model.value} disagrees with "
        f"{critique.target_model.value}: {critique.content}"
    )
