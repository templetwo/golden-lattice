"""Phase 4 synthesis engine — rule-based composition over a Session.

Public API (additions land per-rule commit):
  - build_claim_trace          — Rule 1 (irreducibility preservation)
  - SynthesisInputError        — engine boundary refusal type
  - validate_session_for_synthesis — engine boundary check

Future:
  - compute_elevations               — Rule 2 (agreement elevation)
  - compute_surfaced_disagreements   — Rule 3 (disagreement surfacing)
  - render_output                    — Rule 4 (output mode rendering)
  - synthesize                       — engine composition entry point

STAGING NOTE.
Rules currently compose independently. v0 synthesis is correct but
conservative — disposition decisions in Rule 1 do not yet consume
elevation or disagreement results from Rules 2 and 3. v1 introduces
feedback between rules once all four are operational. Independent rules
first, composition second. The chronicle has the reasoning at hypothesis
layer.
"""

from golden_lattice.synthesis.claim_trace import (
    OMISSION_REASON_PREFIXES,
    build_claim_trace,
)
from golden_lattice.synthesis.elevation import compute_elevations
from golden_lattice.synthesis.engine import (
    SynthesisInputError,
    validate_session_for_synthesis,
)

__all__ = [
    "OMISSION_REASON_PREFIXES",
    "SynthesisInputError",
    "build_claim_trace",
    "compute_elevations",
    "validate_session_for_synthesis",
]
