"""CLI entry: `python -m golden_lattice.tui <session_id> [--speed N] [--snapshot]`.

Loads a persisted Session via JsonFileSessionStore, walks it through the
replay event emitter, and drives the renderer at the requested speed.

speed=1.0 plays at lived time (the real Phase 1 latency as recorded).
speed=10.0 fast-forwards for iteration. --snapshot bypasses Live entirely and
prints one fully-composed final frame to stdout.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console

from golden_lattice.memory_graph.store import JsonFileSessionStore
from golden_lattice.replay import replay_session_events
from golden_lattice.tui.renderer import play_events


def _default_sessions_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "sessions"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="golden_lattice.tui")
    parser.add_argument(
        "session_id",
        help="Session id (without .session.json suffix) to replay.",
    )
    parser.add_argument(
        "--sessions-dir",
        type=Path,
        default=_default_sessions_dir(),
        help="Directory holding persisted sessions (default: repo sessions/).",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="Replay speed multiplier (1.0 = lived time, 10.0 = 10x faster).",
    )
    parser.add_argument(
        "--snapshot",
        action="store_true",
        help="Skip the Live loop; print one fully-composed final frame and exit.",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=None,
        help="Force console width (default: detect from terminal). Useful with --snapshot.",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=None,
        help="Force console height (default: detect from terminal). Useful with --snapshot.",
    )
    args = parser.parse_args(argv)

    if not args.sessions_dir.exists():
        print(f"sessions dir not found: {args.sessions_dir}", file=sys.stderr)
        return 1

    store = JsonFileSessionStore(args.sessions_dir)
    session = store.load(args.session_id)
    events = list(replay_session_events(session))

    console_kwargs = {}
    if args.width is not None:
        console_kwargs["width"] = args.width
    if args.height is not None:
        console_kwargs["height"] = args.height
    console = Console(**console_kwargs)
    play_events(events, speed=args.speed, snapshot=args.snapshot, console=console)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
