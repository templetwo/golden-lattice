"""Interactive prompt helpers for live-run scripts.

Factored out so the persistent-mode loop's quit/EOF/re-prompt logic is
testable without standing up the orchestrator or the TUI.
"""

from __future__ import annotations

from rich.console import Console


QUIT_TOKENS: frozenset[str] = frozenset({"q", "quit", "exit", ":q"})


def get_next_prompt(console: Console) -> str | None:
    """Block until the user types a non-whitespace prompt. None means quit.

    Quit signals: EOF (Ctrl-D), KeyboardInterrupt (Ctrl-C), or any of
    QUIT_TOKENS (case-insensitive, surrounding whitespace ignored).
    Whitespace-only input re-prompts.

    The raw input text is returned unmodified (not stripped), so any
    intentional leading/trailing whitespace the user typed is preserved.
    """
    while True:
        console.print(
            "\n[bold]Question for the lattice[/]  [dim](single line; "
            "blank repeats; 'q' or Ctrl-D to quit)[/]"
        )
        try:
            text = input("> ")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]exiting loop.[/]")
            return None
        stripped = text.strip()
        if not stripped:
            continue
        if stripped.lower() in QUIT_TOKENS:
            console.print("[dim]exiting loop.[/]")
            return None
        return text
