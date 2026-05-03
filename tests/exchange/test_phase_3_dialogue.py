"""Tests for the Phase 3 wire format — schemas, prompts, parsers, refusals."""

import pytest

from golden_lattice.exchange.phase_2_cross_reading import WireParseError
from golden_lattice.exchange.phase_3_dialogue import (
    PHASE_3_DIALOGUE_TOOL_NAME,
    Phase3WireClient,
    build_phase_3_dialogue_prompt,
    parse_phase_3_dialogue_tool_use,
    phase_3_dialogue_tool_schema,
)
from golden_lattice.memory_graph.base import ModelId, Phase, claim_id_for
from golden_lattice.memory_graph.schema import Claim


def _phase1_claim(model: ModelId, text: str) -> Claim:
    return Claim(
        claim_id=claim_id_for(model, Phase.INDEPENDENT, text),
        source_model=model,
        source_phase=Phase.INDEPENDENT,
        text=text,
    )


# --- Tool schema --------------------------------------------------------


def test_phase_3_tool_schema_advertises_closed_channel_vocabulary():
    schema = phase_3_dialogue_tool_schema()
    assert schema["name"] == PHASE_3_DIALOGUE_TOOL_NAME
    enum = schema["input_schema"]["properties"]["turns"]["items"]["properties"][
        "channel"
    ]["enum"]
    assert set(enum) == {"critique", "augment", "converge"}


def test_phase_3_tool_schema_marks_only_channel_and_content_required():
    """target_model and target_claim_ids are conditionally required by the parser,
    not the JSON schema. Schema-level required is the minimum every turn must have."""
    schema = phase_3_dialogue_tool_schema()
    item_schema = schema["input_schema"]["properties"]["turns"]["items"]
    assert set(item_schema["required"]) == {"channel", "content"}


# --- Prompt builders -----------------------------------------------------


def test_phase_3_prompt_names_role_and_tool():
    sonnet_claim = _phase1_claim(ModelId.SONNET, "sonnet alpha")
    haiku_claim = _phase1_claim(ModelId.HAIKU, "haiku alpha")
    system, user = build_phase_3_dialogue_prompt(
        speaker_model=ModelId.OPUS,
        original_prompt="design a cache",
        own_phase_1_response="here is opus's response",
        peer_phase_1_blocks=(
            (ModelId.SONNET, "sonnet's response", (sonnet_claim,)),
            (ModelId.HAIKU, "haiku's response", (haiku_claim,)),
        ),
    )
    assert ModelId.OPUS.value in system
    assert PHASE_3_DIALOGUE_TOOL_NAME in system
    assert "design a cache" in user
    assert "sonnet alpha" in user
    assert "haiku alpha" in user
    assert sonnet_claim.claim_id in user
    assert haiku_claim.claim_id in user


def test_phase_3_prompt_makes_per_peer_vs_aggregate_asymmetry_visible():
    """Critique cap is per-peer; augment and converge are aggregate. The contrast
    must land in the prompt so models don't silently default to one pattern."""
    system, _ = build_phase_3_dialogue_prompt(
        speaker_model=ModelId.OPUS,
        original_prompt="p",
        own_phase_1_response="r",
        peer_phase_1_blocks=(),
    )
    # Per-peer language for critique:
    assert "EACH peer" in system
    # Aggregate language for augment and converge:
    assert "total" in system


def test_phase_3_prompt_states_channel_purposes():
    """Each channel's function (not just form) is named so models emit meaningful turns."""
    system, _ = build_phase_3_dialogue_prompt(
        speaker_model=ModelId.OPUS,
        original_prompt="p",
        own_phase_1_response="r",
        peer_phase_1_blocks=(),
    )
    assert "identify weakness" in system  # critique purpose
    assert "add" in system  # augment purpose
    assert "alignment" in system  # converge purpose


def test_phase_3_prompt_uses_symmetric_emission_protection():
    """Single, late, lattice-perception-framed protection — replaces three per-channel
    'wasted' repetitions. Symmetric: don't manufacture, don't withhold."""
    system, _ = build_phase_3_dialogue_prompt(
        speaker_model=ModelId.OPUS,
        original_prompt="p",
        own_phase_1_response="r",
        peer_phase_1_blocks=(),
    )
    assert "don't help the lattice" in system
    assert "Don't manufacture turns" in system
    assert "don't withhold real ones" in system


