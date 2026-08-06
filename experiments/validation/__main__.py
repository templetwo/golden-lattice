"""CLI entry: python -m experiments.validation"""

from __future__ import annotations

import sys

from experiments.validation.core import main

if __name__ == "__main__":
    sys.exit(main())
