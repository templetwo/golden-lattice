"""Rule 4 — attribution preservation and output mode rendering.

Operationalizes ARCHITECTURE.md §7 (output modes) and §5.4 rule 4. Produces
the SynthesisArtifact's output: str field by dispatching to one of four
mode renderers. Pure function. No LLM calls. Templated, deterministic,
inspectable.

ANNOTATED AS CANONICAL PROOF-FORM:

Per §7's load-bearing line — "Default: annotated. The annotation is the
proof we did not flatten" — annotated mode is the architecturally central
rendering. The other three modes derive from or transform around it:

  - unified  = annotated with markers stripped (mechanical)
  - layered  = peer-voice-by-emission-order (NOT regrouped annotated)
  - transcript = bypass synthesis rendering entirely; Phase 1-3 raw

ENGINE CONVENTIONS (v0):

Marker abbreviation. The original spec specifies [O]/[S]/[H]. The roster now
includes Fable, so the marker vocabulary is explicitly extended with [F].
Markers remain a closed, deterministic mapping rather than being inferred
from model names.

Marker placement: prefix. "[O] LRU is the right default." Reads as
"according to" — academic-citation convention.

Segment unit: claim-level. Each ClaimTraceEntry becomes one rendered
segment. No sentence-level or paragraph-level decomposition; the unit of
templating matches the unit of substrate identity.

Trailers in output: str. None in v0. Elevations and surfaced disagreements
live on SynthesisArtifact.elevations and .surfaced_disagreements fields,
queryable separately. The output: str field is the rendered prose only.
One field, one job. v1 may add a "render with trailers" helper.

Disposition rendering rules (annotated, unified):
  - present  → claim.text with marker
  - modified → claim_trace_entry.modified_text with marker
  - omitted  → not rendered in output: str (still in claim_trace for query)

Disposition rendering rules (layered) — diverges from annotated:
  - present  → claim.text (original) under the source-model section header
  - modified → claim.text (original; NOT modified_text) — layered shows
               what each model SAID, not what synthesis USED from them.
               The synthesis's modifications are presentation choices that
               belong in annotated mode where synthesis flow is the
               organizing principle. Layered mode preserves peer-voice
               integrity.
  - omitted  → not rendered. Same as other modes.

Layered section ordering: alphabetical by model.value (deterministic).
Within-section: claims in their Phase 1 EMISSION order, not claim_trace
order. Layered mode shows each peer's argument as the peer made it; the
synthesis's reweaving belongs to annotated mode.

Layered section headers: "=== claude-opus-5 ===" — consistent with
transcript-mode section markers; uses the canonical model identifier
rather than introducing a parallel human-readable scheme.

Unified mode: mechanical strip-and-concatenate. No generative connective
text ("Additionally," "Moreover," etc.) — that would cross the templated-
not-generative discipline. The result reads disjointedly compared to a
model-authored summary; that's the architectural cost of staying templated.
v1 may consider a closed connective vocabulary if disjointedness becomes a
real problem on real session data.

Transcript mode: runs the full synthesis pipeline (Rules 1, 2, 3 still
produce their artifacts; substrate validators still apply for
irreducibility preservation, etc.) but the renderer ignores their
outputs when building output: str. The artifact remains substrate-valid
and queryable; only the rendered string is Phase 1-3 raw. Subtle but
important: transcript is "running synthesis without rendering its
output," NOT "bypassing synthesis."

ENGINE-AUTHORED-PROSE-AT-SCALE DISCIPLINE:

Three modes (annotated, layered, unified) involve synthesis prose
composition. The unit of templating stays small (a claim segment),
regardless of synthesis size. A synthesis with 30 claims renders as 30
templated segments concatenated per mode rules. Each segment is
"marker_prefix + rendered_text" or "section_header + claim_text" with
shared helpers. The renderer doesn't compose novel sentences; it
concatenates templated segments. Same discipline as Rule 3's
SurfacedDisagreement.note format, scaled by repetition rather than by
generative complexity.
"""

from __future__ import annotations

from golden_lattice.memory_graph.base import (
    ModelId,
    OutputMode,
    Phase,
)
from golden_lattice.memory_graph.schema import (
    Claim,
    ClaimTraceEntry,
    DialogueTurn,
    Elevation,
    IndependentResponse,
    Session,
    SurfacedDisagreement,
)


# Marker mapping per ARCHITECTURE.md §7. Explicit so attribution remains
# deterministic and cannot collide when model names share an initial.
_MARKER_BY_MODEL: dict[ModelId, str] = {
    ModelId.FABLE: "[F]",
    ModelId.OPUS: "[O]",
    ModelId.SONNET: "[S]",
    ModelId.HAIKU: "[H]",
    ModelId.LEGACY_OPUS_4_7: "[O]",
    ModelId.LEGACY_SONNET_4_6: "[S]",
    ModelId.LEGACY_HAIKU_4_5: "[H]",
}


