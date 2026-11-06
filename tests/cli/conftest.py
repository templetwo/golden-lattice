"""Every CLI test runs against a fake home: no real ~/.lattice_key, no real sessions."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def fake_home(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GOLDEN_LATTICE_HOME", str(home / "gl"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GOLDEN_LATTICE_KEY_FILE", raising=False)
    return home
