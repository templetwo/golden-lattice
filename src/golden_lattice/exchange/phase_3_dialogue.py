"""Phase 3 wire format — structured cross-model dialogue across closed channels.

Pure logic. No network. One tool call per model per Phase 3, emitting a batch of
dialogue turns. Wire-layer enforcement complements the substrate's structural
refusals; the substrate is the unconditional backstop for caps and resolution.

CAP STRUCTURE (per ARCHITECTURE.md §5):

  - critique:  per (speaker, target_model). 3 per peer. A model may critique
               up to 3 claims of each peer's work.
  - augment:   per speaker. 3 aggregate, regardless of target distribution.
  - converge:  per speaker. 3 aggregate, regardless of target distribution.

The asymmetry is principled. Critique addresses specific peer claims, so the cap
is per-peer to prevent overwhelming one peer while permitting substantive
engagement with each. Augment and converge are conversational moves on the whole
discussion (additive and alignment-naming respectively), so the cap is aggregate.

The wire layer enforces caps where they're computable from the batch alone.
Cross-batch caps (i.e., other models' Phase 3 turns affecting this model's caps)
do not exist — caps are per-speaker, and one speaker emits one Phase 3 batch.

WIRE-LAYER REFUSALS (in addition to substrate's structural refusals):

  - Attribution mismatch: parser fixes speaker_model to expected; refuses if a
    turn claims to be spoken by someone else.
  - Channel must be from the closed vocabulary; refused otherwise.
  - target_model == speaker_model refused (no self-dialogue).
  - critique without target_model refused (per-spec).
  - critique without target_claim_ids refused (per-spec).
  - target_claim_ids without target_model refused (incoherent).
  - Content must be non-empty.
  - Cap violations refused at parse boundary (per-peer for critique;
    aggregate for augment/converge).
  - Optional valid_claim_ids resolution at the wire boundary, mirroring
    cross-reading and tagging parsers.

TRANSLATION COLLAPSE — DRAFTING-HAND SELF-FLAGS:

Phase 3 is the first wire layer where the prompt has to convey an asymmetric cap
structure (per-peer for critique, aggregate for augment/converge). Three deliberate
choices in the prompt that need flagging for the audit:

  Flag A — Channel-purpose framing pulls toward function over form. The prompt
  names what each channel is *for*: critique identifies weakness, augment adds
  something new, converge names actual alignment. This anchors the model toward
  meaningful turns over filler. But "meaningful" is fuzzy in the same way
  "substantive" was fuzzy in Phase 1; the peer-utility test framing is reused
  ("a critique that doesn't identify weakness is wasted") to give a structural
  criterion the model can apply. Worth verifying this doesn't pull models toward
  over-justifying their turns ("here's why this is meaningful...") rather than
  emitting the turn directly.

  Flag B — Cap asymmetry rendering. The prompt enumerates "up to 3 of EACH
  peer's positions" for critique and "up to 3 total" for augment and converge.
  The contrast between "each peer's" and "total" is the load-bearing language
  carrying the asymmetry. If a model misreads "each peer's" as the universal
  pattern, it may treat augment/converge as per-peer. If it misreads "total"
  as the universal pattern, it may treat critique as aggregate. Both errors
  are silently caught by the substrate's cap validators, but they would
  manifest as systematic under-engagement on critique or over-emission on
  augment/converge. Worth verifying with real session data later.

  Flag C — Channel ordering in the system message. Channels listed
  critique, augment, converge — same order as the spec. Critique-first may
  bias models toward leading with disagreement rather than alignment. Spec
  order is principled (critique surfaces what needs work, augment adds,
  converge names alignment — a natural conversational arc), but worth knowing
  the order is a choice. Status: kept as spec order, flagged.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable

from golden_lattice.exchange.phase_2_cross_reading import WireParseError
from golden_lattice.memory_graph.base import ModelId
from golden_lattice.memory_graph.schema import (
    DIALOGUE_CHANNEL_CAP,
    Claim,
    DialogueChannel,
    DialogueTurn,
)


# ---------------------------------------------------------------------------
# Tool schema.
# ---------------------------------------------------------------------------

PHASE_3_DIALOGUE_TOOL_NAME = "emit_phase_3_dialogue"
_VALID_CHANNELS: tuple[DialogueChannel, ...] = ("critique", "augment", "converge")


def phase_3_dialogue_tool_schema() -> dict[str, Any]:
    """Anthropic tool schema for emitting a Phase 3 dialogue batch.

    One tool call per model per Phase 3. The model emits a list of turns; each
    turn names channel, target_model (required for critique, optional for
    augment/converge), target_claim_ids (required non-empty for critique), and
    content. Cap enforcement happens at the parser, not in the JSON schema —
    aggregate-vs-per-peer caps require counting, which JSON Schema can't express.
    """
    return {
        "name": PHASE_3_DIALOGUE_TOOL_NAME,
        "description": (
            "Emit your Phase 3 dialogue turns. One call per model. Each turn "
            "names channel, optional target_model, optional target_claim_ids, "
            "and content. Caps are enforced by the wire layer."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["turns"],
            "properties": {
                "turns": {
                    "type": "array",
                    "description": "Your dialogue turns for this Phase 3.",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["channel", "content"],
                        "properties": {
                            "channel": {
                                "type": "string",
                                "enum": list(_VALID_CHANNELS),
                            },
                            "target_model": {
                                "type": "string",
                                "description": (
                                    "Required for critique; optional for "
                                    "augment and converge."
                                ),
                            },
                            "target_claim_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "Required non-empty for critique; "
                                    "optional otherwise. Cannot be present "
                                    "without target_model."
                                ),
                            },
                            "content": {"type": "string"},
                        },
                    },
                },
            },
        },
    }


# ---------------------------------------------------------------------------
# Prompt builders.
# ---------------------------------------------------------------------------


def _format_phase_2_summary(
    own_phase_1_response: str,
    peer_phase_1_blocks: tuple[tuple[ModelId, str, tuple[Claim, ...]], ...],
) -> str:
    """Render peer Phase 1 outputs with claim_ids visible for the model to reference.

    Each peer entry is (peer_model, peer_response_prose, peer_claims).
    """
    blocks = []
    for peer_model, response, claims in peer_phase_1_blocks:
        claim_lines = "\n".join(
            f"  - claim_id: {c.claim_id}\n    text: {c.text}" for c in claims
        )
        if not claim_lines:
            claim_lines = "  (no claims)"
        blocks.append(
            f"=== {peer_model.value} ===\nresponse:\n{response}\nclaims:\n{claim_lines}"
        )
    own_block = f"=== Your Phase 1 response ===\n{own_phase_1_response}"
    return own_block + "\n\n" + "\n\n".join(blocks)


def build_phase_3_dialogue_prompt(
    *,
    speaker_model: ModelId,
    original_prompt: str,
    own_phase_1_response: str,
    peer_phase_1_blocks: tuple[tuple[ModelId, str, tuple[Claim, ...]], ...],
) -> tuple[str, str]:
    """Returns (system, user) messages for Phase 3 structured dialogue."""
    cap = DIALOGUE_CHANNEL_CAP

    system = (
        f"You are a Lattice peer producing Phase 3 (structured dialogue).\n"
        f"You are: {speaker_model.value}\n"
        "\n"
        "Phase 3 is structured cross-model addressing across three closed channels. "
        "You have already seen peer Phase 1 responses (Phase 2 cross-reading and "
        "tagging happened in parallel). Now you address peer claims directly.\n"
        "\n"
        "The three channels and what each is for:\n"
        "\n"
        f"  - critique: identify weakness in a specific peer's claims. Targets a "
        f"specific peer; references that peer's claim_ids. Up to {cap} critiques "
        f"of EACH peer's positions.\n"
        "\n"
        f"  - augment: add something to the conversation. May target a specific "
        f"peer's position or be a general addition. Up to {cap} augmentations "
        f"total, whether targeted or general.\n"
        "\n"
        f"  - converge: name where alignment exists. May acknowledge specific "
        f"peers' agreement or a general alignment across the group. Up to {cap} "
        f"convergence points total, whether targeted or general.\n"
        "\n"
        "Cap structure summary: critique is per-peer (you may critique up to "
        f"{cap} of each peer's claims). Augment and converge are aggregate "
        f"(up to {cap} total each, regardless of target).\n"
        "\n"
        "Use this channel only when you actually have something to say in its "
        "register. A critique that names no specific weakness, an augment that "
        "adds nothing peers couldn't have inferred, a converge that overstates "
        "alignment — these don't help the lattice. Don't manufacture turns to "
        "fill the cap; don't withhold real ones.\n"
        "\n"
        f"Use the {PHASE_3_DIALOGUE_TOOL_NAME} tool to emit your turns. Free-form "
        "prose outside the tool will be ignored."
    )

    user = (
        f"Original prompt:\n{original_prompt}\n"
        "\n"
        + _format_phase_2_summary(own_phase_1_response, peer_phase_1_blocks)
    )
    return system, user


# ---------------------------------------------------------------------------
# Parser.
# ---------------------------------------------------------------------------


def _validate_caps(turns: list[DialogueTurn]) -> None:
    """Refuse a batch whose turns exceed the per-channel caps.

    Mirrors Session._dialogue_channel_caps but operates on the wire batch
    so the failure surfaces at parse time with a wire-layer error message.
    """
    critique_counts: dict[tuple[ModelId, ModelId], int] = {}
    augment_counts: dict[ModelId, int] = {}
    converge_counts: dict[ModelId, int] = {}
    for turn in turns:
        if turn.channel == "critique":
            assert turn.target_model is not None
            key = (turn.speaker_model, turn.target_model)
            critique_counts[key] = critique_counts.get(key, 0) + 1
            if critique_counts[key] > DIALOGUE_CHANNEL_CAP:
                raise WireParseError(
                    f"critique cap exceeded: {turn.speaker_model.value} has more "
                    f"than {DIALOGUE_CHANNEL_CAP} critiques targeting "
                    f"{turn.target_model.value}."
                )
        elif turn.channel == "augment":
            augment_counts[turn.speaker_model] = (
                augment_counts.get(turn.speaker_model, 0) + 1
            )
            if augment_counts[turn.speaker_model] > DIALOGUE_CHANNEL_CAP:
                raise WireParseError(
                    f"augment cap exceeded: {turn.speaker_model.value} has more "
                    f"than {DIALOGUE_CHANNEL_CAP} augments (aggregate)."
                )
        else:  # converge
            converge_counts[turn.speaker_model] = (
                converge_counts.get(turn.speaker_model, 0) + 1
            )
            if converge_counts[turn.speaker_model] > DIALOGUE_CHANNEL_CAP:
                raise WireParseError(
                    f"converge cap exceeded: {turn.speaker_model.value} has more "
                    f"than {DIALOGUE_CHANNEL_CAP} converges (aggregate)."
                )


def parse_phase_3_dialogue_tool_use(
    tool_input: dict[str, Any],
    *,
    expected_speaker: ModelId,
    valid_claim_ids: Optional[set[str]] = None,
    turn_id_prefix: str = "",
) -> tuple[DialogueTurn, ...]:
    """Parse a phase_3_dialogue tool input into a tuple of DialogueTurns.

    Each turn's speaker_model is fixed to expected_speaker. turn_ids are assigned
    by the wire layer using turn_id_prefix + index, since the model doesn't
    produce IDs (avoiding forgery and giving stable wire-layer identity).

    If valid_claim_ids is provided, target_claim_ids are resolved at the wire
    boundary. If None, resolution defers to the schema's Session-level validator.
    Symmetric with cross-reading and tagging parsers.
    """
    if not isinstance(tool_input, dict):
        raise WireParseError(f"tool_input must be a dict, got {type(tool_input).__name__}")

    turns_raw = tool_input.get("turns", [])
    if not isinstance(turns_raw, list):
        raise WireParseError("turns must be a list of objects")

    parsed: list[DialogueTurn] = []
    for i, entry in enumerate(turns_raw):
        if not isinstance(entry, dict):
            raise WireParseError("turn entries must be objects")

        channel = entry.get("channel")
        if channel not in _VALID_CHANNELS:
            raise WireParseError(
                f"unknown channel {channel!r}; must be one of {_VALID_CHANNELS}."
            )

        content = entry.get("content")
        if not isinstance(content, str) or not content.strip():
            raise WireParseError("turn.content must be a non-empty string")

        target_model_raw = entry.get("target_model")
        target_model: Optional[ModelId]
        if target_model_raw is None:
            target_model = None
        elif isinstance(target_model_raw, str):
            try:
                target_model = ModelId(target_model_raw)
            except ValueError as exc:
                raise WireParseError(
                    f"unknown target_model {target_model_raw!r}."
                ) from exc
        else:
            raise WireParseError("target_model must be a string or omitted")

        target_claim_ids_raw = entry.get("target_claim_ids", []) or []
        if not isinstance(target_claim_ids_raw, list):
            raise WireParseError("target_claim_ids must be a list of strings")
        for cid in target_claim_ids_raw:
            if not isinstance(cid, str) or not cid:
                raise WireParseError(
                    "target_claim_ids entries must be non-empty strings"
                )
        target_claim_ids = tuple(target_claim_ids_raw)

        if valid_claim_ids is not None:
            for cid in target_claim_ids:
                if cid not in valid_claim_ids:
                    raise WireParseError(
                        f"target_claim_id {cid!r} does not resolve."
                    )

        # Construct DialogueTurn — substrate validators fire here for any
        # coherence rules (target_model != speaker, critique requirements, etc.).
        turn = DialogueTurn(
            turn_id=f"{turn_id_prefix}{i}",
            speaker_model=expected_speaker,
            channel=channel,
            target_model=target_model,
            target_claim_ids=target_claim_ids,
            content=content,
        )
        parsed.append(turn)

    _validate_caps(parsed)
    return tuple(parsed)


# ---------------------------------------------------------------------------
# Wire client Protocol.
# ---------------------------------------------------------------------------


@runtime_checkable
class Phase3WireClient(Protocol):
    """The orchestrator's view of a Phase 3 capable model client."""

    def submit_phase_3_dialogue(
        self,
        *,
        speaker_model: ModelId,
        original_prompt: str,
        own_phase_1_response: str,
        peer_phase_1_blocks: tuple[tuple[ModelId, str, tuple[Claim, ...]], ...],
        valid_claim_ids: Optional[set[str]] = None,
    ) -> tuple[DialogueTurn, ...]: ...
