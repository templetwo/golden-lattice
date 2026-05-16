"""Phase 2 wire format — how models emit CrossReading and Phase2Tagging artifacts.

Pure logic. No network. Provider integration (Anthropic, etc.) is layered on top via
the Phase2WireClient Protocol. The substrate constrains this layer from below: every
artifact emitted by a model must satisfy the Pydantic schema constructors before the
orchestrator sees it.

Wire-layer refusals (in addition to the substrate's structural refusals):

  - Attribution mismatch: a model claiming to be a different reader/tagger than the
    one we asked is refused at parse time. The wire layer enforces caller intent.
  - Unknown claim reference: tagging a claim_id that doesn't resolve to a known claim
    is refused. Tags must reference real artifacts.
  - Free-form prose in structured fields: the tool-use protocol is the wire — text
    fields outside the tool input are ignored, structure is mandatory.

Design note worth surfacing to future instances: the prompt itself is an authority
artifact. The framing we use to elicit a CrossReading shapes what models say.
Translation collapse — where the wire layer silently biases what counts as a
contribution via prompt construction — is a candidate for the anticipatory-sixth
collapse mode the chronicle expects. Treat prompt revisions with the same care
as schema amendments.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable

from golden_lattice.memory_graph.base import ModelId, Phase, claim_id_for
from golden_lattice.memory_graph.schema import (
    Claim,
    ClaimRef,
    CrossReading,
    Disagreement,
)
from golden_lattice.memory_graph.tagging import (
    TAG_VOCABULARY_VERSION,
    ClaimTags,
    EdgeCaseTag,
    Phase2Tagging,
    StructuralPatternTag,
)


# ---------------------------------------------------------------------------
# Tool schemas — derived from Pydantic models, wrapped for Anthropic tool-use.
# ---------------------------------------------------------------------------

CROSS_READING_TOOL_NAME = "emit_cross_reading"
PHASE_2_TAGGING_TOOL_NAME = "emit_phase_2_tagging"


def cross_reading_tool_schema() -> dict[str, Any]:
    """Anthropic tool schema for emitting a CrossReading.

    The model emits agreement claim_id refs, disagreements (claim_id + reason),
    and missing-claim TEXTS (claim_ids are assigned post-parse from content hashes).
    """
    return {
        "name": CROSS_READING_TOOL_NAME,
        "description": (
            "Emit a structured cross-reading of one peer's Phase 1 response. "
            "Reference target's claims by claim_id. For new edge cases or framings "
            "the target missed, emit text only — IDs are assigned by the wire layer."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["agreements", "disagreements", "missing_texts"],
            "properties": {
                "agreements": {
                    "type": "array",
                    "description": "claim_ids of target's claims you agree with.",
                    "items": {"type": "string"},
                },
                "disagreements": {
                    "type": "array",
                    "description": "Per-claim disagreements with explicit reasons.",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["target_claim_id", "reason"],
                        "properties": {
                            "target_claim_id": {"type": "string"},
                            "reason": {"type": "string"},
                        },
                    },
                },
                "missing_texts": {
                    "type": "array",
                    "description": (
                        "Texts of claims you noticed the target missed. "
                        "Each one becomes a Phase 2 claim attributed to you."
                    ),
                    "items": {"type": "string"},
                },
            },
        },
    }


def phase_2_tagging_tool_schema() -> dict[str, Any]:
    """Anthropic tool schema for emitting a Phase2Tagging.

    Model emits per-claim tags split into peer_tags (claims authored by other models)
    and self_tags (claims authored by this model). Closed vocabularies enforced.
    """
    edge_case_values = [t.value for t in EdgeCaseTag]
    structural_values = [t.value for t in StructuralPatternTag]

    claim_tags_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["claim_id"],
        "properties": {
            "claim_id": {"type": "string"},
            "edge_case_tags": {
                "type": "array",
                "items": {"type": "string", "enum": edge_case_values},
                "description": (
                    "Edge-case categories. 'other' if it fits the dimension but "
                    "no subtype applies. Empty array if not an edge case."
                ),
            },
            "structural_pattern_tags": {
                "type": "array",
                "items": {"type": "string", "enum": structural_values},
                "description": (
                    "Structural-pattern categories. 'other' if it fits the dimension "
                    "but no subtype applies. Empty array if not a structural pattern."
                ),
            },
        },
    }

    return {
        "name": PHASE_2_TAGGING_TOOL_NAME,
        "description": (
            "Emit a structured classification of every visible claim using closed "
            "vocabularies. Split tags between peer_tags (claims you did not author) "
            "and self_tags (claims you authored). A claim that does not fit a "
            "dimension should have an empty tag array for that dimension."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["peer_tags", "self_tags"],
            "properties": {
                "peer_tags": {"type": "array", "items": claim_tags_schema},
                "self_tags": {"type": "array", "items": claim_tags_schema},
            },
        },
    }


# ---------------------------------------------------------------------------
# Prompt builders — the framing the model sees before emitting.
# ---------------------------------------------------------------------------


def _format_claims_block(claims: tuple[Claim, ...]) -> str:
    if not claims:
        return "(no claims)"
    return "\n".join(
        f"- claim_id: {c.claim_id}\n  text: {c.text}" for c in claims
    )


def build_cross_reading_prompt(
    *,
    reader_model: ModelId,
    target_model: ModelId,
    original_prompt: str,
    target_response: str,
    target_claims: tuple[Claim, ...],
    target_self_reflection: Optional[str] = None,
) -> tuple[str, str]:
    """Returns (system, user) messages for cross-reading one peer's Phase 1 output."""
    system = (
        f"You are a Lattice peer reading another peer's Phase 1 response.\n"
        f"You are: {reader_model.value}\n"
        f"You are reading: {target_model.value}\n"
        "\n"
        "Your task: emit a structured cross-reading that names where you agree, "
        "where you disagree (with a reason), and what the target missed. Reference "
        "the target's claims by claim_id. For things the target missed, emit text — "
        "the wire layer will assign claim_ids.\n"
        "\n"
        "Refuse to flatten disagreement into agreement. Productive disagreement is "
        "one of the things the Lattice surfaces. If you genuinely agree with everything, "
        "say so by listing all claim_ids in agreements. If you genuinely disagree, "
        "name the reason clearly. Do not soften.\n"
        "\n"
        f"Use the {CROSS_READING_TOOL_NAME} tool to emit your cross-reading. "
        "Free-form prose outside the tool will be ignored."
    )

    reflection_block = (
        f"\n\n{target_model.value}'s self-reflection on their own response:\n"
        f"{target_self_reflection}"
        if target_self_reflection
        else ""
    )

    user = (
        f"Original prompt:\n{original_prompt}\n"
        "\n"
        f"{target_model.value}'s response:\n{target_response}\n"
        "\n"
        f"{target_model.value}'s claims (reference these by claim_id):\n"
        f"{_format_claims_block(target_claims)}"
        f"{reflection_block}"
    )

    return system, user


