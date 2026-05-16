"""Tests for the dashboard's replay-to-server bridge (Slice 3).

The bridge walks a persisted Session via replay_session_events and emits
every event to a DashboardServer's broadcast (which broadcasts to
connected browser clients).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from aiohttp import ClientSession, WSMsgType, web

from golden_lattice.dashboard.replay_bridge import replay_session_to_server
from golden_lattice.dashboard.server import DashboardServer
from golden_lattice.memory_graph.store import JsonFileSessionStore


def test_replay_bridge_emits_every_event_from_persisted_session():
    """A real persisted session round-trips through the bridge: each
    LatticeEvent the replay emitter would yield is broadcast to the
    server (and appears in its event_log)."""

    async def _run():
        sessions_dir = Path(__file__).parent.parent.parent / "sessions"
        session_id = "session_20260516_190624_77b1da30"  # the successful Phase 0 run
        if not (sessions_dir / f"{session_id}.session.json").exists():
            import pytest
            pytest.skip(f"persisted session {session_id} not present")

        store = JsonFileSessionStore(sessions_dir)
        session = store.load(session_id)
        server = DashboardServer()
        await replay_session_to_server(session, server, speed=1000.0)  # fast-forward
        # Every event in the persisted session must be in the server's log.
        assert len(server.event_log) > 10
        # First event is session_started, last is session_completed.
        first = json.loads(server.event_log[0])
        last = json.loads(server.event_log[-1])
        assert first["event_type"] == "session_started"
        assert last["event_type"] == "session_completed"

    asyncio.run(_run())


def test_replay_bridge_to_connected_client_receives_events_in_order():
    """End-to-end with a connected client: events arrive in canonical
    order matching replay_session_events output."""

    async def _run():
        sessions_dir = Path(__file__).parent.parent.parent / "sessions"
        session_id = "session_20260516_190624_77b1da30"
        if not (sessions_dir / f"{session_id}.session.json").exists():
            import pytest
            pytest.skip(f"persisted session {session_id} not present")

        store = JsonFileSessionStore(sessions_dir)
        session = store.load(session_id)
        server = DashboardServer()
        runner = web.AppRunner(server.app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            async with ClientSession() as http:
                async with http.ws_connect(f"ws://127.0.0.1:{port}/ws") as ws:
                    # Run the replay (fast); the client receives every event.
                    await replay_session_to_server(session, server, speed=1000.0)
                    received: list[dict] = []
                    # Drain — read until we see session_completed.
                    while True:
                        try:
                            msg = await asyncio.wait_for(ws.receive(), timeout=1.0)
                        except asyncio.TimeoutError:
                            break
                        if msg.type != WSMsgType.TEXT:
                            break
                        data = json.loads(msg.data)
                        received.append(data)
                        if data.get("event_type") == "session_completed":
                            break
                    assert received[0]["event_type"] == "session_started"
                    assert any(
                        d["event_type"] == "phase_0_datetime_grounding"
                        for d in received
                    )
                    assert received[-1]["event_type"] == "session_completed"
                    await ws.close()
        finally:
            await runner.cleanup()

    asyncio.run(_run())
