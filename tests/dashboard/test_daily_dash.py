"""The dashboard over the sessions home the daily CLI writes.

Five audit items, each reproduced before it was fixed:
  1. daily runs live at sessions/daily/<run>/session.json and must be listed
     beside flat *.session.json files (a failed run dir has no session.json);
  2. the Record orders by each session's created_at, newest first, not by id
     ("daily-*" sorts before every "session_2026*" id as a string);
  3. the M3 accountability footer is not answer lines: parsing stops at its
     first header and the rest is carried verbatim as "accountability";
  4. serve_record(sessions_dir, port, open_browser) serves the Record;
  5. the live prompt box is disabled: nothing behind `serve` runs a question.

No test here reads the real sessions home: every directory is a tmp_path.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from aiohttp import ClientSession

from golden_lattice.dashboard import __main__ as dash_main
from golden_lattice.dashboard.normalize import (
    _synthesis_lines,
    load_normalized,
    normalize_session,
)
from golden_lattice.dashboard.record import build_record
from golden_lattice.dashboard.server import DashboardServer
from golden_lattice.memory_graph.store import JsonFileSessionStore

ROOT = Path(__file__).resolve().parents[2]
LIVE_JS = ROOT / "src/golden_lattice/dashboard/static/js/live.js"

FLAT_ID = "session_20260504_120000_abcd1234"
DAILY_ID = "daily-20260514T120000Z-0a1b2c3d"
OLD_DAILY_ID = "daily-20260424T120000Z-99887766"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Any default that reads the sessions home lands in tmp, never ~."""
    monkeypatch.setenv("GOLDEN_LATTICE_HOME", str(tmp_path / "home"))


def _write_daily(root: Path, session) -> Path:
    run_dir = root / "daily" / session.session_id
    run_dir.mkdir(parents=True)
    path = run_dir / "session.json"
    path.write_text(session.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "answer.md").write_text(session.phase_4.output, encoding="utf-8")
    return path


def _write_failed_run(root: Path, run_id: str = "daily-20260515T000000Z-deadbeef") -> None:
    run_dir = root / "daily" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "failed.json").write_text('{"session_id": "%s"}' % run_id, encoding="utf-8")


def _mixed_home(root: Path, make_full_session) -> None:
    """A flat session dated 05-04, a daily run dated 05-14 (newest), a daily
    run dated 04-24 (oldest), a failed run dir and the daily ledger."""
    JsonFileSessionStore(root).save(make_full_session())
    _write_daily(root, make_full_session(session_id=DAILY_ID, created_offset_days=10))
    _write_daily(root, make_full_session(session_id=OLD_DAILY_ID, created_offset_days=-10))
    _write_failed_run(root)
    (root / "daily" / "ledger.jsonl").write_text("", encoding="utf-8")


# --- 1. daily layout ---------------------------------------------------------


def test_daily_run_session_json_is_discovered_beside_flat_files(tmp_path, make_full_session):
    _mixed_home(tmp_path, make_full_session)
    bundle = load_normalized(tmp_path)
    assert sorted(s["id"] for s in bundle.sessions) == sorted([FLAT_ID, DAILY_ID, OLD_DAILY_ID])
    assert bundle.unloadable == []  # the failed run dir is tolerated, not an error


def test_daily_dir_with_only_a_failed_run_gives_an_empty_record(tmp_path):
    _write_failed_run(tmp_path)
    bundle = load_normalized(tmp_path)
    assert bundle.sessions == [] and bundle.unloadable == []


def test_unreadable_daily_session_is_reported_by_its_run_id(tmp_path):
    run_dir = tmp_path / "daily" / "daily-20260516T000000Z-0badf00d"
    run_dir.mkdir(parents=True)
    (run_dir / "session.json").write_text("{}", encoding="utf-8")
    bundle = load_normalized(tmp_path)
    assert bundle.unloadable[0]["id"] == "daily-20260516T000000Z-0badf00d"
    assert "validation error" in bundle.unloadable[0]["error"]


def test_server_lists_daily_runs_and_sees_a_new_one(tmp_path, make_full_session):
    JsonFileSessionStore(tmp_path).save(make_full_session())

    async def _run():
        server = DashboardServer(sessions_dir=tmp_path)
        assert [s["id"] for s in server.bundle().sessions] == [FLAT_ID]
        _write_daily(tmp_path, make_full_session(session_id=DAILY_ID, created_offset_days=10))
        assert [s["id"] for s in server.bundle().sessions] == [DAILY_ID, FLAT_ID]

    asyncio.run(_run())


# --- 2. sort by created_at ---------------------------------------------------


def test_bundle_is_newest_first_by_created_at_not_id(tmp_path, make_full_session):
    _mixed_home(tmp_path, make_full_session)
    assert [s["id"] for s in load_normalized(tmp_path).sessions] == [DAILY_ID, FLAT_ID, OLD_DAILY_ID]


