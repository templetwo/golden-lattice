"""Runs the pure frontend logic tests (tests/js) with node, when node exists."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_frontend_logic_with_node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    files = sorted(str(f) for f in (ROOT / "tests" / "js").glob("*.test.mjs"))
    assert files, "no frontend logic tests found"
    result = subprocess.run(
        [node, "--test", *files],
        capture_output=True, text=True, cwd=ROOT, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
