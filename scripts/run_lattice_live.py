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

from golden_lattice.exchange.tavily_search_client import TavilySearchClient
from golden_lattice.memory_graph.store import JsonFileSessionStore
from golden_lattice.orchestrator import (
    AnthropicClient,
    LatticeConfig,
    run_lattice_session,
    run_lattice_session_async,
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
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Run the web dashboard instead of the terminal TUI. Opens a browser tab and streams events via WebSocket so panels can be scrolled and read in full.",
    )
    parser.add_argument(
        "--dashboard-port",
        type=int,
        default=8765,
        help="Dashboard port when --dashboard is set (default: 8765).",
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

    # --- Phase 0 clients: only run Phase 0 if a Tavily key is available.
    # When TAVILY_API_KEY is absent, the session runs in M1-only mode (no
    # investigation, no shared evidence feed) — the same backward-compat
    # path the orchestrator already enforces when phase_0_client is None.
    phase_0_client = None
    search_client = None
    tavily_key = os.environ.get("TAVILY_API_KEY")
    if not tavily_key and sys.stdin.isatty():
        console.print(
            "\n[dim]TAVILY_API_KEY not set. Phase 0 (Investigation) "
            "requires a Tavily key for search + extract.[/]"
        )
        try:
            entered = getpass.getpass(
                "TAVILY_API_KEY (press Enter to skip Phase 0): "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            entered = ""
        if entered:
            tavily_key = entered
    if tavily_key:
        # AnthropicClient already satisfies Phase0WireClient via
        # submit_investigation_proposal — same client, four protocols.
        phase_0_client = client
        search_client = TavilySearchClient(api_key=tavily_key)
        console.print(
            "[green]Phase 0 enabled[/]  [dim]Tavily search + extract; "
            "temporal grounding in America/New_York; flat cap = 3 per model.[/]"
        )
    else:
        console.print(
            "[dim]Phase 0 skipped — running M1 mode. The lattice will not "
            "investigate before answering.[/]"
        )

    if args.dashboard:
        # Web dashboard: orchestrator and server run in the same asyncio
        # loop. Each event broadcasts to connected browser clients via the
        # DashboardServer. Server stays alive a few seconds after
        # session_completed so the user can read the final state, then exits.
        import asyncio
        import webbrowser
        from aiohttp import web as aioweb
        from golden_lattice.dashboard.server import DashboardServer

        async def _run_with_dashboard():
            server = DashboardServer()
            runner = aioweb.AppRunner(server.app)
            await runner.setup()
            site = aioweb.TCPSite(runner, "127.0.0.1", args.dashboard_port)
            await site.start()
            url = f"http://127.0.0.1:{args.dashboard_port}"
            console.print(f"[green]Dashboard at[/] {url}")
            webbrowser.open(url)
            await asyncio.sleep(1.0)  # let the browser connect first

            pending: list[asyncio.Task] = []

            def callback(event):
                # Fire-and-forget the async broadcast from the sync callback;
                # we collect tasks so we can await them before exit to make
                # sure the browser actually received the final events.
                pending.append(asyncio.create_task(server.broadcast(event)))

            try:
                session = await run_lattice_session_async(
                    prompt,
                    config=config,
                    client=client,
                    progress_callback=callback,
                    phase_0_client=phase_0_client,
                    search_client=search_client,
                )
                await asyncio.gather(*pending, return_exceptions=True)
                console.print(
                    "[dim]session_completed — dashboard stays live for 60s; "
                    "Ctrl-C to exit sooner.[/]"
                )
                await asyncio.sleep(60.0)
                return session
            finally:
                await runner.cleanup()

        try:
            session = asyncio.run(_run_with_dashboard())
        except KeyboardInterrupt:
            console.print("\n[dim]Dashboard stopped.[/]")
            return 130
    elif args.no_tui:
        def callback(event):
            print(
                f"[{event.timestamp_offset_ms:7d}ms] {event.event_type}",
                file=sys.stderr,
                flush=True,
            )
        session = run_lattice_session(
            prompt,
            config=config,
            client=client,
            progress_callback=callback,
            phase_0_client=phase_0_client,
            search_client=search_client,
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
                prompt,
                config=config,
                client=client,
                progress_callback=callback,
                phase_0_client=phase_0_client,
                search_client=search_client,
            )

    # Close the Tavily HTTP client if we opened one.
    if search_client is not None:
        import asyncio as _asyncio
        try:
            _asyncio.run(search_client.aclose())
        except Exception:
            pass

    args.sessions_dir.mkdir(parents=True, exist_ok=True)
    store = JsonFileSessionStore(args.sessions_dir)
    store.save(session)
    persisted_path = args.sessions_dir / f"{session.session_id}{store.SUFFIX}"

    # Print the synthesis output prominently so the answer is visible in
    # the terminal, not just in the persisted JSON. The TUI panels show
    # the structural picture; this shows the actual co-authored response.
    if session.phase_4 is not None:
        console.print()
        console.rule("[bold]Synthesis (Phase 4)[/]", style="bright_black")
        console.print()
        console.print(session.phase_4.output)
        console.print()
        console.rule(style="bright_black")
    console.print(f"\n[green]Session persisted:[/] {persisted_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
