"""Tests for the interactive prompt helper used by the live-run script.

The helper is the unit-testable piece of the persistent-mode loop in
scripts/run_lattice_live.py — quit-detection, EOF/SIGINT handling, and
re-prompt-on-blank behavior, factored out so it can be exercised without
standing up the orchestrator.
"""

from __future__ import annotations

import builtins
import io
from unittest.mock import patch

from rich.console import Console

from golden_lattice.cli.interactive import (
    QUIT_TOKENS,
    PromptSourceError,
    get_next_prompt,
    resolve_prompt_source,
)


def _silent_console() -> Console:
    """Console writing to a string buffer so test output stays clean."""
    return Console(file=io.StringIO(), force_terminal=False, width=80)


def test_returns_user_text_unchanged():
    with patch.object(builtins, "input", return_value="what is consciousness"):
        result = get_next_prompt(_silent_console())
    assert result == "what is consciousness"


def test_quit_tokens_return_none_lowercase():
    for token in QUIT_TOKENS:
        with patch.object(builtins, "input", return_value=token):
            assert get_next_prompt(_silent_console()) is None, token


def test_quit_tokens_return_none_uppercase():
    for token in QUIT_TOKENS:
        with patch.object(builtins, "input", return_value=token.upper()):
            assert get_next_prompt(_silent_console()) is None, token


def test_quit_tokens_return_none_with_surrounding_whitespace():
    for token in QUIT_TOKENS:
        with patch.object(builtins, "input", return_value=f"  {token}  "):
            assert get_next_prompt(_silent_console()) is None, token


def test_eof_returns_none():
    with patch.object(builtins, "input", side_effect=EOFError):
        assert get_next_prompt(_silent_console()) is None


def test_keyboard_interrupt_returns_none():
    with patch.object(builtins, "input", side_effect=KeyboardInterrupt):
        assert get_next_prompt(_silent_console()) is None


def test_blank_input_reprompts_until_real_text():
    inputs = iter(["", "  ", "\t\t", "the real question"])
    with patch.object(builtins, "input", side_effect=lambda *_: next(inputs)):
        result = get_next_prompt(_silent_console())
    assert result == "the real question"


def test_dashboard_without_argv_prompt_stays_persistent_on_empty_nontty_stdin():
    """Background/non-TTY launch of --dashboard must not treat empty stdin
    as a one-shot empty prompt. The browser is the prompt source."""
    prompt, interactive = resolve_prompt_source(
        argv_prompt=None,
        prompt_file_text=None,
        stdin_is_tty=False,
        stdin_text="",
        dashboard=True,
    )
    assert prompt is None
    assert interactive is True


def test_nontty_empty_stdin_without_dashboard_is_empty_prompt_error():
    try:
        resolve_prompt_source(
            argv_prompt=None,
            prompt_file_text=None,
            stdin_is_tty=False,
            stdin_text="",
            dashboard=False,
        )
    except PromptSourceError as exc:
        assert "empty prompt" in str(exc)
    else:
        raise AssertionError("expected PromptSourceError")


def test_dashboard_honors_explicit_one_shot_prompt():
    prompt, interactive = resolve_prompt_source(
        argv_prompt="design a cache",
        prompt_file_text=None,
        stdin_is_tty=False,
        stdin_text="",
        dashboard=True,
    )
    assert prompt == "design a cache"
    assert interactive is False
