"""The sessions home: one place every CLI and dashboard default resolves to.

$GOLDEN_LATTICE_HOME/sessions when the variable is set, else
~/golden-lattice/sessions. Always absolute, so a ledger, a monthly cap or a
saved answer never depends on the directory a command was started from.
"""

from __future__ import annotations

import os
from pathlib import Path

HOME_ENV = "GOLDEN_LATTICE_HOME"


def sessions_root() -> Path:
    base = os.environ.get(HOME_ENV)
    if base:
        return Path(base).expanduser().absolute() / "sessions"
    return Path.home() / "golden-lattice" / "sessions"