def render_output(
    session: Session,
    *,
    mode: OutputMode,
    claim_trace: tuple[ClaimTraceEntry, ...],
    elevations: tuple[Elevation, ...],
    surfaced_disagreements: tuple[SurfacedDisagreement, ...],
) -> str:
    """Pure function. Kernel-syscall-shape. Dispatches to mode renderers.

    All four modes accept the same inputs for signature uniformity, even
    though transcript mode ignores Rule 1/2/3 outputs. Determinism: same
    inputs in, same string out, byte-equal across runs.
    """
    if mode is OutputMode.ANNOTATED:
        return _render_annotated(session, claim_trace)
    if mode is OutputMode.LAYERED:
        return _render_layered(session, claim_trace)
    if mode is OutputMode.UNIFIED:
        return _render_unified(session, claim_trace)
    if mode is OutputMode.TRANSCRIPT:
        return _render_transcript(session)
    raise ValueError(f"Unknown OutputMode: {mode!r}")


# ---------------------------------------------------------------------------
# Shared helpers.
# ---------------------------------------------------------------------------


def _attribution_marker(model: ModelId) -> str:
    """Return the explicit attribution marker for a model."""
    if model not in _MARKER_BY_MODEL:
        raise ValueError(
            f"No attribution marker defined for {model.value}. Extend "
            "_MARKER_BY_MODEL when a new ModelId is added."
        )
    return _MARKER_BY_MODEL[model]


def _phase_1_claims_by_id(session: Session) -> dict[str, Claim]:
    """Map claim_id → Claim for every Phase 1 claim. Used by all renderers
    to look up source_model and original text."""
    return {
        claim.claim_id: claim
        for response in session.phase_1.values()
        for claim in response.claims
    }


# ---------------------------------------------------------------------------
# Annotated mode — canonical proof-form.
# ---------------------------------------------------------------------------


def _render_annotated(
    session: Session,
    claim_trace: tuple[ClaimTraceEntry, ...],
) -> str:
    """Render annotated mode: synthesis prose with inline model markers.

    Iterates claim_trace in order. Each segment is "{marker} {text}\n".
    For modified claims: text is modified_text. For omitted claims: skipped
    in output (still preserved in claim_trace for query). Order matches
    claim_trace order — that order is the synthesis's flow per Rule 1.
    """
    claims_by_id = _phase_1_claims_by_id(session)
    segments: list[str] = []
    for entry in claim_trace:
        if entry.disposition == "omitted":
            continue
        claim = claims_by_id.get(entry.claim_id)
        if claim is None:
            # Phase 2 missing claims have source_phase=CROSS_READING; not in
            # phase_1 lookup. Skip in v0 — claim_trace covers Phase 1 only.
            continue
        marker = _attribution_marker(claim.source_model)
        text = entry.modified_text if entry.disposition == "modified" else claim.text
        segments.append(f"{marker} {text}")
    return "\n".join(segments)


# ---------------------------------------------------------------------------
# Layered mode — peer-voice-by-emission-order.
# ---------------------------------------------------------------------------


def _render_layered(
    session: Session,
    claim_trace: tuple[ClaimTraceEntry, ...],
) -> str:
    """Render layered mode: per-model sections, claims in Phase 1 emission order.

    Diverges from annotated:
      - Order: claims appear in their Phase 1 emission order within each
        section, NOT claim_trace order. Layered mode preserves peer-voice
        as the peer made it.
      - Source text: claim.text (original), NOT modified_text. Layered
        shows what each model SAID, not what synthesis USED from them.
      - Section headers: "=== model.value ===" — canonical model identifier.

    Omitted claims are skipped (they're not part of what the model
    contributed to the surviving synthesis). That preserves consistency
    with annotated/unified modes' omission handling.
    """
    traced_claim_ids: dict[str, ClaimTraceEntry] = {
        entry.claim_id: entry for entry in claim_trace
    }
    sections: list[str] = []
    sorted_models = sorted(session.phase_1.keys(), key=lambda m: m.value)
    for model in sorted_models:
        response = session.phase_1[model]
        rendered_claims: list[str] = []
        for claim in response.claims:  # Phase 1 emission order.
            entry = traced_claim_ids.get(claim.claim_id)
            if entry is None or entry.disposition == "omitted":
                continue
            # Layered renders ORIGINAL claim.text, even for modified disposition.
            rendered_claims.append(claim.text)
        if not rendered_claims:
            continue  # Don't emit empty sections.
        section_body = "\n".join(rendered_claims)
        sections.append(f"=== {model.value} ===\n{section_body}")
    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Unified mode — mechanical strip-and-concatenate.
