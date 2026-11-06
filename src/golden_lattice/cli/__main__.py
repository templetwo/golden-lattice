"""golden-lattice: estimate, run, show and browse co-authored answers (M1).

  golden-lattice estimate "prompt or path"          worst case and expected cost; no calls
  golden-lattice run "prompt or path" --budget 15 --yes [--out answer.md] [--json]
  golden-lattice show <session id | run dir | path>  print a saved answer
  golden-lattice dashboard [--port 8765] [--no-open] browse every session in the sessions home

Everything lives under the sessions home: $GOLDEN_LATTICE_HOME/sessions, else
~/golden-lattice/sessions (daily runs and their ledger in daily/, eval runs in eval/).

`run` refuses without --yes. While it runs, one progress line per event goes to
stderr. When it completes, the answer goes to stdout and the JSON receipt to stderr
(--json swaps them). The API key is read from ANTHROPIC_API_KEY, else the file named
by $GOLDEN_LATTICE_KEY_FILE, else ~/.lattice_key (--key-file overrides all three).
Exit codes: 0 completed, 1 failed (salvaged to failed.json), 2 refused or not
confirmed, 130 interrupted (spend still recorded in the ledger).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from golden_lattice.evaluation.cost import SpendGateError
from golden_lattice.evaluation.daily import DEFAULT_MONTHLY_CAP_USD, DailyRefused, run_daily
from golden_lattice.evaluation.estimate import estimate as estimate_run
from golden_lattice.home import sessions_root
from golden_lattice.orchestrator.config import LatticeConfig, TokenAllowance
from golden_lattice.orchestrator.orchestrator import DEFAULT_INVITED_MODELS

DEFAULT_KEY_FILE = "~/.lattice_key"
KEY_FILE_ENV = "GOLDEN_LATTICE_KEY_FILE"
DEFAULT_DASHBOARD_PORT = 8765
_EVENT_MODEL_FIELDS = ("model_id", "reader_model", "tagger_model", "speaker_model")


class Refused(Exception):
    """A one-line refusal: printed to stderr, exit 2, no traceback."""


def _err(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _abs(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


# --- prompt -----------------------------------------------------------------

def _looks_like_path(arg: str) -> bool:
    text = arg.strip()
    if text.lower().endswith((".md", ".txt")):
        return True
    # "compare TCP/IP and UDP" is prose; "notes/q" or "~/q" is a path.
    return "/" in text and not any(ch.isspace() for ch in text)


def _prompt_text(arg: str) -> str:
    if not arg.strip():
        raise Refused("refused: the prompt is empty")
    path = Path(arg.strip()).expanduser()
    if path.is_file():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise Refused(f"refused: cannot read prompt file {path}: {exc}") from None
        if not text.strip():
            raise Refused(f"refused: the prompt file {path} is empty")
        return text
    if _looks_like_path(arg):
        raise Refused(f"refused: no such file: {arg.strip()} (a prompt that looks like a path must exist)")
    return arg


# --- key --------------------------------------------------------------------

def _read_key_file(path: Path, source: str) -> str:
    try:
        key = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        reason = getattr(exc, "strerror", None) or str(exc)
        raise Refused(f"not started: cannot read key file {path} ({source}): {reason}") from None
    if not key:
        raise Refused(f"not started: key file {path} ({source}) is empty")
    return key


def _api_key(key_file: Optional[str]) -> Optional[str]:
    """--key-file, then ANTHROPIC_API_KEY, then $GOLDEN_LATTICE_KEY_FILE, then ~/.lattice_key.

    None means no source exists at all. A source that exists but is empty or
    unreadable is a Refused, never a silent fall-through.
    """
    if key_file:
        return _read_key_file(Path(key_file).expanduser(), "--key-file")
    env_key = os.environ.get("ANTHROPIC_API_KEY")
    if env_key is not None:
        if not env_key.strip():
            raise Refused("not started: ANTHROPIC_API_KEY is set but empty")
        return env_key.strip()
    named = os.environ.get(KEY_FILE_ENV)
    if named:
        return _read_key_file(Path(named).expanduser(), f"${KEY_FILE_ENV}")
    default = Path(DEFAULT_KEY_FILE).expanduser()
    if not default.exists():
        return None
    return _read_key_file(default, "default key file")


# --- progress ---------------------------------------------------------------

def _progress_printer() -> Callable[[Any], None]:
    started = time.monotonic()

    def on_event(event: Any) -> None:
        kind = getattr(event, "event_type", type(event).__name__)
        if kind == "model_stream_delta":
            return  # transport deltas arrive many per second; phases are the signal
        model = next((getattr(event, f) for f in _EVENT_MODEL_FIELDS if getattr(event, f, None)), None)
        label = f" {getattr(model, 'value', model)}" if model is not None else ""
        _err(f"[{time.monotonic() - started:7.1f}s] {kind}{label}")

    return on_event


# --- show -------------------------------------------------------------------

def _failed_summary(failed: dict[str, Any], where: Path) -> str:
    error = failed.get("error") or {}
    status = failed.get("status") or "failed"
    lines = [f"run {failed.get('session_id', where.name)} {status}: "
             f"{error.get('class', 'unknown error')}: {error.get('message', '')}".rstrip()]
    detail = [f"{k}: {error[k]}" for k in ("phase", "model", "category", "explanation") if error.get(k)]
    if detail:
        lines.append("  " + "  ".join(detail))
    lines.append(f"  completed artifacts: {len(failed.get('completed_artifacts') or [])}"
                 f"  spend so far: ${float(failed.get('spend_usd') or 0.0):.4f}")
    lines.append(f"  partial artifacts: {where}")
    return "\n".join(lines)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise Refused(f"cannot read {path}: {exc}") from None


def _locate(target: str, home: Path) -> Path:
    given = Path(target).expanduser()
    if given.exists():
        return given.resolve()
    session_id = target.removesuffix(".session.json")
    for candidate in (home / f"{session_id}.session.json", home / "daily" / session_id):
        if candidate.exists():
            return candidate
    raise Refused(f"no session {target!r} in {home} (looked for {session_id}.session.json "
                  f"and daily/{session_id}/)")


def _show(target: str, home: Path) -> int:
    path = _locate(target, home)
    if path.is_dir():
        if (path / "session.json").is_file():
            path = path / "session.json"
        elif (path / "failed.json").is_file():
            path = path / "failed.json"
        else:
            raise Refused(f"{path} holds no session.json or failed.json (the run may not have finished)")
    elif path.name == "sidecar.json":
        return _show(str(path.parent), home)
    data = _load_json(path)
    phase_4 = data.get("phase_4") if isinstance(data, dict) else None
    if isinstance(phase_4, dict) and "output" in phase_4:
        print(phase_4["output"])
        return 0
    if isinstance(data, dict) and "error" in data and "completed_artifacts" in data:
        print(_failed_summary(data, path.parent))
        return 1
    raise Refused(f"{path} is not a session or a failed run")


# --- parser -----------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="golden-lattice",
        description="Sessions home: $GOLDEN_LATTICE_HOME/sessions, else ~/golden-lattice/sessions.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    est = sub.add_parser("estimate", help="worst case and expected cost; makes no calls")
    est.add_argument("prompt")
    est.add_argument("--runs-root", help="eval runs (default: <sessions home>/eval)")
    est.add_argument("--sessions-dir", help="daily runs (default: <sessions home>/daily)")

    run = sub.add_parser(
        "run", help="run a co-authored session (metered)",
        description=("Run a metered co-authored session. Key: ANTHROPIC_API_KEY, else the file "
                     f"named by ${KEY_FILE_ENV}, else {DEFAULT_KEY_FILE}."),
    )
    run.add_argument("prompt", help="the question, or the path of a file holding it")
    run.add_argument("--budget", type=float, required=True, help="USD cap for this run")
    run.add_argument("--yes", action="store_true", help="confirm after reading the estimate")
    run.add_argument("--out", help="also write the answer to this file")
    run.add_argument("--json", action="store_true",
                     help="print the JSON receipt to stdout and the answer to stderr")
    run.add_argument("--sessions-dir", help="daily runs and their ledger (default: <sessions home>/daily)")
    run.add_argument("--monthly-cap", type=float, default=DEFAULT_MONTHLY_CAP_USD)
    run.add_argument("--runs-root", help="eval runs for the estimate (default: <sessions home>/eval)")
    run.add_argument("--key-file", help=(f"read the API key from this file (overrides ANTHROPIC_API_KEY, "
                                         f"${KEY_FILE_ENV} and the default {DEFAULT_KEY_FILE})"))

    show = sub.add_parser("show", help="print the answer of a saved session, or a failed run's summary")
    show.add_argument("target", help="a session id, a run directory, or a session/failed JSON path")
    show.add_argument("--sessions-home", help="where to look up ids (default: the sessions home)")

    dash = sub.add_parser("dashboard", help="open the dashboard on every session in the sessions home")
    dash.add_argument("--port", type=int, default=DEFAULT_DASHBOARD_PORT)
    dash.add_argument("--no-open", action="store_true", help="print the URL; do not open a browser")
    dash.add_argument("--sessions-dir", help="default: the sessions home")
    return parser


# --- main -------------------------------------------------------------------

def _dashboard(args: argparse.Namespace) -> int:
    sessions_dir = _abs(args.sessions_dir) if args.sessions_dir else sessions_root()
    try:
        from golden_lattice.dashboard.__main__ import serve_record
    except ImportError as exc:
        raise Refused(f"dashboard unavailable: {exc}") from None
    return int(serve_record(sessions_dir, args.port, not args.no_open))


def _run(args: argparse.Namespace, prompt: str, estimate: dict[str, Any],
         client_factory: Optional[Callable[..., Any]], daily_dir: Path) -> int:
    peers = DEFAULT_INVITED_MODELS
    if not args.yes:
        print(json.dumps(estimate, indent=2))
        _err(f"not started: re-run with --yes to spend up to ${args.budget:.2f} on this run")
        return 2
    key = _api_key(args.key_file)
    if key is None and client_factory is None:
        raise Refused(f"not started: no API key; set ANTHROPIC_API_KEY, set ${KEY_FILE_ENV}, "
                      f"or write it to {DEFAULT_KEY_FILE}")
    _err(f"starting: budget ${args.budget:.2f}, worst case ${estimate['worst_case_usd']:.2f}, "
         f"runs in {daily_dir}")
    try:
        result = run_daily(
            prompt,
            budget_usd=args.budget,
            out_dir=daily_dir,
            api_key=key,
            monthly_cap_usd=args.monthly_cap,
            peers=peers,
            config=LatticeConfig(),
            allowance=TokenAllowance(),
            client_factory=client_factory,
            progress_callback=_progress_printer(),
        )
    except (DailyRefused, SpendGateError) as exc:
        raise Refused(f"refused: {exc}") from None
    except KeyboardInterrupt as exc:
        notes = "; ".join(getattr(exc, "__notes__", []) or [])
        _err(f"interrupted: spend so far is recorded in {daily_dir / 'ledger.jsonl'}"
             + (f"; {notes}" if notes else ""))
        return 130

    receipt = json.dumps(result, indent=2)
    if result["status"] == "completed":
        answer = Path(result["answer_path"]).read_text(encoding="utf-8")
        if args.out:
            Path(args.out).expanduser().write_text(answer, encoding="utf-8")
        if args.json:
            print(receipt)
            _err(answer.rstrip("\n"))
        else:
            print(answer.rstrip("\n"))
            _err(receipt)
        return 0
    if args.json:
        print(receipt)
    else:
        _err(receipt)
    _err(f"failed: {result['error']['class']}: partial artifacts in {result['run_dir']}/failed.json")
    return 1


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    client_factory: Optional[Callable[..., Any]] = None,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "dashboard":
            return _dashboard(args)
        if args.command == "show":
            home = _abs(args.sessions_home) if args.sessions_home else sessions_root()
            return _show(args.target, home)

        prompt = _prompt_text(args.prompt)
        home = sessions_root()
        runs_root = _abs(args.runs_root) if args.runs_root else home / "eval"
        daily_dir = _abs(args.sessions_dir) if args.sessions_dir else home / "daily"
        estimate = estimate_run(runs_root, peers=DEFAULT_INVITED_MODELS, allowance=TokenAllowance(),
                                config=LatticeConfig(), daily_dir=daily_dir)
        if args.command == "estimate":
            print(json.dumps(estimate, indent=2))
            return 0
        return _run(args, prompt, estimate, client_factory, daily_dir)
    except Refused as exc:
        _err(str(exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())
