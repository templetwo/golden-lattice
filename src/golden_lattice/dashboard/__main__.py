"""CLI entry for the dashboard.

Subcommands:
  replay <session_id>  — open a browser, walk a persisted Session, broadcast
                         events to connected clients at lived-time pace.

Live mode (driven by the orchestrator's progress_callback) is wired through
run_lattice_live.py's --dashboard flag, not this module.

Usage:
  python -m golden_lattice.dashboard replay session_20260516_190624_77b1da30
  python -m golden_lattice.dashboard replay <session_id> --speed 5
  python -m golden_lattice.dashboard replay <session_id> --port 8765
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import webbrowser
from pathlib import Path

from aiohttp import web

from golden_lattice.dashboard.replay_bridge import replay_session_to_server
from golden_lattice.dashboard.server import DashboardServer
from golden_lattice.memory_graph.store import JsonFileSessionStore


def _default_sessions_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "sessions"


async def _serve_replay(session_id: str, sessions_dir: Path, port: int, speed: float, open_browser: bool) -> int:
    store = JsonFileSessionStore(sessions_dir)
    session = store.load(session_id)
    server = DashboardServer(sessions_dir=sessions_dir)
    runner = web.AppRunner(server.app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    actual_port = site._server.sockets[0].getsockname()[1]
    url = f"http://127.0.0.1:{actual_port}"
    print(f"Dashboard at {url}")
    if open_browser:
        webbrowser.open(url)
    # Tiny delay so the browser has time to connect before events start
    # firing — otherwise it joins the connection mid-replay and reads the
    # backlog from the event_log (which is fine but less satisfying).
    await asyncio.sleep(1.0)
    try:
        await replay_session_to_server(session, server, speed=speed)
        print("Replay complete. Browser session stays live; Ctrl-C to exit.")
        # Hold the server alive so the user can keep reading.
        while True:
            await asyncio.sleep(3600)
    finally:
        await runner.cleanup()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="golden_lattice.dashboard")
    sub = parser.add_subparsers(dest="cmd", required=True)

    replay = sub.add_parser("replay", help="Replay a persisted session in the dashboard.")
    replay.add_argument("session_id", help="Session id (no .session.json suffix).")
    replay.add_argument("--sessions-dir", type=Path, default=_default_sessions_dir())
    replay.add_argument("--port", type=int, default=8765)
    replay.add_argument("--speed", type=float, default=1.0,
                        help="Replay speed multiplier (1.0 = lived time, 10 = 10x).")
    replay.add_argument("--no-open", action="store_true",
                        help="Do not auto-open browser; print URL instead.")

    args = parser.parse_args(argv)
    if args.cmd == "replay":
        try:
            return asyncio.run(
                _serve_replay(
                    args.session_id,
                    args.sessions_dir,
                    args.port,
                    args.speed,
                    open_browser=not args.no_open,
                )
            )
        except KeyboardInterrupt:
            print("\nDashboard stopped.")
            return 130
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
