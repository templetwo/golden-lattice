"""A corrupt ledger line refuses the run with one clear line — never a traceback.

Blocker from the daily-cli adversarial verify: a truncated tail line (power
loss or kill mid-write) made every later run die with a raw JSONDecodeError.
The failure must stay CLOSED (no spend understated, nothing written) but
become a DailyRefused that names the offending line.
"""

from __future__ import annotations

import pytest

from golden_lattice.evaluation.daily import DailyRefused, month_to_date

GOOD = '{"run_id": "a", "month": "2026-11", "status": "completed", "cost_usd": 1.0}'


def test_truncated_tail_line_refuses_naming_the_line():
    lines = [GOOD, '{"run_id": "trunc", "status": "sta']
    with pytest.raises(DailyRefused) as exc:
        month_to_date(lines, "2026-11")
    assert "line 2" in str(exc.value)
    assert "corrupt" in str(exc.value)


def test_garbage_line_refuses_naming_the_line():
    with pytest.raises(DailyRefused) as exc:
        month_to_date(["not json at all", GOOD], "2026-11")
    assert "line 1" in str(exc.value)


def test_corrupt_line_is_never_skipped_spend_never_understated():
    """The refusal must come even when every parseable line is fine —
    skipping the bad line could hide completed spend."""
    lines = [GOOD, "{", GOOD.replace('"a"', '"b"')]
    with pytest.raises(DailyRefused):
        month_to_date(lines, "2026-11")


def test_clean_ledger_still_sums():
    spent, reserved = month_to_date(
        [GOOD, '{"run_id": "r2", "month": "2026-11", "status": "started", "budget_usd": 5.0}'],
        "2026-11",
    )
    assert spent == 1.0
    assert reserved == 5.0
