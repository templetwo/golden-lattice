"""DashboardServer — aiohttp app with HTML at / and WebSocket at /ws.

Broadcasts LatticeEvents to all connected clients as JSON. Maintains an
event log so late-joining clients receive the prior stream on connect
(replay-on-attach) and then keep getting live events thereafter.

Designed for two consumers:
  - Live: orchestrator's progress_callback bridges into broadcast() per event
  - Replay: dashboard.replay walks a persisted Session and emits events
            into broadcast() at lived-time pace

The same DashboardServer instance handles both. reset_log() clears the
buffer between runs so a fresh replay doesn't inherit stale state.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Set

from aiohttp import WSMsgType, web

from golden_lattice.events import LatticeEvent


_STATIC_DIR = Path(__file__).parent / "static"
_INDEX_PATH = _STATIC_DIR / "index.html"


class DashboardServer:
    """aiohttp-based WebSocket server with event log buffering."""

    def __init__(self) -> None:
        self.app = web.Application()
        self.app.router.add_get("/", self._handle_index)
        self.app.router.add_get("/ws", self._handle_ws)
        self.clients: Set[web.WebSocketResponse] = set()
        self.event_log: list[str] = []

    async def broadcast(self, event: LatticeEvent) -> None:
        """Serialize event to JSON, append to log, send to all clients."""
        payload = event.model_dump_json()
        self.event_log.append(payload)
        if not self.clients:
            return
        dead: list[web.WebSocketResponse] = []
        for ws in self.clients:
            try:
                await ws.send_str(payload)
            except ConnectionResetError:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    def reset_log(self) -> None:
        """Clear the event log. Use before a fresh replay or live run so
        new clients don't inherit prior session state."""
        self.event_log.clear()

    async def _handle_index(self, request: web.Request) -> web.Response:
        if _INDEX_PATH.exists():
            body = _INDEX_PATH.read_text(encoding="utf-8")
        else:
            body = (
                "<html><body><h1>Golden Lattice</h1>"
                "<p>Dashboard frontend not yet built. "
                "Slice 2 lands the HTML.</p></body></html>"
            )
        return web.Response(text=body, content_type="text/html")

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        # Replay the buffered log before adding to the live broadcast set,
        # so the new client sees prior events in order before live ones.
        for payload in self.event_log:
            await ws.send_str(payload)
        self.clients.add(ws)
        try:
            async for msg in ws:
                # Clients don't need to send messages in v0; just keep the
                # connection open. Echo or ignore.
                if msg.type == WSMsgType.ERROR:
                    break
        finally:
            self.clients.discard(ws)
        return ws

    async def run_forever(self, *, host: str = "127.0.0.1", port: int = 8765) -> None:
        """Run the server on the given host/port until cancelled. Used by
        the CLI entry points (live integration and replay-mode)."""
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            await runner.cleanup()