def test_phase_3_prompt_no_anti_softening_clause():
    """Anti-softening protection lives in Phase 2 where peer-reading meets agreement-pull.
    Phase 3 channel descriptions carry the discipline; duplicating the protection here
    would compound across phases."""
    system, _ = build_phase_3_dialogue_prompt(
        speaker_model=ModelId.OPUS,
        original_prompt="p",
        own_phase_1_response="r",
        peer_phase_1_blocks=(),
    )
    assert "Do not soften" not in system
    assert "Refuse to flatten dialogue" not in system


def test_phase_3_prompt_renders_own_and_peer_phase_1_in_user_message():
    sonnet_claim = _phase1_claim(ModelId.SONNET, "sonnet alpha")
    _, user = build_phase_3_dialogue_prompt(
        speaker_model=ModelId.OPUS,
        original_prompt="p",
        own_phase_1_response="opus response prose",
        peer_phase_1_blocks=((ModelId.SONNET, "sonnet response prose", (sonnet_claim,)),),
    )
    assert "opus response prose" in user
    assert "sonnet response prose" in user
    assert sonnet_claim.claim_id in user
    assert ModelId.SONNET.value in user


# --- Parser: happy path --------------------------------------------------


def test_parse_phase_3_happy_path_assigns_turn_ids():
    sonnet_claim = _phase1_claim(ModelId.SONNET, "sonnet alpha")
    tool_input = {
        "turns": [
            {
                "channel": "critique",
                "target_model": ModelId.SONNET.value,
                "target_claim_ids": [sonnet_claim.claim_id],
                "content": "sonnet's framing misses X",
            },
            {
                "channel": "augment",
                "content": "we should also consider Y",
            },
            {
                "channel": "converge",
                "target_model": ModelId.SONNET.value,
                "content": "agree with sonnet's overall framing",
            },
        ]
    }
    turns = parse_phase_3_dialogue_tool_use(
        tool_input,
        expected_speaker=ModelId.OPUS,
        turn_id_prefix="opus_p3_",
    )
    assert len(turns) == 3
    assert all(t.speaker_model is ModelId.OPUS for t in turns)
    assert turns[0].turn_id == "opus_p3_0"
    assert turns[0].channel == "critique"
    assert turns[0].target_model is ModelId.SONNET
    assert turns[1].channel == "augment"
    assert turns[1].target_model is None
    assert turns[2].channel == "converge"
    assert turns[2].target_model is ModelId.SONNET


# --- Parser: refusals ----------------------------------------------------


def test_parse_phase_3_rejects_non_dict_input():
    with pytest.raises(WireParseError, match="must be a dict"):
        parse_phase_3_dialogue_tool_use(
            "not a dict",  # type: ignore[arg-type]
            expected_speaker=ModelId.OPUS,
        )


def test_parse_phase_3_rejects_unknown_channel():
    tool_input = {
        "turns": [{"channel": "vibes", "content": "c"}],
    }
    with pytest.raises(WireParseError, match="unknown channel"):
        parse_phase_3_dialogue_tool_use(tool_input, expected_speaker=ModelId.OPUS)


def test_parse_phase_3_rejects_unknown_target_model():
    tool_input = {
        "turns": [
            {"channel": "augment", "target_model": "claude-mystery-99", "content": "c"}
        ],
    }
    with pytest.raises(WireParseError, match="unknown target_model"):
        parse_phase_3_dialogue_tool_use(tool_input, expected_speaker=ModelId.OPUS)


def test_parse_phase_3_rejects_empty_content():
    tool_input = {"turns": [{"channel": "augment", "content": "   "}]}
    with pytest.raises(WireParseError, match="content must be a non-empty"):
        parse_phase_3_dialogue_tool_use(tool_input, expected_speaker=ModelId.OPUS)


def test_parse_phase_3_critique_without_target_model_refused():
    """Substrate's DialogueTurn validator surfaces this as ValidationError; the
    parser propagates it through Pydantic. Exact message comes from substrate."""
    tool_input = {
        "turns": [
            {
                "channel": "critique",
                "target_claim_ids": ["abc"],
                "content": "c",
            }
        ],
    }
    with pytest.raises(Exception, match="target_model"):
        parse_phase_3_dialogue_tool_use(tool_input, expected_speaker=ModelId.OPUS)


def test_parse_phase_3_target_model_equals_speaker_refused():
    tool_input = {
        "turns": [
            {
                "channel": "augment",
                "target_model": ModelId.OPUS.value,
                "content": "c",
            }
        ],
    }
    with pytest.raises(Exception, match="cannot be its own target_model"):
        parse_phase_3_dialogue_tool_use(tool_input, expected_speaker=ModelId.OPUS)


