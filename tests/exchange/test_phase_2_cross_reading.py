"""Tests for the Phase 2 wire format — schemas, prompts, parsers, refusals."""

import pytest

from golden_lattice.exchange.phase_2_cross_reading import (
    CROSS_READING_TOOL_NAME,
    PHASE_2_TAGGING_TOOL_NAME,
    Phase2WireClient,
    WireParseError,
    build_cross_reading_prompt,
    build_phase_2_tagging_prompt,
    cross_reading_tool_schema,
    parse_cross_reading_tool_use,
    parse_phase_2_tagging_tool_use,
    phase_2_tagging_tool_schema,
)
from golden_lattice.memory_graph.base import ModelId, Phase, claim_id_for
from golden_lattice.memory_graph.schema import Claim
from golden_lattice.memory_graph.tagging import (
    EdgeCaseTag,
    StructuralPatternTag,
    TAG_VOCABULARY_VERSION,
)


def _phase1_claim(model: ModelId, text: str) -> Claim:
    return Claim(
        claim_id=claim_id_for(model, Phase.INDEPENDENT, text),
        source_model=model,
        source_phase=Phase.INDEPENDENT,
        text=text,
    )


# --- Tool schemas --------------------------------------------------------


def test_cross_reading_tool_schema_well_formed():
    schema = cross_reading_tool_schema()
    assert schema["name"] == CROSS_READING_TOOL_NAME
    assert "input_schema" in schema
    props = schema["input_schema"]["properties"]
    assert set(props) == {"agreements", "disagreements", "missing_texts"}
    assert schema["input_schema"]["additionalProperties"] is False


def test_phase_2_tagging_tool_schema_advertises_closed_vocabulary():
    schema = phase_2_tagging_tool_schema()
    assert schema["name"] == PHASE_2_TAGGING_TOOL_NAME
    claim_tag_props = schema["input_schema"]["properties"]["peer_tags"]["items"]["properties"]
    edge_enum = claim_tag_props["edge_case_tags"]["items"]["enum"]
    structural_enum = claim_tag_props["structural_pattern_tags"]["items"]["enum"]
    assert set(edge_enum) == {t.value for t in EdgeCaseTag}
    assert set(structural_enum) == {t.value for t in StructuralPatternTag}
    assert "other" in edge_enum
    assert "other" in structural_enum


# --- Prompt builders -----------------------------------------------------


def test_cross_reading_prompt_names_roles_and_tool():
    target_claims = (_phase1_claim(ModelId.SONNET, "sonnet says alpha"),)
    system, user = build_cross_reading_prompt(
        reader_model=ModelId.OPUS,
        target_model=ModelId.SONNET,
        original_prompt="design a cache",
        target_response="here is what sonnet thinks...",
        target_claims=target_claims,
    )
    assert ModelId.OPUS.value in system
    assert ModelId.SONNET.value in system
    assert CROSS_READING_TOOL_NAME in system
    assert "design a cache" in user
    assert target_claims[0].claim_id in user
    assert "sonnet says alpha" in user


def test_cross_reading_prompt_includes_self_reflection_when_provided():
    target_claims = (_phase1_claim(ModelId.SONNET, "sonnet alpha"),)
    _, user = build_cross_reading_prompt(
        reader_model=ModelId.OPUS,
        target_model=ModelId.SONNET,
        original_prompt="p",
        target_response="r",
        target_claims=target_claims,
        target_self_reflection="my strongest claim is X, my weakest is Y",
    )
    assert "strongest claim is X" in user


def test_phase_2_tagging_prompt_uses_content_vs_form_routing_criterion():
    """Empirically-driven revision: the first live run produced a translation
    collapse where 'adversarial_input' (an EdgeCaseTag value) was emitted as
    a structural_pattern_tag. The fix routes via content-vs-form: edge cases
    concern what the claim is about; structural patterns concern how the claim
    is organizing thought. A future contributor weakening this routing should
    fail this test."""
    own = (_phase1_claim(ModelId.HAIKU, "haiku alpha"),)
    peer = (_phase1_claim(ModelId.OPUS, "opus alpha"),)
    system, _ = build_phase_2_tagging_prompt(
        tagger_model=ModelId.HAIKU,
        original_prompt="p",
        own_claims=own,
        peer_claims=peer,
    )
    assert "claim content" in system
    assert "claim form" in system
    assert "what the claim is about" in system
    assert "how the claim is organizing" in system
    assert "Tag according to what the claim is, not how it sounds" in system


