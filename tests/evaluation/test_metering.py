"""Usage metering records (eval harness step 9). No network: fake responses only."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from golden_lattice.evaluation.metering import MeteredAnthropicClient, usage_record
from golden_lattice.memory_graph.base import ModelId
from golden_lattice.orchestrator import anthropic_client as ac


def _response(model="claude-opus-5", inp=1000, out=500, cw=0, cr=0):
    usage = SimpleNamespace(
        input_tokens=inp,
        output_tokens=out,
        cache_creation_input_tokens=cw,
        cache_read_input_tokens=cr,
    )
    return SimpleNamespace(model=model, usage=usage)


def test_record_captures_requested_and_served_model():
    rec = usage_record(
        requested_model="claude-opus-5",
        phase="phase_1",
        response=_response(model="claude-opus-5-snapshot"),
    )
    assert rec["requested_model"] == "claude-opus-5"
    assert rec["served_model"] == "claude-opus-5-snapshot"


def test_record_cost_counts_cache_tokens_at_input_rate():
    rec = usage_record(
        requested_model="claude-opus-5",
        phase="phase_2",
        response=_response(inp=1_000_000, out=0, cw=500_000, cr=500_000),
    )
    # 2M billed input at $5/MTok, no output.
    assert rec["cost_usd"] == 10.0
    assert rec["cache_write_tokens"] == 500_000
    assert rec["cache_read_tokens"] == 500_000


def test_record_tolerates_missing_usage():
    rec = usage_record(requested_model="claude-sonnet-5", phase="phase_3", response=SimpleNamespace())
    assert rec["input_tokens"] == 0 and rec["output_tokens"] == 0
    assert rec["served_model"] == "claude-sonnet-5"


def test_metered_client_reports_each_call_to_sink(monkeypatch):
    seen = []

    async def fake_stream(self, *, model_id, phase, **kwargs):
        return _response(model=model_id.value)

    monkeypatch.setattr(ac.AnthropicClient, "_stream_message", fake_stream)
    client = MeteredAnthropicClient.__new__(MeteredAnthropicClient)
    client._sink = seen.append

    asyncio.run(MeteredAnthropicClient._stream_message(client, model_id=ModelId.HAIKU, phase="phase_1"))
    assert len(seen) == 1
    assert seen[0]["phase"] == "phase_1"
    assert seen[0]["requested_model"] == ModelId.HAIKU.value
