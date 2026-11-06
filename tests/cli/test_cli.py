"""CLI: estimate makes no calls; run needs --yes and a key; show prints an answer.

Every test runs under the autouse fake_home fixture (conftest.py): HOME and
GOLDEN_LATTICE_HOME point into tmp, and no key is in the environment.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from golden_lattice.cli.__main__ import main
from golden_lattice.evaluation.fixtures import FixtureClient
from golden_lattice.home import sessions_root


@pytest.fixture
def no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


def _factory(**kwargs):
    return FixtureClient(seed=0)


def _run(tmp_path, *extra, prompt="design a cache", factory=_factory):
    return main(["run", prompt, "--budget", "15", "--yes",
                 "--sessions-dir", str(tmp_path / "daily"), "--runs-root", str(tmp_path), *extra],
                client_factory=factory)


def test_estimate_prints_both_numbers_and_makes_no_calls(tmp_path, capsys, no_network):
    code = main(["estimate", "design a cache", "--runs-root", str(tmp_path)], client_factory=_factory)
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["worst_case_usd"] > 0
    assert out["expected"]["p50"] is None  # no history in an empty directory


def test_run_without_yes_prints_estimate_and_does_not_spend(tmp_path, capsys, no_network):
    code = main(["run", "design a cache", "--budget", "15", "--sessions-dir", str(tmp_path),
                 "--runs-root", str(tmp_path)], client_factory=_factory)
    assert code == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)["worst_case_usd"] > 0
    assert "re-run with --yes" in captured.err
    assert not (tmp_path / "ledger.jsonl").exists()


def test_run_without_a_key_is_refused(tmp_path, capsys, no_network):
    code = main(["run", "design a cache", "--budget", "15", "--yes",
                 "--sessions-dir", str(tmp_path), "--runs-root", str(tmp_path)])
    assert code == 2
    assert "no API key" in capsys.readouterr().err


def test_run_yes_writes_the_answer_and_show_prints_it(tmp_path, capsys, no_network):
    answer = tmp_path / "answer.md"
    code = _run(tmp_path, "--out", str(answer))
    assert code == 0
    assert answer.read_text(encoding="utf-8").strip()
    session_json = next((tmp_path / "daily").glob("*/session.json"))
    capsys.readouterr()
    assert main(["show", str(session_json)]) == 0
    assert capsys.readouterr().out.strip()


def test_run_refused_when_budget_is_below_worst_case(tmp_path, capsys, no_network):
    code = main(["run", "design a cache", "--budget", "5", "--yes",
                 "--sessions-dir", str(tmp_path), "--runs-root", str(tmp_path)], client_factory=_factory)
    assert code == 2
    assert "refused" in capsys.readouterr().err


# --- Sessions home (item 1) ---

def test_run_defaults_to_the_sessions_home_not_the_cwd(tmp_path, capsys, monkeypatch, no_network):
    cwd = tmp_path / "elsewhere"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    code = main(["run", "design a cache", "--budget", "15", "--yes"], client_factory=_factory)
    assert code == 0
    daily = sessions_root() / "daily"
    assert (daily / "ledger.jsonl").is_file()
    assert next(daily.glob("*/session.json"))
    assert list(cwd.iterdir()) == []


def test_monthly_cap_holds_across_working_directories(tmp_path, capsys, monkeypatch, no_network):
    # Spend recorded in the sessions home is seen from any cwd.
    daily = sessions_root() / "daily"
    daily.mkdir(parents=True)
    from datetime import datetime, timezone
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    (daily / "ledger.jsonl").write_text(json.dumps(
        {"run_id": "daily-x", "month": month, "status": "completed", "cost_usd": 95.0}) + "\n",
        encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    code = main(["run", "design a cache", "--budget", "15", "--yes"], client_factory=_factory)
    assert code == 2
    assert "monthly cap" in capsys.readouterr().err
    assert list(elsewhere.iterdir()) == []


# --- Answer on stdout, receipt on stderr (item 5) ---

def _last_json_object(text: str) -> dict:
    """The receipt: the last top-level JSON object in a stream, ignoring lines after it."""
    start = text.rindex("\n{") + 1 if "\n{" in text else text.index("{")
    return json.JSONDecoder().raw_decode(text[start:])[0]


def test_completed_run_prints_the_answer_to_stdout_and_the_receipt_to_stderr(tmp_path, capsys, no_network):
    assert _run(tmp_path) == 0
    captured = capsys.readouterr()
    run_dir = next((tmp_path / "daily").glob("*/answer.md")).parent
    assert captured.out == (run_dir / "answer.md").read_text(encoding="utf-8").rstrip("\n") + "\n"
    receipt = _last_json_object(captured.err)
    assert receipt["status"] == "completed"
    assert Path(receipt["answer_path"]).is_absolute()
    assert Path(receipt["answer_path"]) == (run_dir / "answer.md").resolve()
    assert Path(receipt["run_dir"]).is_absolute()


def test_json_flag_swaps_the_answer_and_the_receipt(tmp_path, capsys, no_network):
    assert _run(tmp_path, "--json") == 0
    captured = capsys.readouterr()
    receipt = json.loads(captured.out)
    assert receipt["status"] == "completed"
    answer = Path(receipt["answer_path"]).read_text(encoding="utf-8").strip()
    assert answer in captured.err


# --- Progress lines (item 4) ---

def test_run_prints_one_progress_line_per_event_to_stderr(tmp_path, capsys, no_network):
    assert _run(tmp_path) == 0
    err = capsys.readouterr().err
    progress = [l for l in err.splitlines() if l.startswith("[")]
    assert any("session_started" in l for l in progress)
    assert any("phase_1_response_completed" in l and "claude-opus-5" in l for l in progress)
    assert any("session_completed" in l for l in progress)
    assert all(l.split("]")[0].strip("[ ").endswith("s") for l in progress)  # elapsed seconds


# --- Prompt guards (item 6) ---

@pytest.mark.parametrize("prompt", ["", "   ", "\n\t"])
def test_empty_prompt_is_refused_and_creates_nothing(tmp_path, capsys, prompt, no_network):
    code = _run(tmp_path, prompt=prompt)
    assert code == 2
    assert "empty" in capsys.readouterr().err
    assert not (tmp_path / "daily").exists()
    assert not sessions_root().exists()


@pytest.mark.parametrize("prompt", ["notes/question.md", "question.md", "q.txt", "~/missing/prompt"])
def test_a_missing_path_is_refused_not_sent_as_the_question(tmp_path, capsys, monkeypatch, prompt, no_network):
    monkeypatch.chdir(tmp_path)
    called = []

    def factory(**kwargs):
        called.append(True)
        return FixtureClient(seed=0)

    code = _run(tmp_path, prompt=prompt, factory=factory)
    assert code == 2
    assert "no such file" in capsys.readouterr().err
    assert called == []
    assert not (tmp_path / "daily").exists()


def test_an_empty_prompt_file_is_refused(tmp_path, capsys, no_network):
    empty = tmp_path / "empty.md"
    empty.write_text("  \n", encoding="utf-8")
    assert _run(tmp_path, prompt=str(empty)) == 2
    assert "empty" in capsys.readouterr().err


def test_an_existing_prompt_file_is_read(tmp_path, capsys, no_network):
    prompt_file = tmp_path / "q.md"
    prompt_file.write_text("design a cache for a read-heavy API", encoding="utf-8")
    assert _run(tmp_path, prompt=str(prompt_file)) == 0
    session = json.loads(next((tmp_path / "daily").glob("*/session.json")).read_text(encoding="utf-8"))
    assert session["prompt"] == "design a cache for a read-heavy API"


def test_a_question_with_a_slash_in_prose_is_still_a_question(tmp_path, capsys, no_network):
    assert _run(tmp_path, prompt="compare TCP/IP and UDP for a game server") == 0


def test_estimate_refuses_an_empty_prompt(tmp_path, capsys, no_network):
    assert main(["estimate", " ", "--runs-root", str(tmp_path)]) == 2


# --- Key resolution (item 7) ---

def _seen_key(tmp_path, *extra):
    seen = []

    def factory(**kwargs):
        seen.append(kwargs.get("api_key"))
        return FixtureClient(seed=0)

    code = _run(tmp_path, *extra, factory=factory)
    return code, seen


def test_key_from_the_environment_comes_first(tmp_path, capsys, monkeypatch, fake_home, no_network):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "  env-key \n")
    keyfile = tmp_path / "k"
    keyfile.write_text("file-key", encoding="utf-8")
    monkeypatch.setenv("GOLDEN_LATTICE_KEY_FILE", str(keyfile))
    (fake_home / ".lattice_key").write_text("home-key", encoding="utf-8")
    code, seen = _seen_key(tmp_path)
    assert code == 0
    assert seen == ["env-key"]


def test_key_file_env_var_comes_before_the_default_file(tmp_path, capsys, monkeypatch, fake_home, no_network):
    keyfile = tmp_path / "k"
    keyfile.write_text("file-key\n", encoding="utf-8")
    monkeypatch.setenv("GOLDEN_LATTICE_KEY_FILE", str(keyfile))
    (fake_home / ".lattice_key").write_text("home-key", encoding="utf-8")
    code, seen = _seen_key(tmp_path)
    assert code == 0
    assert seen == ["file-key"]


def test_default_key_file_is_in_the_fake_home(tmp_path, capsys, fake_home, no_network):
    (fake_home / ".lattice_key").write_text(" home-key\n", encoding="utf-8")
    code, seen = _seen_key(tmp_path)
    assert code == 0
    assert seen == ["home-key"]


def test_empty_key_is_refused_with_one_line(tmp_path, capsys, monkeypatch, no_network):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
    code, seen = _seen_key(tmp_path)
    assert code == 2
    assert seen == []
    err = capsys.readouterr().err.strip()
    assert "empty" in err
    assert len(err.splitlines()) == 1


def test_empty_default_key_file_is_refused(tmp_path, capsys, fake_home, no_network):
    (fake_home / ".lattice_key").write_text("\n", encoding="utf-8")
    code, seen = _seen_key(tmp_path)
    assert code == 2
    assert seen == []
    assert "empty" in capsys.readouterr().err


def test_missing_key_file_is_one_line_and_no_traceback(tmp_path, capsys, monkeypatch, no_network):
    monkeypatch.setenv("GOLDEN_LATTICE_KEY_FILE", str(tmp_path / "nope"))
    code, seen = _seen_key(tmp_path)
    assert code == 2
    assert seen == []
    err = capsys.readouterr().err.strip()
    assert len(err.splitlines()) == 1
    assert "Traceback" not in err
    assert "nope" in err


def test_unreadable_key_file_is_one_line(tmp_path, capsys, no_network):
    code, seen = _seen_key(tmp_path, "--key-file", str(tmp_path))  # a directory cannot be read
    assert code == 2
    assert seen == []
    assert len(capsys.readouterr().err.strip().splitlines()) == 1


def test_help_documents_the_default_key_file(capsys):
    with pytest.raises(SystemExit):
        main(["run", "--help"])
    out = capsys.readouterr().out
    assert "~/.lattice_key" in out
    assert "GOLDEN_LATTICE_KEY_FILE" in out


# --- Interrupt (item 3, CLI half) ---

def test_ctrl_c_exits_130_and_the_ledger_records_it(tmp_path, capsys, no_network):
    class _CtrlC(FixtureClient):
        async def submit_phase_3_dialogue(self, **kwargs):
            raise KeyboardInterrupt

    code = _run(tmp_path, factory=lambda **kw: _CtrlC(seed=0))
    assert code == 130
    assert "interrupted" in capsys.readouterr().err
    entries = [json.loads(l) for l in (tmp_path / "daily" / "ledger.jsonl").read_text().splitlines()]
    assert entries[-1]["status"] == "interrupted"


# --- Show (item 8) ---

def test_show_accepts_a_session_id_from_the_sessions_home(tmp_path, capsys, monkeypatch, no_network):
    monkeypatch.chdir(tmp_path)
    assert main(["run", "design a cache", "--budget", "15", "--yes"], client_factory=_factory) == 0
    receipt = _last_json_object(capsys.readouterr().err)
    answer = Path(receipt["answer_path"]).read_text(encoding="utf-8").strip()
    assert main(["show", receipt["session_id"]]) == 0
    assert capsys.readouterr().out.strip() == answer
    assert main(["show", receipt["run_dir"]]) == 0
    assert capsys.readouterr().out.strip() == answer


def test_show_finds_a_flat_session_json_by_id(tmp_path, capsys, no_network):
    assert _run(tmp_path) == 0
    capsys.readouterr()
    session_json = next((tmp_path / "daily").glob("*/session.json"))
    session = json.loads(session_json.read_text(encoding="utf-8"))
    flat = sessions_root() / f"{session['session_id']}-flat.session.json"
    flat.parent.mkdir(parents=True, exist_ok=True)
    flat.write_text(session_json.read_text(encoding="utf-8"), encoding="utf-8")
    assert main(["show", f"{session['session_id']}-flat"]) == 0
    assert capsys.readouterr().out.strip() == session["phase_4"]["output"].strip()


def test_show_a_failed_run_prints_its_summary_not_a_traceback(tmp_path, capsys, monkeypatch, no_network):
    from golden_lattice.memory_graph.base import ModelId
    from golden_lattice.orchestrator.errors import OrchestratorProviderError

    class _Fails(FixtureClient):
        async def submit_phase_3_dialogue(self, **kwargs):
            raise OrchestratorProviderError(model=ModelId.HAIKU, phase="phase_3",
                                            underlying=RuntimeError("upstream 529"))

    monkeypatch.chdir(tmp_path)
    assert main(["run", "design a cache", "--budget", "15", "--yes"],
                client_factory=lambda **kw: _Fails(seed=0)) == 1
    receipt = _last_json_object(capsys.readouterr().err)
    code = main(["show", receipt["session_id"]])
    captured = capsys.readouterr()
    assert code == 1
    assert "OrchestratorProviderError" in captured.out
    assert "phase_3" in captured.out
    assert "Traceback" not in captured.out + captured.err


def test_show_unknown_id_is_one_line_exit_2(tmp_path, capsys):
    assert main(["show", "daily-nope"]) == 2
    assert len(capsys.readouterr().err.strip().splitlines()) == 1


# --- Dashboard subcommand (C2) ---

def test_dashboard_subcommand_serves_the_sessions_home(monkeypatch):
    import golden_lattice.dashboard.__main__ as dash

    calls = []

    def fake_serve_record(sessions_dir, port, open_browser):
        calls.append((sessions_dir, port, open_browser))
        return 0

    monkeypatch.setattr(dash, "serve_record", fake_serve_record, raising=False)
    assert main(["dashboard", "--port", "9123", "--no-open"]) == 0
    assert calls == [(sessions_root(), 9123, False)]
    assert main(["dashboard"]) == 0
    assert calls[-1][1:] == (8765, True)
