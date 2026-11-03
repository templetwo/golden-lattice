"""Near-duplicate collapse for distinct-insight counting (eval harness step 3).

Pure, deterministic, no model calls. An adjudicating model is refused by
design (ARCHITECTURE §5 authority-gradient rule), so duplicate detection is
token-set Jaccard with a fixed threshold.
"""

from __future__ import annotations

import pytest

from golden_lattice.evaluation.insights import (
    cluster_count,
    collapse,
    jaccard,
    tokens,
)


def test_identical_claims_collapse_at_any_band():
    claims = ["Use a write ahead log for durability.", "Use a write-ahead log for durability"]
    assert cluster_count(claims, threshold=0.5) == 1
    assert cluster_count(claims, threshold=0.9) == 1


def test_known_paraphrase_collapses_at_half_but_not_at_eight_tenths():
    a = "Cache results in Redis with a short TTL."
    b = "Cache the results in Redis using a short TTL."
    assert jaccard(tokens(a), tokens(b)) == pytest.approx(0.7)
    assert cluster_count([a, b], threshold=0.5) == 1
    assert cluster_count([a, b], threshold=0.8) == 2


def test_distinct_claims_stay_separate():
    claims = [
        "Shard the ledger by account identifier.",
        "Batch invoice emails nightly to smooth load.",
        "Require two approvals for any refund above a limit.",
    ]
    assert cluster_count(claims, threshold=0.5) == 3


def test_empty_claims_never_collapse_into_each_other():
    # A claim with no tokens is not evidence of a duplicate; keep it distinct.
    assert cluster_count(["", "   ", "!!!"], threshold=0.5) == 3


def test_collapse_groups_in_input_order_and_is_deterministic():
    claims = [
        "Retry idempotent writes only.",
        "Shard the ledger by account.",
        "Retry idempotent writes only, never charges.",
    ]
    first = collapse(claims, threshold=0.5)
    second = collapse(claims, threshold=0.5)
    assert first == second
    assert first == [[0, 2], [1]]


@pytest.mark.parametrize("bad", [0.0, -0.1, 1.01])
def test_threshold_outside_unit_interval_refused(bad):
    with pytest.raises(ValueError, match="threshold"):
        cluster_count(["x"], threshold=bad)


def test_jaccard_of_two_empty_sets_is_zero():
    assert jaccard(frozenset(), frozenset()) == 0.0