def test_parse_phase_3_resolves_claim_ids_when_valid_ids_provided():
    tool_input = {
        "turns": [
            {
                "channel": "critique",
                "target_model": ModelId.SONNET.value,
                "target_claim_ids": ["ghost"],
                "content": "c",
            }
        ],
    }
    with pytest.raises(WireParseError, match="does not resolve"):
        parse_phase_3_dialogue_tool_use(
            tool_input,
            expected_speaker=ModelId.OPUS,
            valid_claim_ids={"some_other_id"},
        )


def test_parse_phase_3_skips_resolution_when_valid_ids_none():
    """Symmetric with cross-reading and tagging: defers to schema when None."""
    tool_input = {
        "turns": [
            {
                "channel": "critique",
                "target_model": ModelId.SONNET.value,
                "target_claim_ids": ["ghost"],
                "content": "c",
            }
        ],
    }
    turns = parse_phase_3_dialogue_tool_use(
        tool_input,
        expected_speaker=ModelId.OPUS,
        valid_claim_ids=None,
    )
    assert len(turns) == 1


# --- Cap enforcement at wire boundary -----------------------------------


def test_parse_phase_3_critique_per_peer_cap_six_against_two_peers_allowed():
    sonnet_id = claim_id_for(ModelId.SONNET, Phase.INDEPENDENT, "s_alpha")
    haiku_id = claim_id_for(ModelId.HAIKU, Phase.INDEPENDENT, "h_alpha")
    turns_input = []
    for i in range(3):
        turns_input.append(
            {
                "channel": "critique",
                "target_model": ModelId.SONNET.value,
                "target_claim_ids": [sonnet_id],
                "content": f"crit s {i}",
            }
        )
    for i in range(3):
        turns_input.append(
            {
                "channel": "critique",
                "target_model": ModelId.HAIKU.value,
                "target_claim_ids": [haiku_id],
                "content": f"crit h {i}",
            }
        )
    tool_input = {"turns": turns_input}
    turns = parse_phase_3_dialogue_tool_use(
        tool_input, expected_speaker=ModelId.OPUS
    )
    assert len(turns) == 6


def test_parse_phase_3_critique_four_against_one_peer_refused():
    sonnet_id = claim_id_for(ModelId.SONNET, Phase.INDEPENDENT, "s_alpha")
    turns_input = [
        {
            "channel": "critique",
            "target_model": ModelId.SONNET.value,
            "target_claim_ids": [sonnet_id],
            "content": f"crit {i}",
        }
        for i in range(4)
    ]
    with pytest.raises(WireParseError, match="critique cap exceeded"):
        parse_phase_3_dialogue_tool_use(
            {"turns": turns_input}, expected_speaker=ModelId.OPUS
        )


def test_parse_phase_3_augment_four_aggregate_refused():
    """Aggregate cap, regardless of target distribution."""
    turns_input = [
        {"channel": "augment", "target_model": ModelId.SONNET.value, "content": "a0"},
        {"channel": "augment", "content": "a1"},
        {"channel": "augment", "target_model": ModelId.HAIKU.value, "content": "a2"},
        {"channel": "augment", "content": "a3"},
    ]
    with pytest.raises(WireParseError, match="augment cap exceeded"):
        parse_phase_3_dialogue_tool_use(
            {"turns": turns_input}, expected_speaker=ModelId.OPUS
        )


def test_parse_phase_3_augment_three_aggregate_allowed():
    turns_input = [
        {"channel": "augment", "target_model": ModelId.SONNET.value, "content": "a0"},
        {"channel": "augment", "content": "a1"},
        {"channel": "augment", "target_model": ModelId.HAIKU.value, "content": "a2"},
    ]
    turns = parse_phase_3_dialogue_tool_use(
        {"turns": turns_input}, expected_speaker=ModelId.OPUS
    )
    assert len(turns) == 3


def test_parse_phase_3_converge_four_aggregate_refused():
    turns_input = [
        {"channel": "converge", "target_model": ModelId.SONNET.value, "content": f"c{i}"}
        if i % 2 == 0
        else {"channel": "converge", "content": f"c{i}"}
        for i in range(4)
    ]
    with pytest.raises(WireParseError, match="converge cap exceeded"):
        parse_phase_3_dialogue_tool_use(
            {"turns": turns_input}, expected_speaker=ModelId.OPUS
        )


# --- Protocol conformance ------------------------------------------------


class _StubPhase3Client:
    def submit_phase_3_dialogue(self, **kwargs):
        raise NotImplementedError


def test_phase3wire_client_protocol_runtime_checkable():
    assert isinstance(_StubPhase3Client(), Phase3WireClient)
