"""Executable validation for Golden Lattice experiment + constitutional surfaces.

Phase 2 Task 10 surface. Reusable library + CLI:

    python -m experiments.validation
    python -m experiments.validation --run-json path/to/run.json

Checks:
  1. Corpus task IDs/fields/step ordering/perturbation IDs + reversal/removal coverage
  2. Baseline SUT registry completeness and canonical/non-canonical metadata
  3. Report/run JSON structural integrity
  4. Four-seat roster consistency across code/docs (no stale triadic-default claims)
  5. Constitutional invariants (no hidden judge, attribution trace, explicit transitions)

Does not network. Does not depend on /tmp-generated artifacts.
"""

from __future__ import annotations

from experiments.validation.core import (
    Finding,
    ValidationReport,
    main,
    validate_all,
    validate_baseline_registry,
    validate_constitutional_invariants,
    validate_corpus,
    validate_roster_consistency,
    validate_run_json,
)

__all__ = [
    "Finding",
    "ValidationReport",
    "main",
    "validate_all",
    "validate_baseline_registry",
    "validate_constitutional_invariants",
    "validate_corpus",
    "validate_roster_consistency",
    "validate_run_json",
]
