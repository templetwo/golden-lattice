"""Typed refusal: a safety-declined response is a refusal with its category, never a missing tool block."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from golden_lattice.exchange.phase_1_independent import PHASE_1_RESPONSE_TOOL_NAME
from golden_lattice.memory_graph.base import ModelId
from golden_lattice.orchestrator import anthropic_client as ac
from golden_lattice.orchestrator.errors import OrchestratorRefusalError


def _refused(category="cyber"):
    return SimpleNamespace(
        stop_reason="refusal",
        stop_details=SimpleNamespace(category=category, explanation="declined"),
        content=[],
        usage=None,
        model="claude-opus-5",
    )


def _client(monkeypatch, response):
    async def fake_stream(self, *, model_id, phase, **kwargs):
        return response

    monkeypatch.setattr(ac.AnthropicClient, "_stream_message", fake_stream)
    client = ac.AnthropicClient.__new__(ac.AnthropicClient)
    client._allowance = ac.TokenAllowance()
    client.retry_log = []
    return client


def test_refusal_is_reported_with_its_category_not_as_a_missing_tool_block(monkeypatch):
    client = _client(monkeypatch, _refused("cyber"))
    with pytest.raises(OrchestratorRefusalError) as info:
        asyncio.run(client.submit_phase_1_response(
            model_id=ModelId.OPUS, original_prompt="p", prompt_hash="h",
        ))
    assert info.value.category == "cyber"
    assert info.value.model == ModelId.OPUS
    assert info.value.phase == "phase_1"
    assert "no output was produced" in str(info.value)


def _phase_1_response(model):
    from datetime import datetime, timezone

    from golden_lattice.memory_graph.base import FocusTag, Phase, claim_id_for
    from golden_lattice.memory_graph.schema import Claim, IndependentResponse

    now = datetime(2026, 11, 5, tzinfo=timezone.utc)
    claims = tuple(
        Claim(claim_id=claim_id_for(model, Phase.INDEPENDENT, f"{model.value} {t}"),
              source_model=model, source_phase=Phase.INDEPENDENT, text=f"{model.value} {t}")
        for t in ("alpha", "beta")
    )
    return IndependentResponse(
        model_id=model, prompt_hash="h", response=f"{model.value} response",
        focus_tag=FocusTag.CORRECTNESS, confidence=0.8, claims=claims,
        generation_started_at=now, generation_completed_at=now,
    )


def test_refusal_surfaces_on_a_later_phase_too(monkeypatch):
    client = _client(monkeypatch, _refused("bio"))
    with pytest.raises(OrchestratorRefusalError) as info:
        asyncio.run(client.submit_self_reflection(
            model_id=ModelId.SONNET,
            phase_1_response=_phase_1_response(ModelId.SONNET),
        ))
    assert info.value.phase == "self_reflection"
    assert info.value.category == "bio"


def test_tool_use_response_passes_the_refusal_check():
    client = ac.AnthropicClient.__new__(ac.AnthropicClient)
    assert client._refuse_if_refused(
        SimpleNamespace(stop_reason="tool_use"), model=ModelId.HAIKU, phase="phase_1",
    ) is None


def test_refusal_details_as_a_dict_are_read(monkeypatch):
    response = SimpleNamespace(stop_reason="refusal", stop_details={"category": "general_harms", "explanation": "x"},
                               content=[], usage=None, model="m")
    client = ac.AnthropicClient.__new__(ac.AnthropicClient)
    with pytest.raises(OrchestratorRefusalError) as info:
        client._refuse_if_refused(response, model=ModelId.HAIKU, phase="phase_3")
    assert info.value.category == "general_harms"


def test_refusal_is_not_retried_by_the_transport(monkeypatch):
    # _stream_message returns the refusal normally; the caller refuses it. No retry happens.
    calls = []

    async def once(self, *, model_id, phase, **kwargs):
        calls.append(phase)
        return _refused()

    monkeypatch.setattr(ac.AnthropicClient, "_stream_once", once)
    client = ac.AnthropicClient.__new__(ac.AnthropicClient)
    client.retry_log = []
    client.RETRY_DELAYS = (0.0, 0.0)
    result = asyncio.run(client._stream_message(model_id=ModelId.HAIKU, phase="phase_1"))
    assert result.stop_reason == "refusal"
    assert len(calls) == 1
    assert client.retry_log == []
