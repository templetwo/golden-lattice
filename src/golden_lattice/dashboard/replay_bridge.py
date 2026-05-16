"""Bridge between replay_session_events and the DashboardServer.

Walks a persisted Session via the replay emitter, emits each LatticeEvent
to the server's broadcast at lived-time pace (scaled by `speed`). The
server fans the events out to any connected browser clients in real time.

speed=1.0 plays at lived time (the asymmetric Phase 1 latency renders as
seconds-as-they-happened in the browser). speed=10.0 fast-forwards.
speed=1000.0 effectively dumps all events as fast as the event loop will
deliver them — useful for tests.
"""

from __future__ import annotations

import asyncio

from golden_lattice.dashboard.server import DashboardServer
from golden_lattice.memory_graph.schema import Session
from golden_lattice.replay import replay_session_events


async def replay_session_to_server(
    session: Session,
    server: DashboardServer,
    *,
    speed: float = 1.0,
) -> None:
    """Drive the dashboard server's broadcast from a persisted Session.

    Iterates replay_session_events(session) — the same canonical event
    stream the live orchestrator emits — and broadcasts each event. Sleeps
    between events based on timestamp_offset_ms delta divided by speed so
    the browser experiences the run at lived-time pace.
    """
    prev_offset = 0
    for event in replay_session_events(session):
        delay_ms = event.timestamp_offset_ms - prev_offset
        if delay_ms > 0 and speed > 0:
            await asyncio.sleep(delay_ms / 1000.0 / speed)
        await server.broadcast(event)
        prev_offset = event.timestamp_offset_ms
