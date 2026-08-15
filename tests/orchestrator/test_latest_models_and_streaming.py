"""Regression tests for the active model roster and provider streaming."""

from __future__ import annotations

import asyncio
import sys
import types

from golden_lattice.events import ModelStreamDeltaEvent, SessionErrorEvent
from golden_lattice.memory_graph.base import ModelId
from golden_lattice.orchestrator import AnthropicClient, DEFAULT_INVITED_MODELS


def test_default_roster_uses_current_four_model_api_ids():
    assert DEFAULT_INVITED_MODELS == (
        ModelId.FABLE,
        ModelId.OPUS,
        ModelId.SONNET,
        ModelId.HAIKU,
    )
    assert [model.value for model in DEFAULT_INVITED_MODELS] == [
        "claude-fable-5",
        "claude-opus-5",
        "claude-sonnet-5",
        "claude-haiku-4-5-20251001",
    ]


def test_model_stream_delta_is_serializable():
    event = ModelStreamDeltaEvent(
        timestamp_offset_ms=12,
        model_id=ModelId.OPUS,
        phase="phase_1",
        delta="{\"claims\": [",
        delta_kind="tool_input",
    )
    payload = event.model_dump()
    assert payload["event_type"] == "model_stream_delta"
    assert payload["delta"] == "{\"claims\": ["


def test_session_error_event_preserves_provider_context_without_credentials():
    event = SessionErrorEvent(
        timestamp_offset_ms=0,
        message="credit balance unavailable",
        phase="phase_0_proposal",
        model_id=ModelId.HAIKU,
    )
    payload = event.model_dump()
    assert payload["event_type"] == "session_error"
    assert payload["model_id"] == ModelId.HAIKU
    assert "api_key" not in payload["message"]


class _AsyncStream:
    def __init__(self, events, response):
        self._events = events
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._events:
            raise StopAsyncIteration
        return self._events.pop(0)

    async def get_final_message(self):
        return self._response


def test_anthropic_client_uses_stream_and_forwards_tool_input_deltas(monkeypatch):
    class _Delta:
        type = "input_json_delta"
        partial_json = "{\"claims\": []}"

    class _Event:
        type = "content_block_delta"
        delta = _Delta()

    class _Tool:
        type = "tool_use"
        name = "emit_phase_1_response"
        input = {
            "model_id": ModelId.OPUS.value,
            "focus_tag": "correctness",
            "confidence": 0.8,
            "claims": [],
            "response": "streamed",
        }

    class _Response:
        content = [_Tool()]

    calls = []

    class _Messages:
        def stream(self, **kwargs):
            calls.append(kwargs)
            return _AsyncStream([_Event()], _Response())

    class _SDKClient:
        def __init__(self, **kwargs):
            self.messages = _Messages()

    monkeypatch.setitem(
        sys.modules,
        "anthropic",
        types.SimpleNamespace(AsyncAnthropic=_SDKClient),
    )
    deltas = []
    client = AnthropicClient(
        api_key="test",
        stream_callback=lambda model, phase, delta, kind: deltas.append(
            (model, phase, delta, kind)
        ),
    )

    response = asyncio.run(
        client.submit_phase_1_response(
            model_id=ModelId.OPUS,
            original_prompt="p",
            prompt_hash="h",
        )
    )

    assert response.response == "streamed"
    assert calls[0]["model"] == "claude-opus-5"
    assert deltas == [(ModelId.OPUS, "phase_1", "{\"claims\": []}", "tool_input")]