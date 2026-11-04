"""Claim-ID enums and nullable Phase 3 targets (approved 2026-11-04).

Schema builders are checked directly, then the client is checked to pass the
real ID sets at each call site. No network: _stream_message is faked and raises
once the request has been built.
"""

from __future__ import annotations

import asyncio

import pytest

from golden_lattice.exchange.phase_2_cross_reading import (
    cross_reading_tool_schema,
    parse_phase_2_tagging_tool_use,
    phase_2_tagging_tool_schema,
)
from golden_lattice.exchange.phase_3_dialogue import (
    parse_phase_3_dialogue_tool_use,
    phase_3_dialogue_tool_schema,
)
from golden_lattice.memory_graph.base import Phase, ModelId, claim_id_for
from golden_lattice.memory_graph.schema import Claim
from golden_lattice.orchestrator import anthropic_client as ac

OWN = ("0123456789abcdef", "fedcba9876543210")


class _Stop(Exception):
    pass


def _claim(model: ModelId, text: str) -> Claim:
    return Claim(
        claim_id=claim_id_for(model, Phase.INDEPENDENT, text),
        source_model=model,
        source_phase=Phase.INDEPENDENT,
        text=text,
    )


def test_cross_reading_ids_are_enum_of_target_claims():
    props = cross_reading_tool_schema(target_claim_ids=OWN)["input_schema"]["properties"]
    assert props["agreements"]["items"]["enum"] == list(OWN)
    disagreement_item = props["disagreements"]["items"]
    assert disagreement_item["properties"]["target_claim_id"]["enum"] == list(OWN)


def test_cross_reading_ids_stay_free_without_target_claims():
    props = cross_reading_tool_schema()["input_schema"]["properties"]
    assert "enum" not in props["agreements"]["items"]


def test_tagging_requires_every_field_and_enums_claim_ids():
    item = phase_2_tagging_tool_schema(claim_ids=OWN)["input_schema"]["properties"]["peer_tags"]["items"]
    assert item["required"] == ["claim_id", "edge_case_tags", "structural_pattern_tags"]
    assert set(item["required"]) == set(item["properties"])
    assert item["properties"]["claim_id"]["enum"] == list(OWN)


def test_phase_3_targets_are_nullable_and_enumerated():
    item = phase_3_dialogue_tool_schema(
        target_models=("claude-sonnet-5", "claude-haiku-5-5"), claim_ids=OWN
    )["input_schema"]["properties"]["turns"]["items"]
    target_model = item["properties"]["target_model"]["anyOf"]
    assert {"type": "null"} in target_model
    assert target_model[0]["enum"] == ["claude-sonnet-5", "claude-haiku-5-5"]
    target_ids = item["properties"]["target_claim_ids"]["anyOf"]
    assert {"type": "null"} in target_ids
    assert target_ids[0]["items"]["enum"] == list(OWN)


def test_phase_3_parser_accepts_null_targets_on_untargeted_turns():
    turns = parse_phase_3_dialogue_tool_use(
        {"turns": [{"channel": "augment", "target_model": None,
                    "target_claim_ids": None, "content": "add a point"}]},
        expected_speaker=ModelId.OPUS,
    )
    assert len(turns) == 1
    assert turns[0].target_model is None
    assert tuple(turns[0].target_claim_ids) == ()


def test_tagging_parser_accepts_empty_tag_arrays():
    tagging = parse_phase_2_tagging_tool_use(
        {"peer_tags": [{"claim_id": OWN[0], "edge_case_tags": [], "structural_pattern_tags": []}],
         "self_tags": []},
        expected_tagger=ModelId.OPUS,
    )
    assert tagging is not None


def _capture_tools(monkeypatch, call):
    seen = {}

    async def fake_stream(self, *, model_id, phase, **kwargs):
        seen["tools"] = kwargs["tools"]
        raise _Stop()

    monkeypatch.setattr(ac.AnthropicClient, "_stream_message", fake_stream)
    client = ac.AnthropicClient(api_key="test-key")
    with pytest.raises(Exception):
        asyncio.run(call(client))
    return seen["tools"][0]["input_schema"]


def test_client_passes_phase_3_peer_enums(monkeypatch):
    sonnet = _claim(ModelId.SONNET, "sonnet alpha")
    haiku = _claim(ModelId.HAIKU, "haiku alpha")
    blocks = (
        (ModelId.SONNET, "sonnet response", (sonnet,)),
        (ModelId.HAIKU, "haiku response", (haiku,)),
    )
    schema = _capture_tools(monkeypatch, lambda c: c.submit_phase_3_dialogue(
        speaker_model=ModelId.OPUS, original_prompt="p",
        own_phase_1_response="opus", peer_phase_1_blocks=blocks,
    ))
    item = schema["properties"]["turns"]["items"]
    assert item["properties"]["target_model"]["anyOf"][0]["enum"] == [
        ModelId.SONNET.value, ModelId.HAIKU.value]
    assert item["properties"]["target_claim_ids"]["anyOf"][0]["items"]["enum"] == [
        sonnet.claim_id, haiku.claim_id]


def test_client_passes_cross_reading_target_enum(monkeypatch):
    target = (_claim(ModelId.SONNET, "sonnet alpha"),)
    schema = _capture_tools(monkeypatch, lambda c: c.submit_cross_reading(
        reader_model=ModelId.OPUS, target_model=ModelId.SONNET, original_prompt="p",
        target_response="resp", target_claims=target,
    ))
    assert schema["properties"]["agreements"]["items"]["enum"] == [target[0].claim_id]


def test_client_passes_tagging_claim_enum(monkeypatch):
    own = (_claim(ModelId.OPUS, "opus alpha"),)
    peers = (_claim(ModelId.SONNET, "sonnet alpha"),)
    schema = _capture_tools(monkeypatch, lambda c: c.submit_phase_2_tagging(
        tagger_model=ModelId.OPUS, original_prompt="p",
        own_claims=own, peer_claims=peers,
    ))
    item = schema["properties"]["peer_tags"]["items"]
    assert item["properties"]["claim_id"]["enum"] == [own[0].claim_id, peers[0].claim_id]
