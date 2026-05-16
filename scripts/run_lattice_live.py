#!/usr/bin/env python3
"""Live Lattice session with streaming TUI.

Runs a real Anthropic-API session and renders the same five-panel TUI the
replay emitter drives, sourced from the orchestrator's progress_callback
instead of a persisted file. Identical renderer, identical event protocol,
two sources.

Launch interactively — the script prompts for an API key (hidden input via
getpass) if ANTHROPIC_API_KEY is not already in the environment, and for a
question if no prompt was passed as argv/file/stdin.

Usage:
  python scripts/run_lattice_live.py
      → prompts for API key (if not in env) and question, then runs.
  python scripts/run_lattice_live.py "your prompt here"
      → uses the provided question; prompts for API key only if needed.
  python scripts/run_lattice_live.py --prompt-file path.txt
      → reads question from a file.
  ANTHROPIC_API_KEY=sk-... echo "prompt" | python scripts/run_lattice_live.py
      → fully non-interactive; reads everything from env and pipe.

Flags:
  --no-tui    Stream event-type lines to stderr instead of opening the TUI.
              Useful for headless runs or when piping the persisted-path output.
  --sessions-dir DIR  Override where the resulting session is saved.
"""

import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from rich.console import Console
from rich.live import Live

from golden_lattice.memory_graph.store import JsonFileSessionStore
from golden_lattice.orchestrator import (
    AnthropicClient,
    LatticeConfig,
    run_lattice_session,
)
from golden_lattice.tui.renderer import build_layout
from golden_lattice.tui.state import TuiState, apply_event


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="run_lattice_live",
        description="Run a live Lattice session with the streaming TUI.",
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        help="Prompt text. Omit to read from stdin or use --prompt-file.",
    )
    parser.add_argument(
        "--prompt-file",
        type=Path,
        default=None,
        help="Read prompt from a file path instead of argv/stdin.",
    )
    parser.add_argument(
        "--sessions-dir",
        type=Path,
        default=Path(__file__).parent.parent / "sessions",
        help="Where to save the persisted session JSON.",
    )
    parser.add_argument(
        "--no-tui",
        action="store_true",
        help="Stream event-type lines to stderr instead of rendering the TUI.",
    )
    args = parser.parse_args(argv)
    console = Console()

    # --- API key acquisition --------------------------------------------
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        if not sys.stdin.isatty():
            console.print(
                "[red]ERROR:[/] ANTHROPIC_API_KEY not set and stdin is not a "
                "terminal. Set the env var or run interactively.",
            )
            return 1
        console.print(
            "[bold]Golden Lattice[/]  [dim]live session[/]\n"
            "[dim]Three Claudes on one question. Five panels. The architecture "
            "rendered as it happens.[/]\n"
        )
        try:
            api_key = getpass.getpass("ANTHROPIC_API_KEY: ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]cancelled.[/]")
            return 130
        if not api_key:
            console.print("[red]ERROR:[/] empty API key.")
            return 1

    # --- Prompt acquisition --------------------------------------------
    if args.prompt_file is not None:
        prompt = args.prompt_file.read_text(encoding="utf-8")
    elif args.prompt is not None:
        prompt = args.prompt
    elif sys.stdin.isatty():
        console.print(
            "\n[bold]Question for the lattice[/]  [dim](single line; "
            "use --prompt-file for multi-line)[/]"
        )
        try:
            prompt = input("> ")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]cancelled.[/]")
            return 130
    else:
        prompt = sys.stdin.read()
    if not prompt.strip():
        console.print("[red]ERROR:[/] empty prompt.")
        return 1

    config = LatticeConfig(
        api_key=api_key,
        timeout_phase_1_seconds=120.0,
        timeout_self_reflection_seconds=60.0,
        timeout_phase_2_seconds=120.0,
        timeout_phase_3_seconds=120.0,
    )
    client = AnthropicClient(api_key=api_key)

    if args.no_tui:
        def callback(event):
            print(
                f"[{event.timestamp_offset_ms:7d}ms] {event.event_type}",
                file=sys.stderr,
                flush=True,
            )
        session = run_lattice_session(
            prompt, config=config, client=client, progress_callback=callback
        )
    else:
        state = TuiState()
        with Live(
            build_layout(state),
            console=console,
            refresh_per_second=15,
            screen=False,
        ) as live:
            def callback(event):
                apply_event(state, event)
                live.update(build_layout(state))
            session = run_lattice_session(
                prompt, config=config, client=client, progress_callback=callback
            )

    args.sessions_dir.mkdir(parents=True, exist_ok=True)
    store = JsonFileSessionStore(args.sessions_dir)
    store.save(session)
    persisted_path = args.sessions_dir / f"{session.session_id}{store.SUFFIX}"
    console.print(f"\n[green]Session persisted:[/] {persisted_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
