"""Tests for the dashboard WebSocket server (Slice 1).

The server is a thin async layer on top of the existing LatticeEvent stream:
  - WebSocket endpoint at /ws broadcasts every event as JSON to connected
    clients
  - HTTP endpoint at / serves the dashboard HTML
  - Event log buffer: late-joining clients receive the prior event stream
    on connect, then live events thereafter

The aiohttp server is started on an ephemeral port for each test; tests
connect via aiohttp.ClientSession.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp import ClientSession, WSMsgType, web

from golden_lattice.dashboard.server import DashboardServer
from golden_lattice.events import (
    Phase4MetricsEvent,
    SessionCompletedEvent,
    SessionStartedEvent,
)
from golden_lattice.memory_graph.base import ModelId


async def _start_server(server: DashboardServer):
    runner = web.AppRunner(server.app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, port


def _make_session_started_event(session_id: str = "test-s") -> SessionStartedEvent:
    return SessionStartedEvent(
        timestamp_offset_ms=0,
        session_id=session_id,
        prompt="p",
        prompt_hash="h",
        models_invited=(ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU),
    )


# --- Connection acceptance ----------------------------------------------


def test_server_accepts_websocket_connection():
    async def _run():
        server = DashboardServer()
        runner, port = await _start_server(server)
        try:
            async with ClientSession() as http:
                async with http.ws_connect(f"ws://127.0.0.1:{port}/ws") as ws:
                    assert not ws.closed
                    await ws.close()
        finally:
            await runner.cleanup()

    asyncio.run(_run())


def test_server_serves_index_html_at_root():
    async def _run():
        server = DashboardServer()
        runner, port = await _start_server(server)
        try:
            async with ClientSession() as http:
                async with http.get(f"http://127.0.0.1:{port}/") as resp:
                    assert resp.status == 200
                    body = await resp.text()
                    assert "Golden Lattice" in body
        finally:
            await runner.cleanup()

    asyncio.run(_run())


# --- Broadcast ----------------------------------------------------------


def test_server_broadcasts_event_to_connected_client():
    async def _run():
        server = DashboardServer()
        runner, port = await _start_server(server)
        try:
            async with ClientSession() as http:
                async with http.ws_connect(f"ws://127.0.0.1:{port}/ws") as ws:
                    # Drain any initial buffered messages (empty log, so none).
                    await asyncio.sleep(0.05)
                    event = _make_session_started_event("broadcast-test")
                    await server.broadcast(event)
                    msg = await asyncio.wait_for(ws.receive(), timeout=2.0)
                    assert msg.type == WSMsgType.TEXT
                    data = json.loads(msg.data)
                    assert data["event_type"] == "session_started"
                    assert data["session_id"] == "broadcast-test"
                    await ws.close()
        finally:
            await runner.cleanup()

    asyncio.run(_run())


def test_server_broadcasts_to_multiple_clients():
    async def _run():
        server = DashboardServer()
        runner, port = await _start_server(server)
        try:
            async with ClientSession() as http:
                async with http.ws_connect(f"ws://127.0.0.1:{port}/ws") as ws1:
                    async with http.ws_connect(f"ws://127.0.0.1:{port}/ws") as ws2:
                        await asyncio.sleep(0.05)
                        event = _make_session_started_event("multi-test")
                        await server.broadcast(event)
                        msg1 = await asyncio.wait_for(ws1.receive(), timeout=2.0)
                        msg2 = await asyncio.wait_for(ws2.receive(), timeout=2.0)
                        d1 = json.loads(msg1.data)
                        d2 = json.loads(msg2.data)
                        assert d1["session_id"] == "multi-test"
                        assert d2["session_id"] == "multi-test"
                        await ws1.close()
                        await ws2.close()
        finally:
            await runner.cleanup()

    asyncio.run(_run())


# --- Event log buffer for late-joining clients --------------------------


def test_late_joining_client_receives_buffered_event_log():
    async def _run():
        server = DashboardServer()
        runner, port = await _start_server(server)
        try:
            # Broadcast two events BEFORE any client connects.
            await server.broadcast(_make_session_started_event("late-test"))
            metrics_event = Phase4MetricsEvent(
                timestamp_offset_ms=100,
                metrics=None,
            )
            await server.broadcast(metrics_event)

            async with ClientSession() as http:
                async with http.ws_connect(f"ws://127.0.0.1:{port}/ws") as ws:
                    msg1 = await asyncio.wait_for(ws.receive(), timeout=2.0)
                    msg2 = await asyncio.wait_for(ws.receive(), timeout=2.0)
                    d1 = json.loads(msg1.data)
                    d2 = json.loads(msg2.data)
                    assert d1["event_type"] == "session_started"
                    assert d1["session_id"] == "late-test"
                    assert d2["event_type"] == "phase_4_metrics"
                    await ws.close()
        finally:
            await runner.cleanup()

    asyncio.run(_run())


def test_event_log_replays_then_continues_live():
    """Late-joining client receives buffered events first, then keeps getting
    live ones."""

    async def _run():
        server = DashboardServer()
        runner, port = await _start_server(server)
        try:
            await server.broadcast(_make_session_started_event("replay-then-live"))

            async with ClientSession() as http:
                async with http.ws_connect(f"ws://127.0.0.1:{port}/ws") as ws:
                    # First message: the buffered session_started.
                    msg1 = await asyncio.wait_for(ws.receive(), timeout=2.0)
                    d1 = json.loads(msg1.data)
                    assert d1["session_id"] == "replay-then-live"

                    # Now broadcast a live event.
                    live_event = SessionCompletedEvent(
                        timestamp_offset_ms=500,
                        session_id="replay-then-live",
                    )
                    await server.broadcast(live_event)
                    msg2 = await asyncio.wait_for(ws.receive(), timeout=2.0)
                    d2 = json.loads(msg2.data)
                    assert d2["event_type"] == "session_completed"
                    await ws.close()
        finally:
            await runner.cleanup()

    asyncio.run(_run())


# --- Reset (for replay re-runs) -----------------------------------------


# --- Inbound prompts (persistent-mode dashboard input) -------------------


def test_client_can_submit_prompt_via_websocket():
    """Client sends {'type':'prompt','text':'...'} → server's wait_for_prompt
    returns that text. This is the dashboard-as-input-surface path used by
    run_lattice_live.py --dashboard in persistent mode."""

    async def _run():
        server = DashboardServer()
        runner, port = await _start_server(server)
        try:
            async with ClientSession() as http:
                async with http.ws_connect(f"ws://127.0.0.1:{port}/ws") as ws:
                    await asyncio.sleep(0.05)
                    await ws.send_str(json.dumps({"type": "prompt", "text": "what is consciousness"}))
                    prompt = await asyncio.wait_for(server.wait_for_prompt(), timeout=2.0)
                    assert prompt == "what is consciousness"
                    await ws.close()
        finally:
            await runner.cleanup()

    asyncio.run(_run())


def test_client_can_signal_quit_via_websocket():
    """Client sends {'type':'quit'} → server's wait_for_prompt returns None
    so the script's loop knows to exit."""

    async def _run():
        server = DashboardServer()
        runner, port = await _start_server(server)
        try:
            async with ClientSession() as http:
                async with http.ws_connect(f"ws://127.0.0.1:{port}/ws") as ws:
                    await asyncio.sleep(0.05)
                    await ws.send_str(json.dumps({"type": "quit"}))
                    result = await asyncio.wait_for(server.wait_for_prompt(), timeout=2.0)
                    assert result is None
                    await ws.close()
        finally:
            await runner.cleanup()

    asyncio.run(_run())


