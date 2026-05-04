"""Tests for the synthesis engine boundary — SynthesisInputError refusals."""

from datetime import datetime, timezone

import pytest

from golden_lattice.memory_graph.base import (
    FocusTag,
    ModelId,
    Phase,
    SynthesisRule,
    claim_id_for,
)
from golden_lattice.memory_graph.schema import (
    Claim,
    ClaimTraceEntry,
    IndependentResponse,
    Session,
    SynthesisArtifact,
)
from golden_lattice.synthesis.engine import (
    SynthesisInputError,
    validate_session_for_synthesis,
)


NOW = datetime(2026, 5, 3, 12, 0, 0, tzinfo=timezone.utc)


def _claim(model: ModelId, text: str) -> Claim:
    return Claim(
        claim_id=claim_id_for(model, Phase.INDEPENDENT, text),
        source_model=model,
        source_phase=Phase.INDEPENDENT,
        text=text,
    )


def _response(model: ModelId, claims: tuple[Claim, ...]) -> IndependentResponse:
    return IndependentResponse(
        model_id=model,
        prompt_hash="h",
        response="r",
        focus_tag=FocusTag.CORRECTNESS,
        confidence=0.7,
        claims=claims,
        generation_started_at=NOW,
        generation_completed_at=NOW,
    )


def _minimal_dyad_session(phase_4: SynthesisArtifact | None = None) -> Session:
    o1 = _claim(ModelId.OPUS, "a")
    s1 = _claim(ModelId.SONNET, "b")
    return Session(
        session_id="t",
        prompt="p",
        prompt_hash="h",
        models_invited=(ModelId.OPUS, ModelId.SONNET),
        phase_1={
            ModelId.OPUS: _response(ModelId.OPUS, (o1,)),
            ModelId.SONNET: _response(ModelId.SONNET, (s1,)),
        },
        phase_4=phase_4,
    )


def test_validate_accepts_session_without_phase_4():
    """Legitimate input — no phase_4 yet, ready for synthesis."""
    session = _minimal_dyad_session()
    validate_session_for_synthesis(session)  # no exception


def test_validate_refuses_session_with_phase_4_already_populated():
    o1 = _claim(ModelId.OPUS, "a")
    s1 = _claim(ModelId.SONNET, "b")
    existing_synthesis = SynthesisArtifact(
        output="prior",
        claim_trace=(
            ClaimTraceEntry(claim_id=o1.claim_id, disposition="present"),
            ClaimTraceEntry(claim_id=s1.claim_id, disposition="present"),
        ),
        synthesis_rules_applied=(SynthesisRule.IRREDUCIBILITY_PRESERVATION,),
    )
    session = _minimal_dyad_session(phase_4=existing_synthesis)
    with pytest.raises(SynthesisInputError, match="refuses to overwrite"):
        validate_session_for_synthesis(session)


def test_synthesis_input_error_is_distinct_from_validation_error():
    """Engine boundary failures should be catchable separately from substrate failures."""
    from pydantic import ValidationError

    # SynthesisInputError is its own type, not a Pydantic ValidationError.
    assert not issubclass(SynthesisInputError, ValidationError)
    # But it remains a ValueError so plain ValueError catches still work.
    assert issubclass(SynthesisInputError, ValueError)