# ---------------------------------------------------------------------------


def _render_unified(
    session: Session,
    claim_trace: tuple[ClaimTraceEntry, ...],
) -> str:
    """Render unified mode: synthesis prose, attribution stripped.

    Mechanical transformation of annotated: same content, same order,
    markers removed. No generative connective text. Result reads
    disjointedly compared to a model-authored summary — that's the
    architectural cost of staying templated.
    """
    claims_by_id = _phase_1_claims_by_id(session)
    segments: list[str] = []
    for entry in claim_trace:
        if entry.disposition == "omitted":
            continue
        claim = claims_by_id.get(entry.claim_id)
        if claim is None:
            continue
        text = entry.modified_text if entry.disposition == "modified" else claim.text
        segments.append(text)
    return "\n".join(segments)


# ---------------------------------------------------------------------------
# Transcript mode — Phase 1-3 raw.
# ---------------------------------------------------------------------------


def _render_transcript(session: Session) -> str:
    """Render transcript mode: full Phase 1-3 dialogue, raw.

    Bypasses Phase 4 rendering. The SynthesisArtifact still carries
    claim_trace, elevations, surfaced_disagreements (Rules 1-3 ran), but
    those don't appear in output: str. Subtle: transcript runs synthesis
    without rendering its output, NOT bypasses synthesis.

    Sections:
      === Phase 1 ===
        Per model (alphabetical): response, focus_tag, confidence,
        claims, self-reflection artifacts.
      === Phase 2 ===
        Cross-readings (reader → target with agreements/disagreements/missing)
        and Phase 2 taggings.
      === Phase 3 ===
        Dialogue turns in turn_id order.
    """
    parts: list[str] = []
    parts.append("=== Phase 1 ===")
    sorted_models = sorted(session.phase_1.keys(), key=lambda m: m.value)
    for model in sorted_models:
        parts.append(_render_phase_1_response(model, session.phase_1[model]))

    parts.append("\n=== Phase 2 ===")
    parts.append(_render_phase_2(session))

    parts.append("\n=== Phase 3 ===")
    parts.append(_render_phase_3(session))

    return "\n".join(parts)


def _render_phase_1_response(model: ModelId, response: IndependentResponse) -> str:
    lines = [f"--- {model.value} ---"]
    lines.append(f"focus_tag: {response.focus_tag.value}")
    lines.append(f"confidence: {response.confidence}")
    lines.append(f"response: {response.response}")
    lines.append("claims:")
    for claim in response.claims:
        lines.append(f"  - {claim.claim_id}: {claim.text}")
    if response.self_reflection_artifacts:
        lines.append("self_reflection:")
        for artifact in response.self_reflection_artifacts:
            lines.append(
                f"  strongest: {artifact.strongest_claim_id}; "
                f"weakest: {artifact.weakest_claim_id}; "
                f"justification: {artifact.tag_justification}"
            )
    return "\n".join(lines)


def _render_phase_2(session: Session) -> str:
    if not session.phase_2 and not session.phase_2_taggings:
        return "(no Phase 2 activity)"
    lines: list[str] = []
    for cr in session.phase_2:
        lines.append(f"{cr.reader_model.value} → {cr.target_model.value}")
        if cr.agreements:
            lines.append("  agreements:")
            for ref in cr.agreements:
                lines.append(f"    - {ref.claim_id}")
        if cr.disagreements:
            lines.append("  disagreements:")
            for d in cr.disagreements:
                lines.append(f"    - {d.target_claim_id}: {d.reason}")
        if cr.missing:
            lines.append("  missing (surfaced by reader):")
            for c in cr.missing:
                lines.append(f"    - {c.claim_id}: {c.text}")
    for tagging in session.phase_2_taggings:
        lines.append(f"tagging by {tagging.tagger_model.value} (vocab {tagging.vocabulary_version})")
    return "\n".join(lines) if lines else "(no Phase 2 activity)"


def _render_phase_3(session: Session) -> str:
    if not session.phase_3:
        return "(no Phase 3 dialogue)"
    sorted_turns = sorted(session.phase_3, key=lambda t: t.turn_id)
    lines: list[str] = []
    for turn in sorted_turns:
        target = turn.target_model.value if turn.target_model is not None else "(general)"
        target_claims = (
            ", ".join(turn.target_claim_ids) if turn.target_claim_ids else "(none)"
        )
        lines.append(
            f"[{turn.turn_id}] {turn.speaker_model.value} ({turn.channel}) "
            f"→ {target} | claim_ids: {target_claims}"
        )
        lines.append(f"  {turn.content}")
    return "\n".join(lines)
