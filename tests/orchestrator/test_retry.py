"""M2 retry policy: transient failures retried, everything else refused at once."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from golden_lattice.memory_graph.base import ModelId
from golden_lattice.orchestrator import anthropic_client as ac
from golden_lattice.orchestrator.errors import OrchestratorProviderError


class _Status(Exception):
    def __init__(self, status: int):
        super().__init__(f"status {status}")
        self.status_code = status


class APIConnectionError(Exception):
    """Name-matched like the SDK's connection error (no status code)."""


OK = SimpleNamespace(stop_reason="end_turn", content=[], usage=None, model="claude-haiku-5-5")


def _client(monkeypatch, outcomes):
    """A client whose _stream_once replays the outcomes: an exception, or a response."""
    calls = []

    async def fake_once(self, *, model_id, phase, **kwargs):
        calls.append(phase)
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(ac.AnthropicClient, "_stream_once", fake_once)
    client = ac.AnthropicClient.__new__(ac.AnthropicClient)
    client.retry_log = []
    client.RETRY_DELAYS = (0.0, 0.0)
    return client, calls


def _run(client):
    return asyncio.run(client._stream_message(model_id=ModelId.HAIKU, phase="phase_1"))


@pytest.mark.parametrize("failure", [_Status(429), _Status(529), _Status(503), APIConnectionError()])
def test_transient_failure_is_retried_then_succeeds(monkeypatch, failure):
    client, calls = _client(monkeypatch, [failure, OK])
    assert _run(client) is OK
    assert len(calls) == 2
    assert len(client.retry_log) == 1
    assert client.retry_log[0]["attempt"] == 1


def test_retries_are_bounded_then_the_failure_surfaces(monkeypatch):
    client, calls = _client(monkeypatch, [_Status(529), _Status(529), _Status(529)])
    with pytest.raises(_Status):
        _run(client)
    assert len(calls) == 3  # one attempt plus two retries
    assert len(client.retry_log) == 2


@pytest.mark.parametrize("failure", [_Status(400), _Status(401), _Status(404)])
def test_client_errors_are_not_retried(monkeypatch, failure):
    client, calls = _client(monkeypatch, [failure, OK])
    with pytest.raises(_Status):
        _run(client)
    assert len(calls) == 1
    assert client.retry_log == []


def test_retry_log_records_the_model_and_phase(monkeypatch):
    client, _ = _client(monkeypatch, [_Status(529), OK])
    _run(client)
    assert client.retry_log[0]["model"] == ModelId.HAIKU.value
    assert client.retry_log[0]["phase"] == "phase_1"
    assert client.retry_log[0]["status"] == 529


def test_sdk_level_retries_are_off():
    pytest.importorskip("anthropic")
    client = ac.AnthropicClient(api_key="test-key")
    assert client._client.max_retries == 0


def test_truncation_is_not_retried_by_the_transport(monkeypatch):
    # A max_tokens stop returns normally from _stream_message; the refusal happens in the caller.
    truncated = SimpleNamespace(stop_reason="max_tokens", content=[], usage=None, model="x")
    client, calls = _client(monkeypatch, [truncated, OK])
    assert _run(client) is truncated
    assert len(calls) == 1
