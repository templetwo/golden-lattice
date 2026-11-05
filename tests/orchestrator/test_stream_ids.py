"""Concurrent requests stream on separate ids (review of PR #2, a297b4a, finding 1).

Phase 2 runs every cross-reading and every tagging call at once, so one seat
has several requests streaming together. Each delta must say which request it
belongs to, or the dashboard splices their partial JSON into one buffer.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict

from golden_lattice.events import ModelStreamDeltaEvent
from golden_lattice.memory_graph.schema import CrossReading
from golden_lattice.orchestrator import LatticeConfig, run_lattice_session


def test_concurrent_phase_2_streams_carry_their_own_ids(stub_client):
    callbacks = {}
    stub_client.set_stream_callback = lambda cb: callbacks.setdefault("cb", cb)

    async def cross_reading_hook(*, reader_model, target_model):
        cb = callbacks["cb"]
        cb(reader_model, "phase_2_cross_reading", f"{target_model.value}|a;", "tool_input")
        await asyncio.sleep(0)           # let the reader's other requests interleave
        cb(reader_model, "phase_2_cross_reading", f"{target_model.value}|b;", "tool_input")
        return CrossReading(reader_model=reader_model, target_model=target_model)

    stub_client.cross_reading_hook = cross_reading_hook
    events = []
    run_lattice_session("p", config=LatticeConfig(), client=stub_client, progress_callback=events.append)

    deltas = [e for e in events if isinstance(e, ModelStreamDeltaEvent)]
    assert deltas, "the stub's deltas should reach the event stream"
    by_stream = defaultdict(str)
    for d in deltas:
        target = d.delta.split("|")[0]
        assert d.stream_id == f"phase_2_cross_reading:{d.model_id.value}>{target}"
        assert d.target_model is not None and d.target_model.value == target
        by_stream[d.stream_id] += d.delta
    for stream_id, text in by_stream.items():
        target = stream_id.split(">")[1]
        assert text == f"{target}|a;{target}|b;", stream_id
    readers = defaultdict(set)
    for sid in by_stream:
        readers[sid.split(":")[1].split(">")[0]].add(sid)
    assert all(len(ids) >= 2 for ids in readers.values()), "each reader streams more than one request"


def test_a_lone_request_falls_back_to_seat_and_phase(stub_client):
    callbacks = {}
    stub_client.set_stream_callback = lambda cb: callbacks.setdefault("cb", cb)
    original = stub_client.submit_phase_1_response

    async def phase_1_with_stream(**kwargs):
        callbacks["cb"](kwargs["model_id"], "phase_1", '{"response": "x', "tool_input")
        return await original(**kwargs)

    stub_client.submit_phase_1_response = phase_1_with_stream
    events = []
    run_lattice_session("p", config=LatticeConfig(), client=stub_client, progress_callback=events.append)
    deltas = [e for e in events if isinstance(e, ModelStreamDeltaEvent)]
    assert deltas
    assert all(d.stream_id == f"phase_1:{d.model_id.value}" and d.target_model is None for d in deltas)