def test_phase_2_tagging_prompt_lists_vocabularies_and_pins_version():
    own = (_phase1_claim(ModelId.HAIKU, "haiku alpha"),)
    peer = (_phase1_claim(ModelId.OPUS, "opus alpha"),)
    system, user = build_phase_2_tagging_prompt(
        tagger_model=ModelId.HAIKU,
        original_prompt="p",
        own_claims=own,
        peer_claims=peer,
    )
    assert TAG_VOCABULARY_VERSION in system
    assert EdgeCaseTag.BOUNDARY_CONDITION.value in system
    assert StructuralPatternTag.FRAMING_CHOICE.value in system
    assert PHASE_2_TAGGING_TOOL_NAME in system
    assert own[0].claim_id in user
    assert peer[0].claim_id in user


# --- Parser: cross_reading -----------------------------------------------


def test_parse_cross_reading_happy_path_assigns_missing_claim_ids():
    target_claim = _phase1_claim(ModelId.SONNET, "sonnet alpha")
    tool_input = {
        "agreements": [target_claim.claim_id],
        "disagreements": [],
        "missing_texts": ["a thing sonnet missed"],
    }
    cr = parse_cross_reading_tool_use(
        tool_input,
        expected_reader=ModelId.OPUS,
        expected_target=ModelId.SONNET,
    )
    assert cr.reader_model is ModelId.OPUS
    assert cr.target_model is ModelId.SONNET
    assert len(cr.agreements) == 1
    assert cr.agreements[0].claim_id == target_claim.claim_id
    assert len(cr.missing) == 1
    expected_id = claim_id_for(ModelId.OPUS, Phase.CROSS_READING, "a thing sonnet missed")
    assert cr.missing[0].claim_id == expected_id
    assert cr.missing[0].source_model is ModelId.OPUS
    assert cr.missing[0].source_phase is Phase.CROSS_READING


def test_parse_cross_reading_rejects_self_reading():
    with pytest.raises(WireParseError, match="must differ"):
        parse_cross_reading_tool_use(
            {"agreements": [], "disagreements": [], "missing_texts": []},
            expected_reader=ModelId.OPUS,
            expected_target=ModelId.OPUS,
        )


def test_parse_cross_reading_rejects_non_dict_input():
    with pytest.raises(WireParseError, match="must be a dict"):
        parse_cross_reading_tool_use(
            "not a dict",  # type: ignore[arg-type]
            expected_reader=ModelId.OPUS,
            expected_target=ModelId.SONNET,
        )


def test_parse_cross_reading_rejects_disagreement_missing_reason():
    tool_input = {
        "agreements": [],
        "disagreements": [{"target_claim_id": "abc", "reason": ""}],
        "missing_texts": [],
    }
    with pytest.raises(WireParseError, match="reason must be a non-empty string"):
        parse_cross_reading_tool_use(
            tool_input,
            expected_reader=ModelId.OPUS,
            expected_target=ModelId.SONNET,
        )


def test_parse_cross_reading_rejects_non_dict_disagreement():
    tool_input = {
        "agreements": [],
        "disagreements": ["a string instead of a dict"],
        "missing_texts": [],
    }
    with pytest.raises(WireParseError, match="disagreement entries must be objects"):
        parse_cross_reading_tool_use(
            tool_input,
            expected_reader=ModelId.OPUS,
            expected_target=ModelId.SONNET,
        )


def test_parse_cross_reading_rejects_unknown_agreement_when_valid_ids_provided():
    tool_input = {
        "agreements": ["ghost_claim"],
        "disagreements": [],
        "missing_texts": [],
    }
    with pytest.raises(WireParseError, match="agreement references unknown claim_id"):
        parse_cross_reading_tool_use(
            tool_input,
            expected_reader=ModelId.OPUS,
            expected_target=ModelId.SONNET,
            valid_claim_ids={"some_other_id"},
        )


def test_parse_cross_reading_skips_resolution_when_valid_ids_none():
    """When valid_claim_ids is None, the parser defers resolution to the schema's Session validator."""
    tool_input = {
        "agreements": ["ghost_claim"],
        "disagreements": [],
        "missing_texts": [],
    }
    # Should not raise — resolution check is opt-in at the wire layer
    cr = parse_cross_reading_tool_use(
        tool_input,
        expected_reader=ModelId.OPUS,
        expected_target=ModelId.SONNET,
        valid_claim_ids=None,
    )
    assert cr.agreements[0].claim_id == "ghost_claim"


