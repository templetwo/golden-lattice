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

V1+V2 EXPANSION — two-peer-dispute → modified (added 2026-05-16):

When at least two distinct non-author peers disagree with a claim through
any disagreement channel — Phase 2 cross-reading OR Phase 3 critique turn
— the claim is marked `modified` with a templated hedge appended to the
original text. This is the symmetric counter to §6's consensus rule: the
recognition threshold is ≥2 of (n-1) peers, and the dispute threshold is
the same. The hedge surfaces the dispute inline so the cross-reading +
dialogue audit doesn't get dropped at the synthesis seam — the failure
mode that produced the 2026-05-16 attention-economy session in which 9+
contested numeric claims survived Phase 2/3 critique unmodified.

Channel priority within the rule: Phase 2 cross-reading is scanned first,
then Phase 3 critique. If a single peer disagrees through BOTH channels,
the Phase 2 reason wins (it's the more structured signal — reader
explicitly named what they disagree with and why). Augment and converge
channels are NOT counted as dispute signals, honoring the spec's channel
semantics rather than inferring disagreement across channels.

Threshold is ≥2 distinct non-author peers at any roster size. At N=2 only
one non-author peer exists, so the rule cannot fire (dyad behavior
preserved). At N=4+ a silent additional peer is not a veto — matching
§6's "≥2 of (n-1)" consensus scaling for the default four-seat roster.

Engagement-as-corroboration: a claim is "isolated" iff no peer references
it in EITHER agreements OR disagreements during Phase 2. Disagreement is
engagement; treating disagreement as equivalent to silence would collapse
two structurally different signals into one and risk Pattern 5 (alignment
collapse) sneaking in via the omission heuristic. The new two-peer-
dispute rule is orthogonal: low_confidence_isolated checks "is this
ignored?" and dispute checks "is this strictly contested?" — both can be
present on the same claim, but in practice they don't conflict because
dispute implies engagement.

V3 deferred — what's still not consumed:

The two-peer-dispute rule looks at Phase 2 cross-reading disagreements
and Phase 3 critique turns. It does NOT yet look at:
  - Phase 2 taggings (peer_tags with edge-case or framing-choice flags
    naming a contested pattern). Those carry dispute information but
    through a different mechanism (tag vocabulary, not free-form reason).
  - Phase 1 self_reflection_artifacts beyond weakest_claim_id (e.g., a
    model self-flagging "this contradicts the shared evidence" in
    tag_justification). Self-doubt as a dispute signal is v3+ territory.

