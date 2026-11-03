"""Cost gate and ledger refusals (eval harness step 7)."""

from __future__ import annotations

import pytest

from golden_lattice.evaluation.cost import (
    UNKNOWN_PRICE,
    BudgetExceeded,
    SpendGateError,
    SpendLedger,
    call_cost_usd,
    check_run_gate,
    worst_case_session_usd,
)


def test_run_refused_without_budget():
    with pytest.raises(SpendGateError, match="budget_usd is required"):
        check_run_gate(budget_usd=None, approved_by="Anthony")


def test_run_refused_without_approver():
    with pytest.raises(SpendGateError, match="approved_by is required"):
        check_run_gate(budget_usd=10, approved_by="   ")


def test_run_refused_above_ceiling():
    with pytest.raises(SpendGateError, match="exceeds the ceiling"):
        check_run_gate(budget_usd=100.01, approved_by="Anthony")


@pytest.mark.parametrize("bad", [0, -5])
def test_run_refused_for_non_positive_budget(bad):
    with pytest.raises(SpendGateError, match="must be positive"):
        check_run_gate(budget_usd=bad, approved_by="Anthony")


def test_gate_accepts_budget_at_ceiling():
    assert check_run_gate(budget_usd=100, approved_by="Anthony") == 100.0


def test_opus_call_cost_matches_price_table():
    # 1M input at $5 plus 1M output at $25 is $30.
    assert call_cost_usd("claude-opus-5", 1_000_000, 1_000_000) == pytest.approx(30.0)


def test_unknown_model_priced_at_highest_known_rate():
    assert call_cost_usd("claude-haiku-5-5", 1_000_000, 0) == pytest.approx(UNKNOWN_PRICE[0])


def test_ledger_hard_stops_when_budget_exceeded():
    ledger = SpendLedger(budget_usd=1.0)
    ledger.record({"cost_usd": 0.6})
    assert ledger.can_start(0.3)
    assert not ledger.can_start(0.5)
    with pytest.raises(BudgetExceeded):
        ledger.record({"cost_usd": 0.5})


def test_worst_case_estimate_is_positive_and_grows_with_peers():
    assert 0 < worst_case_session_usd(3) < worst_case_session_usd(4)
