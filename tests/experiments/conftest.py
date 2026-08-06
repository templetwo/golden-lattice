"""Ensure the repo root is importable so experiments/ packages resolve."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_root = str(REPO_ROOT)
if _root not in sys.path:
    sys.path.insert(0, _root)
