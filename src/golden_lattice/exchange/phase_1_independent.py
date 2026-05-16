"""Phase 1 wire format — independent generation + self-reflection during latency gaps.

ORCHESTRATOR-LAYER INVARIANTS (live above this module).

Single-emission-per-model-per-session is an orchestrator invariant, not a wire or
schema invariant. The wire layer is stateless by design; the schema treats Phase 1
as a dict[ModelId, IndependentResponse] which structurally permits at most one
entry per model but cannot detect re-emission attempts that would silently
overwrite. The orchestrator must track per-model emission state.

This is the first invariant in Golden Lattice that lives at the orchestrator layer
rather than the schema or wire layer. Future orchestrator-layer invariants should
be enumerated here as they are identified, so the orchestrator design can carry
them as a complete list rather than discovering them piecemeal.

Pure logic. No network. Two separate tool calls per model per session, by design:

  1. emit_phase_1_response — the model produces its response, focus_tag, confidence,
     and claim texts. Phase 1 is independent generation; nothing peer-visible exists
     when this fires.

  2. emit_self_reflection — produced during the idle latency gap after the model's
     own Phase 1 response is fixed but before Phase 2 cross-reading begins. The
     reflection is *preparation for Phase 2*, not refinement of Phase 1. Two-call
     structure preserves the temporal asymmetry the spec requires.

Wire-layer refusals (in addition to substrate's structural refusals):

  - Attribution mismatch: parser fixes model_id to expected; refuses if model claims
    to be someone else.
  - Focus_tag must be from the closed vocabulary; otherwise refused.
  - Confidence must be in [0, 1]; otherwise refused.
  - Self-reflection strongest/weakest must reference the model's own claim_ids;
    refused otherwise. (Substrate also refuses this; wire catches it earlier.)

Same translation collapse concern as Phase 2 applies. Two deliberate prompt choices
worth flagging in the audit:

  - In build_phase_1_response_prompt: "break your response into distinct claims"
    biases toward decomposition. A model whose natural output is a flowing argument
    might over-fragment to comply. This is asymmetric protection — distinct claims
    are required for content-addressed claim_id assignment and downstream tracing.
    But it could pull toward fragmentation that the model wouldn't produce naturally.

  - In build_self_reflection_prompt: "This is preparation for Phase 2 cross-reading,
    NOT refinement of Phase 1" warns against editing in light of reflection. Asymmetric
    protection because models might be tempted to refine. But naming Phase 2 explicitly
    might bias which claim gets picked as strongest/weakest — through the lens of
    "what will be useful in Phase 2" rather than "what is actually strongest/weakest
    in Phase 1." Subtle translation-collapse risk; flagged for audit.

Confidence prompt deliberately neutral: "a confidence score in [0, 1] for your
response." No editorializing ("be honest", "calibrated to") that would pull the
distribution. Closed-vocabulary focus_tag listed without ordering or emphasis.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional, Protocol, runtime_checkable

from golden_lattice.memory_graph.base import (
    FocusTag,
    ModelId,
    Phase,
    claim_id_for,
)
from golden_lattice.memory_graph.schema import (
    Claim,
    IndependentResponse,
    SelfReflectionArtifact,
)
from golden_lattice.exchange.phase_2_cross_reading import WireParseError


# ---------------------------------------------------------------------------
# Tool schemas — derived from substrate, wrapped for Anthropic tool-use.
# ---------------------------------------------------------------------------

PHASE_1_RESPONSE_TOOL_NAME = "emit_phase_1_response"
SELF_REFLECTION_TOOL_NAME = "emit_self_reflection"


def phase_1_response_tool_schema() -> dict[str, Any]:
    """Anthropic tool schema for emitting a Phase 1 response.

    Model emits response prose, focus_tag from closed vocab, confidence in [0,1],
    and claim_texts. claim_ids are assigned post-parse via content hash.
    """
    focus_tag_values = [t.value for t in FocusTag]
    return {
        "name": PHASE_1_RESPONSE_TOOL_NAME,
        "description": (
            "Emit your Phase 1 response. Phase 1 is independent generation — peer "
            "outputs do not exist yet. Produce response prose, choose a focus_tag, "
            "report a confidence score, and decompose your response into distinct "
            "claim texts. claim_ids are assigned by the wire layer."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["response", "focus_tag", "confidence", "claim_texts"],
            "properties": {
                "response": {
                    "type": "string",
                    "description": "The full prose response to the prompt.",
                },
                "focus_tag": {
                    "type": "string",
                    "enum": focus_tag_values,
                    "description": "The dimension you most prioritized.",
                },
                "confidence": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "description": "A confidence score in [0, 1] for your response.",
                },
                "claim_texts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Distinct claim texts from your response. Each becomes a "
                        "Phase 1 claim with a content-addressed claim_id."
                    ),
                },
            },
        },
    }


def self_reflection_tool_schema() -> dict[str, Any]:
    """Anthropic tool schema for emitting a self-reflection during latency gap."""
    return {
        "name": SELF_REFLECTION_TOOL_NAME,
        "description": (
            "Emit a structured self-reflection on your own Phase 1 response. This "
            "is preparation for Phase 2, not refinement of Phase 1. Reference your "
            "own claims by claim_id."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "strongest_claim_id",
                "weakest_claim_id",
                "tag_justification",
            ],
            "properties": {
                "strongest_claim_id": {
                    "type": "string",
                    "description": "claim_id of your strongest Phase 1 claim.",
                },
                "weakest_claim_id": {
                    "type": "string",
                    "description": (
                        "claim_id of your weakest Phase 1 claim. Must differ from "
                        "strongest_claim_id."
                    ),
                },
                "tag_justification": {
                    "type": "string",
                    "description": (
                        "Why you chose your focus_tag for this response."
                    ),
                },
            },
        },
    }


# ---------------------------------------------------------------------------
# Prompt builders.
# ---------------------------------------------------------------------------


def _format_feed_for_phase_1(feed) -> str:
    """Render a Phase 0 frozen feed as a SHARED EVIDENCE section for the
    Phase 1 user prompt. The feed is symmetrically visible to all three
    peers (per §5.0); this is the inlining that makes that visibility
    operative at generation time.

    Empty/None feed returns empty string — no section, backward compat.
    """
    if not feed:
        return ""

    # Local imports keep the schema dependency optional at module-import
    # time and self-contained inside this rendering helper.
    from golden_lattice.memory_graph.phase_0 import (
        DateTimeGrounding,
        FailedSearch,
        SearchResult,
    )
    try:
        from golden_lattice.memory_graph.phase_0 import InvestigationSummary
        has_summary = True
    except ImportError:  # pragma: no cover — until slice C ships
        InvestigationSummary = None  # type: ignore[assignment]
        has_summary = False

    parts: list[str] = []
    parts.append("SHARED EVIDENCE (Phase 0 investigation — all three peers see this verbatim):")
    parts.append("")
    for entry in feed:
        if isinstance(entry, DateTimeGrounding):
            parts.append(f"  [temporal grounding] {entry.formatted_text}")
        elif isinstance(entry, SearchResult):
            parts.append(f"  [investigation result] query: {entry.query}")
            if entry.source_urls:
                parts.append(f"    sources: {', '.join(entry.source_urls)}")
            # Cap rendered content to keep the prompt bounded.
            content = entry.result_text.strip()
            if len(content) > 4000:
                content = content[:4000] + " … [truncated]"
            parts.append(f"    content:\n    {content}")
        elif isinstance(entry, FailedSearch):
            parts.append(
                f"  [investigation attempted, did not return content] "
                f"query: {entry.query}  reason: {entry.reason}"
            )
        elif has_summary and isinstance(entry, InvestigationSummary):
            parts.append(
                f"  [investigation summary by {entry.source_model.value}]"
            )
            parts.append(f"    summary: {entry.summary_text}")
            if entry.source_urls:
                parts.append(f"    sources: {', '.join(entry.source_urls)}")
        else:  # pragma: no cover
            parts.append(f"  [unknown feed entry type] {type(entry).__name__}")
    parts.append("")
    parts.append("End of shared evidence. Use it where it informs your response; cite the source URL when grounding a claim in fetched content.")
    return "\n".join(parts)


def build_phase_1_response_prompt(
    *,
    model_id: ModelId,
    original_prompt: str,
    feed=None,
) -> tuple[str, str]:
    """Returns (system, user) messages for Phase 1 independent generation.

    The optional `feed` parameter carries the Phase 0 investigation feed
    (tuple of FeedEntry). When provided and non-empty, the feed contents
    are inlined into the user prompt as a SHARED EVIDENCE section so all
    three peers generate on the same symmetric evidence layer (per §5.0).
    Backward compatible: no feed → no extra section, same prompt as the
    pre-amendment Phase 1.
    """
    focus_values = ", ".join(t.value for t in FocusTag)

    system = (
        f"You are a Lattice peer producing Phase 1 (independent generation).\n"
        f"You are: {model_id.value}\n"
        "\n"
        "Phase 1 is independent generation. You do not see peer outputs at this "
        "stage; they do not exist yet. Respond to the prompt as you see it.\n"
        "\n"
        f"After your response, label your work: pick a focus_tag from "
        f"{{{focus_values}}} naming the dimension you most prioritized, and report "
        f"a confidence score in [0, 1] for your response. This is logged for "
        f"analysis; it does not weight your contribution downstream.\n"
        "\n"
        f"Break your response into distinct claim texts. Each claim is a "
        f"load-bearing argument unit — something a peer could agree with, "
        f"disagree with, or extend. A claim is too small if peers couldn't "
        f"meaningfully tag it; too large if it bundles multiple arguments peers "
        f"might respond to differently.\n"
        "\n"
        f"Use the {PHASE_1_RESPONSE_TOOL_NAME} tool to emit your response. Free-form "
        "prose outside the tool will be ignored."
    )

    feed_section = _format_feed_for_phase_1(feed)
    if feed_section:
        user = f"Prompt:\n{original_prompt}\n\n{feed_section}"
    else:
        user = f"Prompt:\n{original_prompt}"
    return system, user


def _format_claims_block(claims: tuple[Claim, ...]) -> str:
    if not claims:
        return "(no claims)"
    return "\n".join(
        f"- claim_id: {c.claim_id}\n  text: {c.text}" for c in claims
    )


def build_self_reflection_prompt(
    *,
    model_id: ModelId,
    phase_1_response: IndependentResponse,
) -> tuple[str, str]:
    """Returns (system, user) messages for self-reflection during latency gap."""
    system = (
        f"You are a Lattice peer producing a self-reflection on your own Phase 1 "
        f"response. This happens during the latency gap after Phase 1 ends and "
        f"before Phase 2 begins.\n"
        f"You are: {model_id.value}\n"
        "\n"
        "This is preparation for Phase 2 cross-reading, NOT refinement of Phase 1. "
        "Your Phase 1 response is fixed. Identify the strongest and weakest of "
        "your own claims as you actually see them now — not as you predict peers "
        "will see them. Your honest read of your own work is what makes the "
        "reflection useful.\n"
        "\n"
        "Justify the focus_tag you chose. Reference your own claims by claim_id. "
        "Strongest and weakest must be different claims.\n"
        "\n"
        f"Use the {SELF_REFLECTION_TOOL_NAME} tool to emit your reflection."
    )

    user = (
        f"Your Phase 1 response:\n{phase_1_response.response}\n"
        "\n"
        f"Your claims:\n{_format_claims_block(phase_1_response.claims)}\n"
        "\n"
        f"Your focus_tag: {phase_1_response.focus_tag.value}\n"
        f"Your confidence: {phase_1_response.confidence}"
    )
    return system, user


# ---------------------------------------------------------------------------
# Parsers.
# ---------------------------------------------------------------------------


def parse_phase_1_response_tool_use(
    tool_input: dict[str, Any],
    *,
    expected_model: ModelId,
    prompt_hash: str,
    generation_started_at: datetime,
    generation_completed_at: datetime,
    latency_used_for_reflection_ms: int = 0,
) -> IndependentResponse:
    """Parse a phase_1_response tool input into an IndependentResponse.

    The returned IndependentResponse has empty self_reflection_artifacts. The
    orchestrator augments via a separate self-reflection call during the latency
    gap, then constructs the final IndependentResponse with the artifact attached.
    """
    if not isinstance(tool_input, dict):
        raise WireParseError(f"tool_input must be a dict, got {type(tool_input).__name__}")

    response = tool_input.get("response")
    focus_tag_raw = tool_input.get("focus_tag")
    confidence_raw = tool_input.get("confidence")
    claim_texts_raw = tool_input.get("claim_texts", [])

    if not isinstance(response, str) or not response.strip():
        raise WireParseError("response must be a non-empty string")
    if not isinstance(focus_tag_raw, str):
        raise WireParseError("focus_tag must be a string from the closed vocabulary")
    try:
        focus_tag = FocusTag(focus_tag_raw)
    except ValueError as exc:
        raise WireParseError(f"unknown focus_tag: {focus_tag_raw!r}") from exc

    if not isinstance(confidence_raw, (int, float)):
        raise WireParseError("confidence must be a number in [0, 1]")
    confidence = float(confidence_raw)
    if not 0.0 <= confidence <= 1.0:
        raise WireParseError(f"confidence {confidence} is outside [0, 1]")

    if not isinstance(claim_texts_raw, list):
        raise WireParseError("claim_texts must be a list of strings")

    claims: list[Claim] = []
    seen_ids: set[str] = set()
    for text in claim_texts_raw:
        if not isinstance(text, str) or not text.strip():
            raise WireParseError("claim_texts entries must be non-empty strings")
        cid = claim_id_for(expected_model, Phase.INDEPENDENT, text)
        if cid in seen_ids:
            raise WireParseError(
                f"duplicate claim_text produced identical claim_id {cid}. "
                "Phase 1 claims must be distinct."
            )
        seen_ids.add(cid)
        claims.append(
            Claim(
                claim_id=cid,
                source_model=expected_model,
                source_phase=Phase.INDEPENDENT,
                text=text,
            )
        )

    return IndependentResponse(
        model_id=expected_model,
        prompt_hash=prompt_hash,
        response=response,
        focus_tag=focus_tag,
        confidence=confidence,
        claims=tuple(claims),
        self_reflection_artifacts=(),
        generation_started_at=generation_started_at,
        generation_completed_at=generation_completed_at,
        latency_used_for_reflection_ms=latency_used_for_reflection_ms,
    )


def parse_self_reflection_tool_use(
    tool_input: dict[str, Any],
    *,
    expected_model: ModelId,
    own_claim_ids: set[str],
    generated_at: Optional[datetime] = None,
) -> SelfReflectionArtifact:
    """Parse a self_reflection tool input into a SelfReflectionArtifact.

    own_claim_ids is the set of claim_ids the model authored in Phase 1. The
    parser refuses references outside that set at the wire boundary.

    Note on the parser-signature asymmetry: cross_reading and phase_2_tagging
    parsers accept Optional[set[str]] valid_claim_ids that defers to the schema's
    Session-level validator when None. This parser requires own_claim_ids because
    the registry is always available — Phase 1 just emitted in this same model's
    prior call. There is no ambiguity for the orchestrator to defer past. Keeping
    this parser stricter is principled, not a Pattern-1 violation; do not "fix"
    the asymmetry into uniformity.
    """
    if not isinstance(tool_input, dict):
        raise WireParseError(f"tool_input must be a dict, got {type(tool_input).__name__}")

    strongest = tool_input.get("strongest_claim_id")
    weakest = tool_input.get("weakest_claim_id")
    justification = tool_input.get("tag_justification")

    if not isinstance(strongest, str) or not strongest:
        raise WireParseError("strongest_claim_id must be a non-empty string")
    if not isinstance(weakest, str) or not weakest:
        raise WireParseError("weakest_claim_id must be a non-empty string")
    if not isinstance(justification, str) or not justification.strip():
        raise WireParseError("tag_justification must be a non-empty string")

    if strongest not in own_claim_ids:
        raise WireParseError(
            f"strongest_claim_id {strongest!r} is not one of the model's own "
            f"Phase 1 claims. A model can only reflect on its own claims."
        )
    if weakest not in own_claim_ids:
        raise WireParseError(
            f"weakest_claim_id {weakest!r} is not one of the model's own "
            f"Phase 1 claims."
        )

    when = generated_at if generated_at is not None else datetime.now(timezone.utc)

    return SelfReflectionArtifact(
        model_id=expected_model,
        generated_at=when,
        strongest_claim_id=strongest,
        weakest_claim_id=weakest,
        tag_justification=justification,
    )


# ---------------------------------------------------------------------------
# Composition helper.
# ---------------------------------------------------------------------------


def compose_phase_1_with_reflection(
    response: IndependentResponse,
    reflection: SelfReflectionArtifact,
) -> IndependentResponse:
    """Fold a self-reflection into the model's Phase 1 response.

    The two-tool wire pattern produces an IndependentResponse with empty
    self_reflection_artifacts, then a separate SelfReflectionArtifact. The
    orchestrator composes them via this helper rather than reconstructing
    the response by hand (which would conflict on the artifacts kwarg).

    Refuses at the helper layer if the reflection's claim_ids don't resolve
    against the response's claims, giving a clearer error than waiting for
    the schema validator to fire on construction.
    """
    if reflection.model_id is not response.model_id:
        raise ValueError(
            f"reflection.model_id {reflection.model_id} does not match "
            f"response.model_id {response.model_id}."
        )
    own_claim_ids = {c.claim_id for c in response.claims}
    if reflection.strongest_claim_id not in own_claim_ids:
        raise ValueError(
            f"reflection.strongest_claim_id {reflection.strongest_claim_id!r} "
            "does not resolve against the response's claims."
        )
    if reflection.weakest_claim_id not in own_claim_ids:
        raise ValueError(
            f"reflection.weakest_claim_id {reflection.weakest_claim_id!r} "
            "does not resolve against the response's claims."
        )
    return response.model_copy(
        update={
            "self_reflection_artifacts": (
                *response.self_reflection_artifacts,
                reflection,
            ),
        }
    )


# ---------------------------------------------------------------------------
# Wire client Protocol.
# ---------------------------------------------------------------------------


@runtime_checkable
class Phase1WireClient(Protocol):
    """The orchestrator's view of a Phase 1 capable model client.

    Two methods, two calls per model per session: Phase 1 response first
    (independent generation), then self-reflection during the latency gap.
    """

    def submit_phase_1_response(
        self,
        *,
        model_id: ModelId,
        original_prompt: str,
        prompt_hash: str,
        feed: Optional[tuple] = None,
    ) -> IndependentResponse: ...

    def submit_self_reflection(
        self,
        *,
        model_id: ModelId,
        phase_1_response: IndependentResponse,
    ) -> SelfReflectionArtifact: ...
