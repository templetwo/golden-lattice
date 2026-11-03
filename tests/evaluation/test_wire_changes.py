"""Strict tools, own-claim enum, and raw capture on parse failure (approved changes).

No network: _stream_message is replaced by fakes.
"""

from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace

import pytest

from golden_lattice.evaluation.pilot import _raw_of
from golden_lattice.memory_graph.base import ModelId
from golden_lattice.orchestrator import anthropic_client as ac
from golden_lattice.orchestrator.errors import OrchestratorTruncationError  # noqa: F401
from golden_lattice.exchange.phase_1_independent import (
    PHASE_1_RESPONSE_TOOL_NAME,
    phase_1_response_tool_schema,
    self_reflection_tool_schema,
)
from golden_lattice.exchange.phase_2_cross_reading import WireParseError


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def test_strict_tool_removes_min_max_and_marks_strict():
    original = phase_1_response_tool_schema()
    before = copy.deepcopy(original)
    strict = ac._strict_tool(original)
    assert strict["strict"] is True
    assert not any(k in {"minimum", "maximum"} for node in _walk(strict) for k in node)
    assert original == before  # the input schema is not mutated


def test_strict_tool_keeps_enum_and_additional_properties_false():
    strict = ac._strict_tool(phase_1_response_tool_schema())
    props = strict["input_schema"]["properties"]
    assert props["focus_tag"]["enum"]
    assert strict["input_schema"]["additionalProperties"] is False


def test_claim_texts_description_refuses_single_string():
    desc = phase_1_response_tool_schema()["input_schema"]["properties"]["claim_texts"]["description"]
    assert "Never a single string" in desc
    assert "one string per claim" in desc


def test_reflection_ids_are_enum_of_own_claims_when_given():
    schema = self_reflection_tool_schema(own_claim_ids=("a" * 16, "b" * 16))
    props = schema["input_schema"]["properties"]
    assert props["strongest_claim_id"]["enum"] == ["a" * 16, "b" * 16]
    assert props["weakest_claim_id"]["enum"] == ["a" * 16, "b" * 16]


def test_reflection_ids_stay_free_text_without_own_claims():
    props = self_reflection_tool_schema()["input_schema"]["properties"]
    assert "enum" not in props["strongest_claim_id"]


def test_wire_parse_error_carries_raw_tool_input(monkeypatch):
    raw = {"response": "x", "focus_tag": "correctness", "confidence": 0.5,
           "claim_texts": "a single string, not a list"}

    async def fake_stream(self, *, model_id, phase, **kwargs):
        block = SimpleNamespace(type="tool_use", name=PHASE_1_RESPONSE_TOOL_NAME, input=raw)
        return SimpleNamespace(stop_reason="tool_use", content=[block], usage=None, model=model_id.value)

    monkeypatch.setattr(ac.AnthropicClient, "_stream_message", fake_stream)
    client = ac.AnthropicClient(api_key="test-key")
    with pytest.raises(WireParseError) as info:
        asyncio.run(client.submit_phase_1_response(
            model_id=ModelId.HAIKU, original_prompt="p", prompt_hash="h",
        ))
    assert _raw_of(info.value) == raw


def test_raw_of_follows_cause_chain_and_returns_none_without_capture():
    class Plain(Exception):
        pass

    try:
        try:
            raise ValueError("inner")
        except ValueError as inner:
            ac._attach_raw(inner, {"k": "v"})
            raise Plain("outer") from inner
    except Plain as outer:
        assert _raw_of(outer) == {"k": "v"}
    assert _raw_of(Plain("bare")) is None


def test_attach_raw_never_masks_the_original_error():
    # Some exception types refuse new attributes. Attaching must not raise.
    ac._attach_raw(object(), {"k": "v"})  # no exception