def build_phase_2_tagging_prompt(
    *,
    tagger_model: ModelId,
    original_prompt: str,
    own_claims: tuple[Claim, ...],
    peer_claims: tuple[Claim, ...],
) -> tuple[str, str]:
    """Returns (system, user) messages for Phase 2 structural tagging."""
    edge_case_values = ", ".join(t.value for t in EdgeCaseTag)
    structural_values = ", ".join(t.value for t in StructuralPatternTag)

    system = (
        f"You are a Lattice peer producing a structural classification pass over "
        f"every visible claim from Phase 1 and Phase 2 missing claims.\n"
        f"You are: {tagger_model.value}\n"
        f"Vocabulary version: {TAG_VOCABULARY_VERSION}\n"
        "\n"
        f"You will tag claims along two dimensions. Dimension 1 — Edge Cases — "
        f"concerns claim content: what the claim is about. Dimension 2 — "
        f"Structural Patterns — concerns claim form: how the claim is organizing "
        f"thought at the architectural level. Tag according to what the claim "
        f"is, not how it sounds.\n"
        "\n"
        f"Edge-case vocabulary: {edge_case_values}\n"
        f"Structural-pattern vocabulary: {structural_values}\n"
        "\n"
        "Each claim can be tagged in one dimension, both, or neither. Empty tag "
        "arrays mean the claim does not fit that dimension at all. Use 'other' "
        "when a claim fits a dimension but no subtype applies — high 'other' "
        "rates signal vocabulary fitness issues, not your judgment.\n"
        "\n"
        "Split tags between self_tags (claims you authored) and peer_tags (claims "
        "authored by other peers). The wire layer rejects mixing.\n"
        "\n"
        f"Use the {PHASE_2_TAGGING_TOOL_NAME} tool to emit your tagging."
    )

    user = (
        f"Original prompt:\n{original_prompt}\n"
        "\n"
        f"Your own claims (use self_tags):\n{_format_claims_block(own_claims)}\n"
        "\n"
        f"Peer claims (use peer_tags):\n{_format_claims_block(peer_claims)}"
    )

    return system, user


