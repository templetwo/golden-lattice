"""C1: one sessions home for every CLI and dashboard default."""

from __future__ import annotations

from pathlib import Path

from golden_lattice.home import sessions_root


def test_sessions_root_uses_golden_lattice_home_when_set(tmp_path, monkeypatch):
    monkeypatch.setenv("GOLDEN_LATTICE_HOME", str(tmp_path / "gl"))
    assert sessions_root() == tmp_path / "gl" / "sessions"


def test_sessions_root_defaults_under_the_user_home(tmp_path, monkeypatch):
    monkeypatch.delenv("GOLDEN_LATTICE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert sessions_root() == Path(tmp_path) / "golden-lattice" / "sessions"


def test_sessions_root_is_absolute_even_for_a_relative_env_value(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GOLDEN_LATTICE_HOME", "relative-home")
    root = sessions_root()
    assert root.is_absolute()
    assert root == tmp_path.resolve() / "relative-home" / "sessions"
