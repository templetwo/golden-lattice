"""M1 CLI: estimate makes no calls; run needs --yes and a key; show prints an answer."""

from __future__ import annotations

import json
import socket

import pytest

from golden_lattice.cli.__main__ import main
from golden_lattice.evaluation.fixtures import FixtureClient


@pytest.fixture
def no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


def _factory(**kwargs):
    return FixtureClient(seed=0)


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
    assert "re-run with --yes" in capsys.readouterr().out
    assert not (tmp_path / "ledger.jsonl").exists()


def test_run_without_a_key_is_refused(tmp_path, capsys, monkeypatch, no_network):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    code = main(["run", "design a cache", "--budget", "15", "--yes",
                 "--sessions-dir", str(tmp_path), "--runs-root", str(tmp_path)])
    assert code == 2
    assert "no API key" in capsys.readouterr().out


def test_run_yes_writes_the_answer_and_show_prints_it(tmp_path, capsys, no_network):
    answer = tmp_path / "answer.md"
    code = main(["run", "design a cache", "--budget", "15", "--yes",
                 "--sessions-dir", str(tmp_path / "daily"), "--runs-root", str(tmp_path),
                 "--out", str(answer)], client_factory=_factory)
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
    assert "refused" in capsys.readouterr().out