# ---------------------------------------------------------------------------
# Parsers — turn validated tool input into Pydantic models, refuse mismatches.
# ---------------------------------------------------------------------------


class WireParseError(ValueError):
    """Raised when tool-use input fails wire-layer validation."""


def parse_cross_reading_tool_use(
    tool_input: dict[str, Any],
    *,
    expected_reader: ModelId,
    expected_target: ModelId,
    valid_claim_ids: Optional[set[str]] = None,
) -> CrossReading:
    """Parse a cross_reading tool input into a CrossReading. Refuses malformed structure.

    The tool input contains agreements (claim_id refs), disagreements
    (claim_id + reason), and missing_texts (model-emitted text that we
    convert into Phase 2 claims attributed to the reader).

    If valid_claim_ids is provided, agreements and disagreements that don't resolve
    are refused at the wire boundary. If None, resolution is deferred to the schema's
    Session-level validator (still refused, just later).
    """
    if not isinstance(tool_input, dict):
        raise WireParseError(f"tool_input must be a dict, got {type(tool_input).__name__}")

    if expected_reader is expected_target:
        raise WireParseError(
            "expected_reader and expected_target must differ — Phase 2 is symmetric "
            "across distinct pairs."
        )

    agreements_raw = tool_input.get("agreements", [])
    disagreements_raw = tool_input.get("disagreements", [])
    missing_texts_raw = tool_input.get("missing_texts", [])

    if not isinstance(agreements_raw, list):
        raise WireParseError("agreements must be a list of claim_id strings")
    if not isinstance(disagreements_raw, list):
        raise WireParseError("disagreements must be a list of objects")
    if not isinstance(missing_texts_raw, list):
        raise WireParseError("missing_texts must be a list of strings")

    agreements = tuple(ClaimRef(claim_id=str(c)) for c in agreements_raw)

    disagreements: list[Disagreement] = []
    for entry in disagreements_raw:
        if not isinstance(entry, dict):
            raise WireParseError("disagreement entries must be objects")
        target_claim_id = entry.get("target_claim_id")
        reason = entry.get("reason")
        if not isinstance(target_claim_id, str) or not target_claim_id:
            raise WireParseError("disagreement.target_claim_id must be a non-empty string")
        if not isinstance(reason, str) or not reason:
            raise WireParseError("disagreement.reason must be a non-empty string")
        disagreements.append(Disagreement(target_claim_id=target_claim_id, reason=reason))

    if valid_claim_ids is not None:
        for ref in agreements:
            if ref.claim_id not in valid_claim_ids:
                raise WireParseError(
                    f"agreement references unknown claim_id {ref.claim_id!r}."
                )
        for d in disagreements:
            if d.target_claim_id not in valid_claim_ids:
                raise WireParseError(
                    f"disagreement references unknown claim_id {d.target_claim_id!r}."
                )

    missing: list[Claim] = []
    for text in missing_texts_raw:
        if not isinstance(text, str) or not text.strip():
            raise WireParseError("missing_texts entries must be non-empty strings")
        missing.append(
            Claim(
                claim_id=claim_id_for(expected_reader, Phase.CROSS_READING, text),
                source_model=expected_reader,
                source_phase=Phase.CROSS_READING,
                text=text,
            )
        )

    return CrossReading(
        reader_model=expected_reader,
        target_model=expected_target,
        agreements=agreements,
        disagreements=tuple(disagreements),
        missing=tuple(missing),
    )


