"""Rule 1 — irreducibility preservation.

Operationalizes ARCHITECTURE.md §5.4 rule 1: every distinct Phase 1 claim
(per the peer-utility test in the Phase 1 prompt) is preserved or has a
logged reason for omission. This rule is what makes the substrate's
_synthesis_traces_every_phase_1_claim refusal pass for real Sessions.

Disposition vocabulary — the planner's three decisions per claim:

  - present: the claim survives unchanged into synthesis. Default disposition.
  - modified: the claim's content survives but the text was rewritten.
              Requires modified_text on the trace entry.
  - omitted: the claim does not appear in synthesis. Requires omission_reason.

Closed internal omission_reason vocabulary (engine convention; substrate
accepts any non-empty string but the engine produces only these formats so
omission patterns become inspectable across sessions):

  - "low_confidence_isolated:{confidence}"
        Author's self-reflection flagged this claim as weakest_claim_id AND
        no peer corroborated it via CrossReading agreement.
        confidence is the author's reported [0,1] confidence.

  - "subsumed_by_other_claim:{citing_claim_id}"   [v1+, not produced in v0]
        This claim's content is captured by another claim cited by ID.

  - "superseded_by_phase_3_revision:{turn_id}"    [v1+, not produced in v0]
        Claim was modified in Phase 3 dialogue; the revision turn is in
        synthesis instead.

  - "redundant_with_elevated_agreement:{elevation_index}"  [v1+, not produced in v0]
        Claim's content is part of an elevated agreement and has been
        absorbed into the Elevation artifact.

v0 emits only low_confidence_isolated. The other three are documented
forward; v1 expands as additional rules' outputs feed back into trace
decisions. The substrate's permissiveness on omission_reason allows the
vocabulary to grow without schema amendments.

Open thread on the chronicle (2026-05-03): should the omission_reason
vocabulary be lifted from engine convention to substrate enum? Defer until
the v0 vocabulary stabilizes against real session data.

Shape A factoring: build_claim_trace is a planning function. It decides
dispositions but does not produce prose. Prose generation is Rule 4's
concern (attribution.py). Each disposition decision is a structured artifact
the rest of the engine consumes.
"""

from __future__ import annotations

from golden_lattice.memory_graph.schema import (
    ClaimTraceEntry,
    Session,
)


# Internal closed vocabulary for omission_reason. Substrate accepts any
# non-empty string; the engine emits only these formats.
OMISSION_REASON_PREFIXES: tuple[str, ...] = (
    "low_confidence_isolated:",
    "subsumed_by_other_claim:",
    "superseded_by_phase_3_revision:",
    "redundant_with_elevated_agreement:",
)


def build_claim_trace(session: Session) -> tuple[ClaimTraceEntry, ...]:
    """Plan a disposition for every Phase 1 claim. Total trace.

    v0 heuristic:
      - low_confidence_isolated: claim is omitted iff its author flagged it
        as weakest_claim_id in their self-reflection AND no peer's
        CrossReading agreements include the claim_id.
      - All other claims default to present.

    Returns one ClaimTraceEntry per Phase 1 claim, in the order claims appear
    by (sorted invited model_id, then claim order within the response). Sorted
    keys make the function deterministic across runs regardless of dict
    insertion order.
    """
    entries: list[ClaimTraceEntry] = []
    sorted_models = sorted(session.phase_1.keys(), key=lambda m: m.value)
    for model in sorted_models:
        response = session.phase_1[model]
        for claim in response.claims:
            if _is_low_confidence_isolated(session, claim.claim_id):
                entries.append(
                    ClaimTraceEntry(
                        claim_id=claim.claim_id,
                        disposition="omitted",
                        omission_reason=(
                            f"low_confidence_isolated:{response.confidence}"
                        ),
                    )
                )
            else:
                entries.append(
                    ClaimTraceEntry(
                        claim_id=claim.claim_id,
                        disposition="present",
                    )
                )
    return tuple(entries)


def _is_low_confidence_isolated(session: Session, claim_id: str) -> bool:
    """True iff the claim is the author's self-flagged weakest AND no peer
    corroborated it via CrossReading agreement."""
    # Find author by scanning phase_1 responses.
    author = None
    for model, response in session.phase_1.items():
        for claim in response.claims:
            if claim.claim_id == claim_id:
                author = model
                break
        if author is not None:
            break
    if author is None:
        return False

    response = session.phase_1[author]
    flagged_weakest = any(
        artifact.weakest_claim_id == claim_id
        for artifact in response.self_reflection_artifacts
    )
    if not flagged_weakest:
        return False

    # Peer corroboration: any CrossReading whose target_model == author and
    # whose agreements contain this claim_id counts as corroboration.
    for cr in session.phase_2:
        if cr.target_model is author:
            for ref in cr.agreements:
                if ref.claim_id == claim_id:
                    return False  # corroborated; not isolated.
    return True