def test_load_normalized_limit_keeps_the_newest(tmp_path, make_full_session):
    _mixed_home(tmp_path, make_full_session)
    assert [s["id"] for s in load_normalized(tmp_path, limit=1).sessions] == [DAILY_ID]


def test_record_orders_runs_flags_and_series_by_created_at(tmp_path, make_full_session):
    _mixed_home(tmp_path, make_full_session)
    sessions = load_normalized(tmp_path).sessions
    record = build_record(list(reversed(sessions)))  # input order must not matter
    assert [r["id"] for r in record["runs"]] == [DAILY_ID, FLAT_ID, OLD_DAILY_ID]
    flag_runs = list(dict.fromkeys(f["run_id"] for f in record["flags"]))
    assert flag_runs == [r for r in [DAILY_ID, FLAT_ID, OLD_DAILY_ID] if r in flag_runs]
    for points in record["series"].values():
        assert [p["run_id"] for p in points] == [OLD_DAILY_ID, FLAT_ID, DAILY_ID]


def test_record_sort_treats_a_naive_timestamp_as_utc():
    from golden_lattice.dashboard.record import created_key

    naive = {"id": "b", "created_at": "2026-05-04T12:00:00"}
    aware = {"id": "a", "created_at": "2026-05-04T13:00:00+00:00"}
    missing = {"id": "z"}
    assert sorted([naive, missing, aware], key=created_key) == [missing, naive, aware]


# --- 3. M3 accountability footer ---------------------------------------------


FOOTERS = {
    "elevated": (
        "--- elevated (1) ---\n"
        "claim_ids: abc, def | converge: t4 (claude-opus-5)"
    ),
    "disagreements": (
        "--- disagreements surfaced (1) ---\n"
        "claim_ids: abc | [H] a note that looks like a seat line"
    ),
    "omitted": (
        "--- omitted: what was dropped and why (2) ---\n"
        "[H] 1a2b3c: restates opus one\n"
        "[S] 4d5e6f: [DISPUTED out of scope]"
    ),
}


def _with_output(session, output: str):
    return session.model_copy(update={"phase_4": session.phase_4.model_copy(update={"output": output})})


@pytest.mark.parametrize("which", sorted(FOOTERS))
def test_synthesis_lines_stop_at_each_accountability_header(which, make_full_session):
    session = make_full_session()
    body = session.phase_4.output
    footer = FOOTERS[which]
    assert _synthesis_lines(body + "\n\n" + footer) == _synthesis_lines(body)
    n = normalize_session(_with_output(session, body + "\n\n" + footer))
    assert n["accountability"] == footer
    assert len(n["synthesis_lines"]) == 5
    assert all(not ln["text"].startswith("---") and "1a2b3c" not in ln["text"] for ln in n["synthesis_lines"])


def test_accountability_carries_every_section_verbatim(make_full_session):
    session = make_full_session()
    footer = "\n\n".join(FOOTERS[k] for k in ("elevated", "disagreements", "omitted")) + "\n"
    n = normalize_session(_with_output(session, session.phase_4.output + "\n\n" + footer))
    assert n["accountability"] == footer
    assert n["stats"] == normalize_session(session)["stats"]


def test_a_header_inside_a_line_does_not_stop_parsing():
    out = "[O] see --- elevated (1) --- in prose\n[S] two"
    assert [ln["text"] for ln in _synthesis_lines(out)] == ["see --- elevated (1) --- in prose", "two"]


def test_footerless_output_is_unchanged(make_full_session):
    n = normalize_session(make_full_session())
    assert n["accountability"] == ""
    assert n["synthesis_lines"] == [
        {"index": 0, "tag": "O", "text": "opus one", "hedge": None, "claim_id": n["synthesis_lines"][0]["claim_id"]},
        {"index": 1, "tag": "O", "text": "opus two", "hedge": None, "claim_id": n["synthesis_lines"][1]["claim_id"]},
        {"index": 2, "tag": "S", "text": "sonnet one",
         "hedge": "[DISPUTED — claude-opus-5: 'opus disagrees with sonnet one'; "
                  "claude-haiku-5-5: 'haiku disagrees with sonnet one']",
         "claim_id": n["synthesis_lines"][2]["claim_id"]},
        {"index": 3, "tag": "H", "text": "haiku one", "hedge": None, "claim_id": n["synthesis_lines"][3]["claim_id"]},
        {"index": 4, "tag": "H", "text": "haiku two", "hedge": None, "claim_id": n["synthesis_lines"][4]["claim_id"]},
    ]
    assert all(ln["claim_id"] for ln in n["synthesis_lines"])


