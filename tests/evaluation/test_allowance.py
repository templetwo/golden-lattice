"""Per-phase token allowance and truncation refusal (pilot review, steps 1 and 2).

No network: _stream_message is replaced by fakes. The real AnthropicClient
methods run, so the caps and the refusal are tested where they are applied.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from golden_lattice.evaluation.cost import worst_case_session_usd
from golden_lattice.evaluation.metering import MeteredAnthropicClient
from golden_lattice.memory_graph.base import ModelId
from golden_lattice.orchestrator import anthropic_client as ac
from golden_lattice.orchestrator.config import TokenAllowance
from golden_lattice.orchestrator.errors import OrchestratorTruncationError


class _Captured(Exception):
    """Raised by the fake to stop the method right after the call is built."""


def test_allowance_defaults_match_the_pilot_review():
    a = TokenAllowance()
    assert a.phase_0_proposal == 4096
    assert a.phase_1_response == 16384
    assert a.self_reflection == 4096
    assert a.phase_2_cross_reading == 8192
    assert a.phase_2_tagging == 8192
    assert a.phase_3_dialogue == 12288


@pytest.mark.parametrize("field", list(TokenAllowance.model_fields))
def test_allowance_refuses_non_positive_values(field):
    with pytest.raises(ValidationError):
        TokenAllowance(**{field: 0})


def test_phase_1_call_receives_configured_phase_1_cap(monkeypatch):
    seen = []

    async def fake_stream(self, *, model_id, phase, **kwargs):
        seen.append({"phase": phase, **kwargs})
        raise _Captured()

    monkeypatch.setattr(ac.AnthropicClient, "_stream_message", fake_stream)
    client = ac.AnthropicClient(api_key="test-key", allowance=TokenAllowance(phase_1_response=9999))
    with pytest.raises(Exception):
        asyncio.run(client.submit_phase_1_response(
            model_id=ModelId.OPUS, original_prompt="p", prompt_hash="h",
        ))
    assert seen[0]["phase"] == "phase_1"
    assert seen[0]["max_tokens"] == 9999


def test_truncated_response_refused_before_parsing(monkeypatch):
    async def fake_stream(self, *, model_id, phase, **kwargs):
        return SimpleNamespace(
            stop_reason="max_tokens",
            usage=SimpleNamespace(output_tokens=16384),
            content=[],
        )

    monkeypatch.setattr(ac.AnthropicClient, "_stream_message", fake_stream)
    client = ac.AnthropicClient(api_key="test-key")
    with pytest.raises(OrchestratorTruncationError) as info:
        asyncio.run(client.submit_phase_1_response(
            model_id=ModelId.OPUS, original_prompt="p", prompt_hash="h",
        ))
    assert info.value.max_tokens == 16384
    assert info.value.output_tokens == 16384
    assert info.value.phase == "phase_1"


def test_truncated_call_is_still_metered(monkeypatch):
    # The sink must record the call before the refusal, so truncated spend
    # is never dropped from the ledger.
    recorded = []

    async def fake_stream(self, *, model_id, phase, **kwargs):
        return SimpleNamespace(
            model=model_id.value,
            stop_reason="max_tokens",
            usage=SimpleNamespace(input_tokens=100, output_tokens=16384,
                                  cache_creation_input_tokens=0, cache_read_input_tokens=0),
            content=[],
        )

    monkeypatch.setattr(ac.AnthropicClient, "_stream_message", fake_stream)
    client = MeteredAnthropicClient(api_key="test-key", sink=recorded.append)
    with pytest.raises(OrchestratorTruncationError):
        asyncio.run(client.submit_phase_1_response(
            model_id=ModelId.OPUS, original_prompt="p", prompt_hash="h",
        ))
    assert len(recorded) == 1
    assert recorded[0]["stop_reason"] == "max_tokens"
    assert recorded[0]["output_tokens"] == 16384


def test_tool_use_response_is_not_refused_by_truncation_check():
    client = ac.AnthropicClient.__new__(ac.AnthropicClient)
    client._allowance = TokenAllowance()
    # Returns silently for a non-truncated stop reason.
    assert client._refuse_if_truncated(
        SimpleNamespace(stop_reason="tool_use"),
        model=ModelId.HAIKU, phase="phase_1", max_tokens=16384,
    ) is None


def test_worst_case_estimate_at_default_caps_matches_review():
    # Review figure for N=3 at the re-pilot caps: about $13.10 per session.
    assert worst_case_session_usd(3) == pytest.approx(13.1016, abs=0.01)
