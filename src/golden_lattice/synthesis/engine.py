"""Phase 4 synthesis engine boundary — refusals, exceptions, composition.

This module owns the engine boundary:
  - SynthesisInputError: error type raised when input is unsuitable.
  - validate_session_for_synthesis: precondition check.
  - synthesize: kernel-syscall-shape entry point that composes the four
    rule modules into a complete SynthesisArtifact.

Synthesis is rule-based, not model-based — no LLM calls, deterministic given
inputs, no authority gradient at any layer. See ARCHITECTURE.md §5.4.
"""

from __future__ import annotations

from golden_lattice.memory_graph.base import OutputMode, SynthesisRule
from golden_lattice.memory_graph.schema import Session, SynthesisArtifact
from golden_lattice.synthesis.attribution import render_output
from golden_lattice.synthesis.claim_trace import build_claim_trace
from golden_lattice.synthesis.disagreement import compute_surfaced_disagreements
from golden_lattice.synthesis.elevation import compute_elevations


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


def synthesize(
    session: Session,
    *,
    mode: OutputMode = OutputMode.ANNOTATED,
    confidence_threshold: float,
) -> SynthesisArtifact:
    """Compose Phase 4 synthesis from a Session's Phase 1-3 artifacts.

    The kernel-syscall-shape entry point for the synthesis engine. Pure
    function, deterministic given inputs, no LLM calls.

    Always computes rule outputs internally rather than accepting pre-
    computed inputs. The closure-test guarantee (synthesis engine produces
    artifacts the substrate accepts) depends on a single source of truth
    for rule composition. Allowing pre-computed inputs would invite caching
    shortcuts that could bypass that guarantee.

    Returns SynthesisArtifact, NOT Session. The orchestrator is responsible
    for folding the artifact into a complete Session by setting phase_4.
    Synthesis stays a pure transformation; Session construction stays the
    orchestrator's job.

    Errors propagate. If validate_session_for_synthesis raises, or any rule
    raises (Pydantic ValidationError, NotImplementedError on Phase-2-missing
    confidence lookup, etc.), the exception bubbles. No partial artifacts.

    Mode default: ANNOTATED, per ARCHITECTURE.md §7 — "the annotation is
    the proof we did not flatten." confidence_threshold is required (no
    default) — defaults invisibly shape behavior; required parameters force
    deliberate choice at the call site.
    """
    validate_session_for_synthesis(session)

    claim_trace = build_claim_trace(session)
    elevations = compute_elevations(session)
    surfaced = compute_surfaced_disagreements(
        session, confidence_threshold=confidence_threshold
    )
    output = render_output(
        session,
        mode=mode,
        claim_trace=claim_trace,
        elevations=elevations,
        surfaced_disagreements=surfaced,
    )

    return SynthesisArtifact(
        output=output,
        output_mode=mode,
        claim_trace=claim_trace,
        synthesis_rules_applied=(
            SynthesisRule.IRREDUCIBILITY_PRESERVATION,
            SynthesisRule.AGREEMENT_ELEVATION,
            SynthesisRule.DISAGREEMENT_SURFACING,
            SynthesisRule.ATTRIBUTION_PRESERVATION,
        ),
        elevations=elevations,
        surfaced_disagreements=surfaced,
    )
