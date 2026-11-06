"""CLI entry for the dashboard.

Subcommands:
  serve                — serve the Record (every session in the sessions home,
                         flat files and daily runs) until Ctrl-C.
  replay <session_id>  — open a browser, walk a persisted Session, broadcast
                         events to connected clients at lived-time pace.

Live mode (driven by the orchestrator's progress_callback) is wired through
run_lattice_live.py's --dashboard flag, not this module.

Usage:
  python -m golden_lattice.dashboard serve
  python -m golden_lattice.dashboard serve --sessions-dir sessions --port 8765 --no-open
  python -m golden_lattice.dashboard replay session_20260516_190624_77b1da30
  python -m golden_lattice.dashboard replay <session_id> --speed 5
  python -m golden_lattice.dashboard replay <session_id> --port 8765
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import webbrowser
from pathlib import Path

from aiohttp import web

from golden_lattice.dashboard.replay_bridge import replay_session_to_server
from golden_lattice.dashboard.server import DashboardServer
from golden_lattice.memory_graph.store import JsonFileSessionStore


def _default_sessions_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "sessions"


HOME_ENV = "GOLDEN_LATTICE_HOME"
DEFAULT_PORT = 8765


def default_sessions_home() -> Path:
    """The sessions home, by the same rule as golden_lattice.home.sessions_root:
    $GOLDEN_LATTICE_HOME/sessions when set, else ~/golden-lattice/sessions."""
    base = os.environ.get(HOME_ENV)
    if base:
        return Path(base).expanduser().absolute() / "sessions"
    return Path.home() / "golden-lattice" / "sessions"


async def serve_record_async(
    sessions_dir: Path,
    port: int,
    open_browser: bool,
    *,
    ready: "asyncio.Future[int] | None" = None,
    stop: asyncio.Event | None = None,
) -> int:
    """Serve the dashboard with the Record as its landing page.

    Runs until `stop` is set (tests) or the task is cancelled / interrupted.
    `ready`, when given, receives the bound port once the site is up (port 0
    picks a free one).
    """
    server = DashboardServer(sessions_dir=Path(sessions_dir))
    runner = web.AppRunner(server.app)
    await runner.setup()
    try:
        site = web.TCPSite(runner, "127.0.0.1", port)
        await site.start()
        actual_port = site._server.sockets[0].getsockname()[1]
        url = f"http://127.0.0.1:{actual_port}/"
        print(f"Record at {url} (sessions: {sessions_dir}); Ctrl-C to stop.")
        if open_browser:
            webbrowser.open(url)
        if ready is not None and not ready.done():
            ready.set_result(actual_port)
        if stop is None:
            while True:
                await asyncio.sleep(3600)
        await stop.wait()
    finally:
        await runner.cleanup()
    return 0


def serve_record(sessions_dir: Path, port: int, open_browser: bool) -> int:
    """Serve the Record over `sessions_dir` until Ctrl-C. 130 on Ctrl-C."""
    try:
        return asyncio.run(serve_record_async(Path(sessions_dir), port, open_browser))
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
        return 130


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

    serve = sub.add_parser("serve", help="Serve the Record over every session in the sessions home.")
    serve.add_argument("--sessions-dir", type=Path, default=None,
                       help="Default: $GOLDEN_LATTICE_HOME/sessions, else ~/golden-lattice/sessions.")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve.add_argument("--no-open", action="store_true",
                       help="Do not auto-open browser; print URL instead.")

    replay = sub.add_parser("replay", help="Replay a persisted session in the dashboard.")
    replay.add_argument("session_id", help="Session id (no .session.json suffix).")
    replay.add_argument("--sessions-dir", type=Path, default=_default_sessions_dir())
    replay.add_argument("--port", type=int, default=8765)
    replay.add_argument("--speed", type=float, default=1.0,
                        help="Replay speed multiplier (1.0 = lived time, 10 = 10x).")
    replay.add_argument("--no-open", action="store_true",
                        help="Do not auto-open browser; print URL instead.")

    args = parser.parse_args(argv)
    if args.cmd == "serve":
        sessions_dir = args.sessions_dir if args.sessions_dir is not None else default_sessions_home()
        return serve_record(sessions_dir, args.port, not args.no_open)
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
