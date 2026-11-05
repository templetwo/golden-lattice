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


# --- the gate at every call site --------------------------------------------
#
# Each of the six submit methods must refuse a safety-declined response itself.
# Without the gate a refusal falls through to _extract_tool_input and surfaces as
# an untyped "No tool_use block" provider error, which these tests reject.


def _call_phase_0(client):
    return client.submit_investigation_proposal(
        model_id=ModelId.OPUS, original_prompt="p", max_queries=3,
    )


def _call_phase_1(client):
    return client.submit_phase_1_response(
        model_id=ModelId.OPUS, original_prompt="p", prompt_hash="h",
    )


def _call_self_reflection(client):
    return client.submit_self_reflection(
        model_id=ModelId.OPUS, phase_1_response=_phase_1_response(ModelId.OPUS),
    )


def _call_cross_reading(client):
    target = _phase_1_response(ModelId.SONNET)
    return client.submit_cross_reading(
        reader_model=ModelId.OPUS, target_model=ModelId.SONNET, original_prompt="p",
        target_response=target.response, target_claims=target.claims,
    )


def _call_tagging(client):
    return client.submit_phase_2_tagging(
        tagger_model=ModelId.OPUS, original_prompt="p",
        own_claims=_phase_1_response(ModelId.OPUS).claims,
        peer_claims=_phase_1_response(ModelId.SONNET).claims,
    )


def _call_phase_3(client):
    peer = _phase_1_response(ModelId.SONNET)
    return client.submit_phase_3_dialogue(
        speaker_model=ModelId.OPUS, original_prompt="p",
        own_phase_1_response=_phase_1_response(ModelId.OPUS).response,
        peer_phase_1_blocks=((ModelId.SONNET, peer.response, peer.claims),),
    )


CALL_SITES = [
    pytest.param(_call_phase_0, "phase_0_proposal", id="phase_0_proposal"),
    pytest.param(_call_phase_1, "phase_1", id="phase_1"),
    pytest.param(_call_self_reflection, "self_reflection", id="self_reflection"),
    pytest.param(_call_cross_reading, "phase_2_cross_reading", id="phase_2_cross_reading"),
    pytest.param(_call_tagging, "phase_2_tagging", id="phase_2_tagging"),
    pytest.param(_call_phase_3, "phase_3", id="phase_3"),
]


@pytest.mark.parametrize("call, phase", CALL_SITES)
def test_every_call_site_refuses_a_safety_declined_response(monkeypatch, call, phase):
    client = _client(monkeypatch, _refused("cyber"))
    with pytest.raises(OrchestratorRefusalError) as info:
        asyncio.run(call(client))
    assert info.value.phase == phase
    assert info.value.model == ModelId.OPUS  # the calling seat, not a peer
    assert info.value.category == "cyber"
    assert info.value.explanation == "declined"


# --- reading stop_details -----------------------------------------------------


def _gate(response):
    client = ac.AnthropicClient.__new__(ac.AnthropicClient)
    with pytest.raises(OrchestratorRefusalError) as info:
        client._refuse_if_refused(response, model=ModelId.HAIKU, phase="phase_1")
    return info.value


def test_refusal_without_stop_details_has_no_category_and_does_not_crash():
    response = SimpleNamespace(stop_reason="refusal", content=[], usage=None, model="m")
    exc = _gate(response)
    assert exc.category is None
    assert exc.explanation == ""
    assert "category=None" in str(exc)


def test_refusal_details_as_an_object_are_read():
    exc = _gate(SimpleNamespace(
        stop_reason="refusal",
        stop_details=SimpleNamespace(category="bio", explanation="dual-use protocol"),
    ))
    assert exc.category == "bio"
    assert exc.explanation == "dual-use protocol"


def test_refusal_explanation_is_capped_at_300_characters():
    exc = _gate(SimpleNamespace(
        stop_reason="refusal",
        stop_details={"category": "cyber", "explanation": "x" * 1000},
    ))
    assert exc.explanation == "x" * 300


def test_refusal_message_carries_the_explanation_when_present():
    exc = _gate(SimpleNamespace(
        stop_reason="refusal",
        stop_details={"category": "cyber", "explanation": "exploit development"},
    ))
    assert "category=cyber" in str(exc)
    assert "no output was produced" in str(exc)
    assert "exploit development" in str(exc)


def test_refusal_message_has_no_explanation_clause_when_empty():
    exc = OrchestratorRefusalError(model=ModelId.HAIKU, phase="phase_1", category="cyber")
    assert str(exc) == f"{ModelId.HAIKU.value} refused phase_1 (category=cyber); no output was produced."