def test_no_phase_4_gives_empty_accountability(make_full_session):
    assert normalize_session(make_full_session(incomplete=True))["accountability"] == ""


# --- 4. serve_record ---------------------------------------------------------


async def _get(port: int, path: str):
    async with ClientSession() as http:
        async with http.get(f"http://127.0.0.1:{port}{path}") as resp:
            ctype = resp.headers.get("Content-Type", "")
            return resp.status, await (resp.json() if "json" in ctype else resp.text())


def test_serve_record_core_serves_record_and_sessions(tmp_path, make_full_session):
    _mixed_home(tmp_path, make_full_session)

    async def _run():
        ready: asyncio.Future = asyncio.get_running_loop().create_future()
        stop = asyncio.Event()
        task = asyncio.create_task(dash_main.serve_record_async(
            tmp_path, 0, False, ready=ready, stop=stop))
        port = await asyncio.wait_for(ready, timeout=5)
        try:
            status, html = await _get(port, "/")
            assert status == 200 and "Golden Lattice" in html and "app.js" in html
            status, data = await _get(port, "/sessions")
            assert status == 200
            assert [r["id"] for r in data["record"]["runs"]] == [DAILY_ID, FLAT_ID, OLD_DAILY_ID]
            status, one = await _get(port, f"/sessions/{DAILY_ID}")
            assert status == 200 and one["id"] == DAILY_ID and one["accountability"] == ""
        finally:
            stop.set()
            assert await asyncio.wait_for(task, timeout=5) == 0

    asyncio.run(_run())


def test_serve_record_opens_the_browser_on_the_record(tmp_path, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(dash_main.webbrowser, "open", opened.append)

    async def _run():
        ready: asyncio.Future = asyncio.get_running_loop().create_future()
        stop = asyncio.Event()
        task = asyncio.create_task(dash_main.serve_record_async(tmp_path, 0, True, ready=ready, stop=stop))
        port = await asyncio.wait_for(ready, timeout=5)
        stop.set()
        await asyncio.wait_for(task, timeout=5)
        return port

    port = asyncio.run(_run())
    assert opened == [f"http://127.0.0.1:{port}/"]


def test_serve_record_ctrl_c_returns_cleanly(tmp_path, monkeypatch):
    async def _interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(dash_main, "serve_record_async", _interrupted)
    assert dash_main.serve_record(tmp_path, 0, False) == 130


def test_serve_subcommand_defaults_to_the_sessions_home(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(dash_main, "serve_record", lambda *a: calls.append(a) or 0)
    assert dash_main.main(["serve", "--no-open"]) == 0
    assert calls == [((tmp_path / "home").absolute() / "sessions", 8765, False)]
    assert dash_main.main(["serve", "--sessions-dir", str(tmp_path), "--port", "9001"]) == 0
    assert calls[-1] == (tmp_path, 9001, True)


def test_serve_default_without_the_env_var_is_under_the_user_home(monkeypatch):
    monkeypatch.delenv("GOLDEN_LATTICE_HOME", raising=False)
    assert dash_main.default_sessions_home() == Path.home() / "golden-lattice" / "sessions"


def test_replay_subcommand_is_still_there():
    with pytest.raises(SystemExit):
        dash_main.main(["replay", "--help"])


# --- 5. the live prompt box ----------------------------------------------------


def test_live_prompt_inputs_are_disabled_and_say_to_use_the_cli():
    src = LIVE_JS.read_text(encoding="utf-8")
    inputs = re.findall(r'<input id="gl-prompt"[^>]*>', src)
    assert len(inputs) == 2
    for tag in inputs:
        assert " disabled" in tag
        assert 'title="${esc(PROMPT_OFF)}"' in tag, tag
    buttons = re.findall(r'<button class="send"[^>]*>', src)
    assert buttons and all(" disabled" in b for b in buttons)
    message = re.search(r"export const PROMPT_OFF = '([^']*)';", src)
    assert message and '"golden-lattice run"' in message.group(1)
    # Nothing re-enables the box once rendered.
    assert not re.search(r"input\.disabled\s*=(?!\s*true\b)", src)


def test_frontend_daily_dash_logic_with_node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    spec = ROOT / "tests" / "dashboard" / "js" / "daily_dash.test.mjs"
    result = subprocess.run([node, "--test", str(spec)], capture_output=True, text=True, cwd=ROOT, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_session_view_renders_accountability_as_one_pre_block_after_the_lines():
    src = (ROOT / "src/golden_lattice/dashboard/static/js/session.js").read_text(encoding="utf-8")
    assert '<pre class="accountability mono">${esc(S.accountability)}</pre>' in src
    assert "S.accountability ?" in src  # absent when the field is ""
    ret = re.search(r"return `\$\{back\}.*`;", src).group(0)
    assert ret.index('<div class="output">${lines}</div>') < ret.index("${footer}")