def _build_claim_tags(entries: list[Any]) -> tuple[ClaimTags, ...]:
    out: list[ClaimTags] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise WireParseError("claim_tags entries must be objects")
        claim_id = entry.get("claim_id")
        if not isinstance(claim_id, str) or not claim_id:
            raise WireParseError("claim_tags.claim_id must be a non-empty string")

        edge_raw = entry.get("edge_case_tags", []) or []
        struct_raw = entry.get("structural_pattern_tags", []) or []
        if not isinstance(edge_raw, list) or not isinstance(struct_raw, list):
            raise WireParseError("tag arrays must be lists")

        # Wire-boundary tolerance for vocabulary slips: filter unknown /
        # cross-vocab string values rather than abort the session. Empirical
        # trigger 2026-05-16 — Haiku put 'framing_choice' (a StructuralPatternTag
        # value) into edge_case_tags; the strict parser refused and the
        # session aborted mid-Phase-2. Same shape of forgiveness as
        # parse_investigation_proposal_tool_use applies to over-cap / empty /
        # duplicate proposed queries.
        #
        # Type errors (non-string elements) still raise — that's malformed
        # output structure, not a vocabulary slip the wire should absorb.
        edge_values = {t.value for t in EdgeCaseTag}
        struct_values = {t.value for t in StructuralPatternTag}
        edge: list[EdgeCaseTag] = []
        for v in edge_raw:
            if not isinstance(v, str):
                raise WireParseError(
                    f"edge_case_tags element must be a string, got {type(v).__name__}"
                )
            if v in edge_values:
                edge.append(EdgeCaseTag(v))
            # else: filter silently — model emitted an unknown or cross-vocab
            # string. The Phase 2 prompt explicitly lists the closed vocab;
            # a slip is the model's, not the architecture's.
        structural: list[StructuralPatternTag] = []
        for v in struct_raw:
            if not isinstance(v, str):
                raise WireParseError(
                    f"structural_pattern_tags element must be a string, got {type(v).__name__}"
                )
            if v in struct_values:
                structural.append(StructuralPatternTag(v))
        # Dedupe in case the model emits the same valid tag twice — substrate
        # would refuse but wire absorbs.
        edge = list(dict.fromkeys(edge))
        structural = list(dict.fromkeys(structural))

        out.append(
            ClaimTags(
                claim_id=claim_id,
                edge_case_tags=tuple(edge),
                structural_pattern_tags=tuple(structural),
            )
        )
    return tuple(out)


def parse_phase_2_tagging_tool_use(
    tool_input: dict[str, Any],
    *,
    expected_tagger: ModelId,
    valid_claim_ids: Optional[set[str]] = None,
) -> Phase2Tagging:
    """Parse a phase_2_tagging tool input into a Phase2Tagging.

    If valid_claim_ids is provided, claim_ids are resolved at the wire boundary.
    If None, resolution is deferred to the schema's Session-level validator
    (still refused, just later). Symmetric with parse_cross_reading_tool_use.

    The schema layer will further refuse own-claim-in-peer-tags and
    peer-claim-in-self-tags when this Phase2Tagging is folded into a Session.
    """
    if not isinstance(tool_input, dict):
        raise WireParseError(f"tool_input must be a dict, got {type(tool_input).__name__}")

    peer_raw = tool_input.get("peer_tags", [])
    self_raw = tool_input.get("self_tags", [])
    if not isinstance(peer_raw, list) or not isinstance(self_raw, list):
        raise WireParseError("peer_tags and self_tags must be lists")

    peer_tags = _build_claim_tags(peer_raw)
    self_tags = _build_claim_tags(self_raw)

    if valid_claim_ids is not None:
        for ct in peer_tags + self_tags:
            if ct.claim_id not in valid_claim_ids:
                raise WireParseError(
                    f"tagging references unknown claim_id {ct.claim_id!r}. "
                    "Tags must resolve to real claims from Phase 1 or Phase 2 missing."
                )

    return Phase2Tagging(
        tagger_model=expected_tagger,
        peer_tags=peer_tags,
        self_tags=self_tags,
    )


# ---------------------------------------------------------------------------
# Wire client Protocol — what the orchestrator depends on.
# ---------------------------------------------------------------------------


@runtime_checkable
class Phase2WireClient(Protocol):
    """The orchestrator's view of a Phase 2 capable model client.

    Concrete implementations: AnthropicPhase2Client (production, separate file),
    StubPhase2Client (tests, scripted responses). Upstream code depends only on
    this Protocol.
    """

    def submit_cross_reading(
        self,
        *,
        reader_model: ModelId,
        target_model: ModelId,
        original_prompt: str,
        target_response: str,
        target_claims: tuple[Claim, ...],
        target_self_reflection: Optional[str] = None,
        valid_claim_ids: Optional[set[str]] = None,
    ) -> CrossReading: ...

    def submit_phase_2_tagging(
        self,
        *,
        tagger_model: ModelId,
        original_prompt: str,
        own_claims: tuple[Claim, ...],
        peer_claims: tuple[Claim, ...],
        valid_claim_ids: Optional[set[str]] = None,
    ) -> Phase2Tagging: ...
