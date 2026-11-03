"""Cost gate and ledger for metered runs (eval harness step 7).

A metered run is refused unless it names a budget, a budget within the
ceiling, and an approver. The ledger records measured spend and refuses to
start a session that would exceed the budget. Prices are USD per million
tokens, dated 2026-10-06 from the claude-api skill table. A model with no
confirmed price is priced at the highest known rate, so unknown models
overstate spend. That is deliberate: a cap must not understate.
"""

from __future__ import annotations

from dataclasses import dataclass, field

PRICE_DATE = "2026-10-06"
CEILING_USD = 100.0

# model id -> (input $/MTok, output $/MTok)
PRICES: dict[str, tuple[float, float]] = {
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}
UNKNOWN_PRICE = max(PRICES.values())  # highest known rate, conservative


class SpendGateError(ValueError):
    """A metered run was refused before any call was made."""


class BudgetExceeded(RuntimeError):
    """The ledger would exceed the approved budget."""


def price_for(model: str) -> tuple[float, float]:
    return PRICES.get(model, UNKNOWN_PRICE)


def call_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    in_rate, out_rate = price_for(model)
    return (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000


def check_run_gate(
    *,
    budget_usd: float | None,
    approved_by: str | None,
    ceiling_usd: float = CEILING_USD,
) -> float:
    """Refuse a metered run unless it has a budget, a valid ceiling, and an approver."""
    if budget_usd is None:
        raise SpendGateError("budget_usd is required for a metered run")
    if not approved_by or not approved_by.strip():
        raise SpendGateError("approved_by is required for a metered run")
    if budget_usd <= 0:
        raise SpendGateError("budget_usd must be positive")
    if budget_usd > ceiling_usd:
        raise SpendGateError(f"budget_usd {budget_usd} exceeds the ceiling {ceiling_usd}")
    return float(budget_usd)


def worst_case_session_usd(peers: int, allowance: "TokenAllowance | None" = None) -> float:
    """Conservative pre-run estimate for one Phase 0-off session at N peers.

    Call slots: N Phase 1, N reflections, N(N-1) cross-readings, N taggings,
    N Phase 3. Every call is assumed to hit its phase cap, with 25k input
    tokens each, at the highest known rate. Used before any measured spend.
    """
    from golden_lattice.orchestrator.config import TokenAllowance

    a = allowance or TokenAllowance()
    slots = [
        (peers, a.phase_1_response),
        (peers, a.self_reflection),
        (peers * (peers - 1), a.phase_2_cross_reading),
        (peers, a.phase_2_tagging),
        (peers, a.phase_3_dialogue),
    ]
    calls = sum(n for n, _ in slots)
    out_tokens = sum(n * cap for n, cap in slots)
    return call_cost_usd("unknown", calls * 25_000, out_tokens)


@dataclass
class SpendLedger:
    budget_usd: float
    spent_usd: float = 0.0
    entries: list[dict] = field(default_factory=list)

    @property
    def remaining_usd(self) -> float:
        return self.budget_usd - self.spent_usd

    def can_start(self, estimate_usd: float) -> bool:
        return self.spent_usd + estimate_usd <= self.budget_usd

    def record(self, entry: dict) -> None:
        cost = float(entry["cost_usd"])
        self.entries.append(entry)
        self.spent_usd += cost
        if self.spent_usd > self.budget_usd:
            raise BudgetExceeded(
                f"spent {self.spent_usd:.4f} exceeds budget {self.budget_usd:.2f}"
            )
