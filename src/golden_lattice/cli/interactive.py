"""Interactive prompt helpers for live-run scripts.

Factored out so the persistent-mode loop's quit/EOF/re-prompt logic is
testable without standing up the orchestrator or the TUI.
"""

from __future__ import annotations

from rich.console import Console


QUIT_TOKENS: frozenset[str] = frozenset({"q", "quit", "exit", ":q"})


class PromptSourceError(ValueError):
    """The live runner received an empty one-shot prompt."""


def resolve_prompt_source(
    *,
    argv_prompt: str | None,
    prompt_file_text: str | None,
    stdin_is_tty: bool,
    stdin_text: str,
    dashboard: bool,
) -> tuple[str | None, bool]:
    """Decide the initial prompt and whether the runner is persistent.

    One-shot sources (file, argv, non-TTY stdin) take precedence except
    for ``--dashboard``: the browser is the prompt source, so empty
    non-TTY stdin must not be treated as a failed one-shot.
    """
    initial_prompt: str | None = None
    if prompt_file_text is not None:
        initial_prompt = prompt_file_text
    elif argv_prompt is not None:
        initial_prompt = argv_prompt
    elif dashboard:
        initial_prompt = None
    elif not stdin_is_tty:
        initial_prompt = stdin_text
    if initial_prompt is not None and not initial_prompt.strip():
        raise PromptSourceError("empty prompt")
    return initial_prompt, initial_prompt is None


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