def test_parse_cross_reading_rejects_empty_missing_text():
    tool_input = {
        "agreements": [],
        "disagreements": [],
        "missing_texts": ["   "],
    }
    with pytest.raises(WireParseError, match="missing_texts entries must be non-empty"):
        parse_cross_reading_tool_use(
            tool_input,
            expected_reader=ModelId.OPUS,
            expected_target=ModelId.SONNET,
        )


# --- Parser: phase_2_tagging ---------------------------------------------


def test_parse_phase_2_tagging_happy_path():
    own = _phase1_claim(ModelId.OPUS, "opus alpha")
    peer = _phase1_claim(ModelId.SONNET, "sonnet alpha")
    tool_input = {
        "peer_tags": [
            {
                "claim_id": peer.claim_id,
                "edge_case_tags": [EdgeCaseTag.BOUNDARY_CONDITION.value],
                "structural_pattern_tags": [],
            }
        ],
        "self_tags": [
            {
                "claim_id": own.claim_id,
                "edge_case_tags": [],
                "structural_pattern_tags": [StructuralPatternTag.FRAMING_CHOICE.value],
            }
        ],
    }
    tagging = parse_phase_2_tagging_tool_use(
        tool_input,
        expected_tagger=ModelId.OPUS,
        valid_claim_ids={own.claim_id, peer.claim_id},
    )
    assert tagging.tagger_model is ModelId.OPUS
    assert len(tagging.peer_tags) == 1
    assert tagging.peer_tags[0].edge_case_tags == (EdgeCaseTag.BOUNDARY_CONDITION,)
    assert len(tagging.self_tags) == 1
    assert tagging.self_tags[0].structural_pattern_tags == (StructuralPatternTag.FRAMING_CHOICE,)


def test_parse_phase_2_tagging_skips_resolution_when_valid_ids_none():
    """Symmetric with cross-reading parser: when valid_claim_ids is None, defer resolution to schema."""
    tool_input = {
        "peer_tags": [
            {"claim_id": "ghost_id", "edge_case_tags": [], "structural_pattern_tags": []}
        ],
        "self_tags": [],
    }
    # Should not raise — resolution is opt-in at the wire layer
    tagging = parse_phase_2_tagging_tool_use(
        tool_input,
        expected_tagger=ModelId.OPUS,
        valid_claim_ids=None,
    )
    assert tagging.peer_tags[0].claim_id == "ghost_id"


def test_parse_phase_2_tagging_rejects_unknown_claim_id():
    tool_input = {
        "peer_tags": [
            {"claim_id": "ghost_id", "edge_case_tags": [], "structural_pattern_tags": []}
        ],
        "self_tags": [],
    }
    with pytest.raises(WireParseError, match="unknown claim_id"):
        parse_phase_2_tagging_tool_use(
            tool_input,
            expected_tagger=ModelId.OPUS,
            valid_claim_ids={"some_other_id"},
        )


def test_parse_phase_2_tagging_rejects_unknown_tag_value():
    own = _phase1_claim(ModelId.OPUS, "opus alpha")
    tool_input = {
        "peer_tags": [],
        "self_tags": [
            {
                "claim_id": own.claim_id,
                "edge_case_tags": ["not_a_real_tag"],
                "structural_pattern_tags": [],
            }
        ],
    }
    with pytest.raises(WireParseError, match="unknown edge_case tag"):
        parse_phase_2_tagging_tool_use(
            tool_input,
            expected_tagger=ModelId.OPUS,
            valid_claim_ids={own.claim_id},
        )


def test_parse_phase_2_tagging_rejects_non_list_tag_arrays():
    tool_input = {
        "peer_tags": [
            {
                "claim_id": "abc",
                "edge_case_tags": "not_a_list",
                "structural_pattern_tags": [],
            }
        ],
        "self_tags": [],
    }
    with pytest.raises(WireParseError, match="tag arrays must be lists"):
        parse_phase_2_tagging_tool_use(
            tool_input,
            expected_tagger=ModelId.OPUS,
            valid_claim_ids={"abc"},
        )


# --- Protocol conformance ------------------------------------------------


class _StubPhase2Client:
    """Minimal stub used to verify the Protocol surface."""

    def submit_cross_reading(self, **kwargs):
        raise NotImplementedError

    def submit_phase_2_tagging(self, **kwargs):
        raise NotImplementedError


def test_phase2wire_client_protocol_runtime_checkable():
    client = _StubPhase2Client()
    assert isinstance(client, Phase2WireClient)
