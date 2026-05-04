"""Phase 4 synthesis engine boundary — refusals, exceptions, composition.

This module owns the engine boundary: error types raised when input is
unsuitable for synthesis, and (in a later commit) the synthesize() function
that composes the four rule modules.

Synthesis is rule-based, not model-based — no LLM calls, deterministic given
inputs, no authority gradient at any layer. See ARCHITECTURE.md §5.4.
"""

from __future__ import annotations

from golden_lattice.memory_graph.schema import Session


class SynthesisInputError(ValueError):
    """Raised when a Session is unsuitable for Phase 4 synthesis.

    Distinct from Pydantic ValidationError so callers can catch synthesis
    boundary failures separately from substrate construction failures.

    Refusal philosophy: substrate construction refuses what's structurally
    malformed (missing Phase 1, asymmetric prompt_hash, dialogue cap
    violations, etc.). The engine boundary refuses what's specific to
    synthesis preconditions and is not already substrate-enforced.

    Legitimate-but-empty cases are NOT refused:
      - phase_3 empty (no dialogue) — synthesis runs, no elevation possible.
      - phase_2 empty (no cross-reading) — synthesis runs.
      - phase_2 has no converge turns — synthesis runs, elevations is empty.
    """


def validate_session_for_synthesis(session: Session) -> None:
    """Refuse malformed-for-synthesis input. Defense in depth at the engine boundary.

    Currently refuses:
      - phase_4 already populated. Synthesize does not overwrite. Construct a
        fresh Session if re-synthesis is needed.
    """
    if session.phase_4 is not None:
        raise SynthesisInputError(
            "Session already has phase_4 populated. Synthesize refuses to "
            "overwrite — construct a fresh Session if re-synthesis is needed."
        )