Shape A factoring: build_claim_trace is a planning function. It decides
dispositions but does not produce prose. Prose generation is Rule 4's
concern (attribution.py). Each disposition decision is a structured artifact
the rest of the engine consumes.
"""

from __future__ import annotations

from typing import Optional

from golden_lattice.memory_graph.base import ModelId
from golden_lattice.memory_graph.schema import (
    Claim,
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

    Heuristics, applied in order:
      1. low_confidence_isolated: claim is omitted iff its author flagged it
         as weakest_claim_id in their self-reflection AND no peer's
         CrossReading engages with it (neither agreement nor disagreement).
      2. two_peer_dispute: claim is modified iff ≥2 distinct non-author
         peers disagree with it (Phase 2 cross-reading and/or Phase 3
         critique). The hedge is templated prose appended to the original
         claim text. Symmetric counter to §6's ≥2-of-(n-1) consensus rule.
      3. Default: present.

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
                continue
            disputers = _two_peer_disputers(session, claim.claim_id, author=model)
            if disputers is not None:
                entries.append(
                    ClaimTraceEntry(
                        claim_id=claim.claim_id,
                        disposition="modified",
                        modified_text=_format_dispute_hedge(claim, disputers),
                    )
                )
                continue
            entries.append(
                ClaimTraceEntry(
                    claim_id=claim.claim_id,
                    disposition="present",
                )
            )
    return tuple(entries)


def _is_low_confidence_isolated(session: Session, claim_id: str) -> bool:
    """True iff the claim is the author's self-flagged weakest AND no peer
    engaged with it via CrossReading agreement OR disagreement.

    Engagement-as-corroboration: a peer that disagreed with the claim is also
    corroborating its relevance. Treating disagreement as silence would let
    alignment-collapse patterns silently inflate omission rates against the
    consistently-dissented-with peer.
    """
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

    # Peer engagement: any CrossReading whose target_model == author and
    # whose agreements OR disagreements reference this claim_id corroborates
    # the claim's relevance.
    for cr in session.phase_2:
        if cr.target_model is author:
            for ref in cr.agreements:
                if ref.claim_id == claim_id:
                    return False  # agreed-with; not isolated.
            for d in cr.disagreements:
                if d.target_claim_id == claim_id:
                    return False  # disagreed-with is still engaged; not isolated.
    return True


def _two_peer_disputers(
    session: Session,
    claim_id: str,
    *,
    author: ModelId,
) -> Optional[list[tuple[ModelId, str]]]:
    """Return [(peer, reason), ...] when ≥2 distinct non-author peers disagree
    with the claim through any disagreement channel — Phase 2 cross-reading
    or Phase 3 critique turn. Otherwise None.

    Threshold matches ARCHITECTURE.md §5.4 / §6: at least two distinct
    non-author peers. At N=2 only one non-author peer exists, so the rule
    cannot fire. At N=4+ a silent additional peer is not a veto.

    Channel priority: Phase 2 cross-reading scanned first, then Phase 3
    critique. If a peer disagrees in both, the Phase 2 reason wins (the
    structured channel where the reader explicitly named the claim and
    why; Phase 3 critique can be more general dialogue). The priority is
    locked so the hedge's reasoning is reproducible across runs.

    Augment and converge channels are NOT disagreement signals — same
    boundary Rule 3 enforces, honoring the spec's channel semantics rather
    than inferring disagreement across channels.

    Peers in the returned list are sorted by model_id.value for
    deterministic downstream formatting. The reason captured per peer is
    the first matching disagreement encountered (cross-readings and
    dialogue turns within a Session are tuple-ordered by construction).
    """
    non_author_peers = set(session.models_invited) - {author}

    found: dict[ModelId, str] = {}

    # Phase 2 cross-reading disagreements — structured channel, scanned first.
    for cr in session.phase_2:
        if cr.target_model is not author:
            continue
        if cr.reader_model not in non_author_peers:
            continue
        if cr.reader_model in found:
            continue  # First disagreement per peer wins; rest skipped for determinism.
        for d in cr.disagreements:
            if d.target_claim_id == claim_id:
                found[cr.reader_model] = d.reason
                break

    # Phase 3 critique turns — fill in peers not already disputing via Phase 2.
    for turn in session.phase_3:
        if turn.channel != "critique":
            continue
        if turn.target_model is not author:
            continue
        if turn.speaker_model not in non_author_peers:
            continue
        if turn.speaker_model in found:
            continue  # Phase 2 already captured this peer; preserve channel priority.
        if claim_id in turn.target_claim_ids:
            found[turn.speaker_model] = turn.content

    if len(found) < 2:
        return None
    return sorted(found.items(), key=lambda kv: kv[0].value)


# Engine-authored prose, templated. Same discipline as Rule 3's
# SurfacedDisagreement.note: the structure is determined by inputs; only
# variable substitutions change. No generative judgment.
_DISPUTE_HEDGE_EXCERPT_CHARS: int = 120


def _format_dispute_hedge(
    claim: Claim,
    disputers: list[tuple[ModelId, str]],
) -> str:
    """Templated hedge prose. No LLM, deterministic.

    Format: "{claim.text} [DISPUTED — {peer}: '{excerpt}'; ...]"
    Peer order follows the already-sorted disputers list (≥2 entries).

    The excerpt is the first sentence of the reason (up to '. ', '! ', or
    '? '), truncated at _DISPUTE_HEDGE_EXCERPT_CHARS characters with an
    ellipsis suffix when truncation happens. Full reasons remain queryable
    on session.phase_2[*].disagreements — the hedge surfaces the dispute
    inline without flooding the rendered output.
    """
    parts = "; ".join(
        f'{peer.value}: "{_excerpt(reason)}"' for peer, reason in disputers
    )
    return f"{claim.text} [DISPUTED — {parts}]"


def _excerpt(reason: str, max_chars: int = _DISPUTE_HEDGE_EXCERPT_CHARS) -> str:
    """First sentence of the reason, truncated. Deterministic, no LLM.

    Boundary detection: smallest index across '. ', '! ', '? '. If found
    within max_chars, return through (and including) that punctuation. If
    no early boundary, truncate to max_chars and append '...'.
    """
    earliest: Optional[int] = None
    for marker in (". ", "! ", "? "):
        idx = reason.find(marker)
        if 0 < idx and (earliest is None or idx < earliest):
            earliest = idx
    if earliest is not None and earliest + 1 <= max_chars:
        return reason[: earliest + 1].strip()
    stripped = reason.strip()
    if len(stripped) <= max_chars:
        return stripped
    return stripped[:max_chars].rstrip() + "..."
