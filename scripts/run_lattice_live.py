#!/usr/bin/env python3
"""Live Lattice session with streaming TUI.

Runs a real Anthropic-API session and renders the same five-panel TUI the
replay emitter drives, sourced from the orchestrator's progress_callback
instead of a persisted file. Identical renderer, identical event protocol,
two sources.

Launch interactively — the script prompts for an API key (hidden input via
getpass) if ANTHROPIC_API_KEY is not already in the environment, and for a
question if no prompt was passed as argv/file/stdin. In interactive mode
the script stays alive after the first session ends: keys are captured
once, then a prompt loop runs sessions back-to-back until you type 'q'
or hit Ctrl-D.

Usage:
  python scripts/run_lattice_live.py
      → prompts for API key (if not in env) and enters persistent mode:
        each loop asks for a new question, runs the lattice, prints the
        synthesis, and re-prompts. 'q' / Ctrl-D / Ctrl-C exits.
  python scripts/run_lattice_live.py "your prompt here"
      → one-shot. Uses the provided question; prompts for API key only if
        needed; exits after the session.
  python scripts/run_lattice_live.py --prompt-file path.txt
      → one-shot from file.
  ANTHROPIC_API_KEY=sk-... echo "prompt" | python scripts/run_lattice_live.py
      → one-shot, fully non-interactive; reads everything from env and pipe.

Flags:
  --no-tui    Stream event-type lines to stderr instead of opening the TUI.
              Useful for headless runs or when piping the persisted-path output.
  --dashboard Open the web dashboard in a browser tab and stream events via
              WebSocket. In persistent mode the server stays up across loop
              iterations; the panels reset on each new session_started event.
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

from golden_lattice.cli.interactive import PromptSourceError, get_next_prompt, resolve_prompt_source
from golden_lattice.events import SessionErrorEvent
from golden_lattice.exchange.tavily_search_client import TavilySearchClient
from golden_lattice.memory_graph.schema import Session
from golden_lattice.memory_graph.store import JsonFileSessionStore
from golden_lattice.orchestrator import (
    AnthropicClient,
    LatticeConfig,
    run_lattice_session,
    run_lattice_session_async,
)
from golden_lattice.tui.renderer import build_layout
from golden_lattice.tui.state import TuiState, apply_event


def _persist_and_print(session: Session, sessions_dir: Path, console: Console) -> Path:
    """Save the session to disk and print the Phase 4 synthesis."""
    sessions_dir.mkdir(parents=True, exist_ok=True)
    store = JsonFileSessionStore(sessions_dir)
    store.save(session)
    persisted_path = sessions_dir / f"{session.session_id}{store.SUFFIX}"
    if session.phase_4 is not None:
        console.print()
        console.rule("[bold]Synthesis (Phase 4)[/]", style="bright_black")
        console.print()
        console.print(session.phase_4.output)
        console.print()
        console.rule(style="bright_black")
    console.print(f"\n[green]Session persisted:[/] {persisted_path}")
    return persisted_path


def _run_no_tui(
    *,
    initial_prompt: str | None,
    interactive_mode: bool,
    config: LatticeConfig,
    client: AnthropicClient,
    phase_0_client,
    search_client,
    sessions_dir: Path,
    console: Console,
) -> int:
    """No-TUI rendering: stream event lines to stderr; loop in interactive mode."""

    def callback(event):
        print(
            f"[{event.timestamp_offset_ms:7d}ms] {event.event_type}",
            file=sys.stderr,
            flush=True,
        )

    def run_once(prompt: str) -> Session:
        return run_lattice_session(
            prompt,
            config=config,
            client=client,
            progress_callback=callback,
            phase_0_client=phase_0_client,
            search_client=search_client,
        )

    if not interactive_mode:
        assert initial_prompt is not None
        session = run_once(initial_prompt)
        _persist_and_print(session, sessions_dir, console)
        return 0

    while True:
        prompt = get_next_prompt(console)
        if prompt is None:
            return 0
        session = run_once(prompt)
        _persist_and_print(session, sessions_dir, console)


def _run_tui(
    *,
    initial_prompt: str | None,
    interactive_mode: bool,
    config: LatticeConfig,
    client: AnthropicClient,
    phase_0_client,
    search_client,
    sessions_dir: Path,
    console: Console,
) -> int:
    """Terminal TUI: fresh TuiState + Live context per session; loop in
    interactive mode so the user sees the synthesis printed cleanly
    between runs."""

    def run_once(prompt: str) -> Session:
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
            return run_lattice_session(
                prompt,
                config=config,
                client=client,
                progress_callback=callback,
                phase_0_client=phase_0_client,
                search_client=search_client,
            )

    if not interactive_mode:
        assert initial_prompt is not None
        session = run_once(initial_prompt)
        _persist_and_print(session, sessions_dir, console)
        return 0

    while True:
        prompt = get_next_prompt(console)
        if prompt is None:
            return 0
        session = run_once(prompt)
        _persist_and_print(session, sessions_dir, console)


def _run_dashboard(
    *,
    initial_prompt: str | None,
    interactive_mode: bool,
    dashboard_port: int,
    config: LatticeConfig,
    client: AnthropicClient,
    phase_0_client,
    search_client,
    sessions_dir: Path,
    console: Console,
) -> int:
    """Web dashboard: aiohttp server stays up for the full run. In
    interactive mode the same server services multiple back-to-back
    sessions; reset_log() clears the buffer between them so a late-joining
    client sees the current session, not the prior one. The browser tab's
    own state resets on each session_started (see static/index.html)."""
    import asyncio
    import webbrowser
    from aiohttp import web as aioweb
    from golden_lattice.dashboard.server import DashboardServer

    async def _async_main() -> int:
        server = DashboardServer()
        runner = aioweb.AppRunner(server.app)
        await runner.setup()
        site = aioweb.TCPSite(runner, "127.0.0.1", dashboard_port)
        await site.start()
        url = f"http://127.0.0.1:{dashboard_port}"
        console.print(f"[green]Dashboard at[/] {url}")
        webbrowser.open(url)
        await asyncio.sleep(1.0)  # let the browser connect first

        pending: list[asyncio.Task] = []

        def callback(event):
            pending.append(asyncio.create_task(server.broadcast(event)))

        async def run_once(prompt: str) -> Session | None:
            server.reset_log()
            pending.clear()
            try:
                session = await run_lattice_session_async(
                    prompt,
                    config=config,
                    client=client,
                    progress_callback=callback,
                    phase_0_client=phase_0_client,
                    search_client=search_client,
                )
            except Exception as exc:
                # Keep the WebSocket/dashboard alive after provider failures.
                # Do not expose SDK request headers or credentials in the UI.
                message = str(exc)
                for marker in ("sk-ant-", "x-api-key", "Authorization"):
                    if marker in message:
                        message = message[: message.index(marker)] + "[REDACTED]"
                        break
                callback(
                    SessionErrorEvent(
                        timestamp_offset_ms=0,
                        message=message,
                        phase=getattr(exc, "phase", None),
                        model_id=getattr(exc, "model", None),
                    )
                )
                await asyncio.gather(*pending, return_exceptions=True)
                console.print(f"[red]Live session failed:[/] {message}")
                return None
            await asyncio.gather(*pending, return_exceptions=True)
            return session

        try:
            if not interactive_mode:
                assert initial_prompt is not None
                session = await run_once(initial_prompt)
                if session is not None:
                    _persist_and_print(session, sessions_dir, console)
                console.print(
                    "[dim]session_completed — dashboard stays live for 60s; "
                    "Ctrl-C to exit sooner.[/]"
                )
                await asyncio.sleep(60.0)
                return 0 if session is not None else 1

            console.print(
                "[dim]Persistent mode — submit prompts from the browser tab. "
                "Ctrl-C here (or the 'quit' button in the tab) to exit.[/]"
            )
            while True:
                # Prompts arrive over the WebSocket from the dashboard's
                # input bar; the server pushes them to a queue we await.
                prompt = await server.wait_for_prompt()
                if prompt is None:
                    return 0
                session = await run_once(prompt)
                if session is not None:
                    _persist_and_print(session, sessions_dir, console)
        finally:
            await runner.cleanup()

    try:
        return asyncio.run(_async_main())
    except KeyboardInterrupt:
        console.print("\n[dim]Dashboard stopped.[/]")
        return 130


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

    # --- API key acquisition (once, up front) ----------------------------
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
            "[dim]Four current Claude models on one question. Five panels. The architecture "
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

    # --- Prompt source: one-shot if argv/file, else persistent.
    # --dashboard never consumes stdin: the browser is the prompt source.
    # Non-dashboard non-TTY stdin is still a one-shot pipe.
    prompt_file_text = (
        args.prompt_file.read_text(encoding="utf-8")
        if args.prompt_file is not None
        else None
    )
    stdin_text = "" if sys.stdin.isatty() else sys.stdin.read()
    try:
        initial_prompt, interactive_mode = resolve_prompt_source(
            argv_prompt=args.prompt,
            prompt_file_text=prompt_file_text,
            stdin_is_tty=sys.stdin.isatty(),
            stdin_text=stdin_text,
            dashboard=args.dashboard,
        )
    except PromptSourceError:
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

    if interactive_mode:
        if args.dashboard:
            console.print(
                "\n[bold]Persistent mode[/]  [dim]keys captured; prompts come "
                "from the browser tab. Ctrl-C here to exit.[/]"
            )
        else:
            console.print(
                "\n[bold]Persistent mode[/]  [dim]keys captured; loop until "
                "'q' or Ctrl-D.[/]"
            )

    rc = 0
    try:
        if args.dashboard:
            rc = _run_dashboard(
                initial_prompt=initial_prompt,
                interactive_mode=interactive_mode,
                dashboard_port=args.dashboard_port,
                config=config,
                client=client,
                phase_0_client=phase_0_client,
                search_client=search_client,
                sessions_dir=args.sessions_dir,
                console=console,
            )
        elif args.no_tui:
            rc = _run_no_tui(
                initial_prompt=initial_prompt,
                interactive_mode=interactive_mode,
                config=config,
                client=client,
                phase_0_client=phase_0_client,
                search_client=search_client,
                sessions_dir=args.sessions_dir,
                console=console,
            )
        else:
            rc = _run_tui(
                initial_prompt=initial_prompt,
                interactive_mode=interactive_mode,
                config=config,
                client=client,
                phase_0_client=phase_0_client,
                search_client=search_client,
                sessions_dir=args.sessions_dir,
                console=console,
            )
    finally:
        if search_client is not None:
            import asyncio as _asyncio
            try:
                _asyncio.run(search_client.aclose())
            except Exception:
                pass

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
