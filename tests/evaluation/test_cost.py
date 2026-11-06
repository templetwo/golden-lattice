"""Cost gate and ledger refusals (eval harness step 7)."""

from __future__ import annotations

import pytest

from golden_lattice.evaluation.cost import (
    PRICE_DATE,
    PRICES,
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
    assert call_cost_usd("claude-not-a-model-9", 1_000_000, 0) == pytest.approx(UNKNOWN_PRICE[0])


def test_haiku_5_5_has_its_own_price_not_the_fallback():
    # A real Haiku phase-1 call: 1447 in, 3603 out, at $0.10 / $0.50 per MTok.
    cost = call_cost_usd("claude-haiku-5-5", 1447, 3603)
    assert cost == pytest.approx(0.0019462, rel=1e-6)
    assert round(cost, 4) == 0.0019
    assert PRICES["claude-haiku-5-5"] == (0.10, 0.50)


def test_price_date_was_bumped_for_haiku_5_5():
    assert PRICE_DATE >= "2026-11-05"


def test_ledger_hard_stops_when_budget_exceeded():
    ledger = SpendLedger(budget_usd=1.0)
    ledger.record({"cost_usd": 0.6})
    assert ledger.can_start(0.3)
    assert not ledger.can_start(0.5)
    with pytest.raises(BudgetExceeded):
        ledger.record({"cost_usd": 0.5})


def test_worst_case_estimate_is_positive_and_grows_with_peers():
    assert 0 < worst_case_session_usd(3) < worst_case_session_usd(4)