def test_empty_or_invalid_inbound_messages_are_ignored():
    """Whitespace-only prompts, malformed JSON, and unknown message types
    don't get pushed to the prompt queue. A valid prompt sent after still
    arrives correctly."""

    async def _run():
        server = DashboardServer()
        runner, port = await _start_server(server)
        try:
            async with ClientSession() as http:
                async with http.ws_connect(f"ws://127.0.0.1:{port}/ws") as ws:
                    await asyncio.sleep(0.05)
                    # Whitespace-only — ignored.
                    await ws.send_str(json.dumps({"type": "prompt", "text": "   "}))
                    # Garbage JSON — ignored.
                    await ws.send_str("not-json{")
                    # Unknown type — ignored.
                    await ws.send_str(json.dumps({"type": "wat", "text": "x"}))
                    # Now a valid prompt.
                    await ws.send_str(json.dumps({"type": "prompt", "text": "real question"}))
                    prompt = await asyncio.wait_for(server.wait_for_prompt(), timeout=2.0)
                    assert prompt == "real question"
                    await ws.close()
        finally:
            await runner.cleanup()

    asyncio.run(_run())


# --- Reset (for replay re-runs) -----------------------------------------


def test_server_reset_clears_event_log():
    """Replay mode needs to clear the log between runs so a fresh client
    doesn't see stale state from a prior run."""

    async def _run():
        server = DashboardServer()
        runner, port = await _start_server(server)
        try:
            await server.broadcast(_make_session_started_event("first"))
            server.reset_log()

            async with ClientSession() as http:
                async with http.ws_connect(f"ws://127.0.0.1:{port}/ws") as ws:
                    # After reset, log is empty — late client should get no
                    # buffered messages. Send a fresh event to confirm
                    # connection still works.
                    await asyncio.sleep(0.05)
                    await server.broadcast(_make_session_started_event("second"))
                    msg = await asyncio.wait_for(ws.receive(), timeout=2.0)
                    data = json.loads(msg.data)
                    assert data["session_id"] == "second"
                    await ws.close()
        finally:
            await runner.cleanup()

    asyncio.run(_run())
