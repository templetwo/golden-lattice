"""Orchestrator error types.

Distinct from substrate ValidationError and SynthesisInputError so callers
can catch orchestration-layer failures separately from kernel failures.

Diagnostic context: each error carries enough information to know which
model, which phase, and how late in the protocol the failure happened.
The orchestrator is the layer where the world enters; failures here have
runtime context that pure layers don't have.
"""

from __future__ import annotations

from typing import Optional

from golden_lattice.memory_graph.base import ModelId


class OrchestratorError(Exception):
    """Base for orchestration failures. Distinct from substrate or synthesis errors."""


class OrchestratorTimeoutError(OrchestratorError):
    """A model did not respond within the phase's timeout budget.

    Carries diagnostic context: which model, which phase, the elapsed budget
    that was exceeded, and (when available) the partial state of other
    models in the same phase.
    """

    def __init__(
        self,
        *,
        model: ModelId,
        phase: str,
        timeout_seconds: float,
        completed_models: tuple[ModelId, ...] = (),
        message: Optional[str] = None,
    ) -> None:
        self.model = model
        self.phase = phase
        self.timeout_seconds = timeout_seconds
        self.completed_models = completed_models
        msg = message or (
            f"{model.value} did not complete {phase} within "
            f"{timeout_seconds}s. Completed models: "
            f"{[m.value for m in completed_models]}."
        )
        super().__init__(msg)


class OrchestratorTruncationError(OrchestratorError):
    """A model stopped at its max_tokens cap. Refused before parsing.

    Under V0 this is a hard fail, typed and attributed. The partial tool
    input is never parsed, so a shortened claim list cannot enter a session.
    """

    def __init__(
        self,
        *,
        model: ModelId,
        phase: str,
        max_tokens: int,
        output_tokens: int,
    ) -> None:
        self.model = model
        self.phase = phase
        self.max_tokens = max_tokens
        self.output_tokens = output_tokens
        super().__init__(
            f"{model.value} hit max_tokens={max_tokens} during {phase} "
            f"(output_tokens={output_tokens}); response refused before parsing."
        )


class OrchestratorRefusalError(OrchestratorError):
    """A model's safety classifier declined the request (stop_reason 'refusal').

    Typed so the failure says what happened: the category, and that no output was
    produced. It is not a missing tool block, and it is never retried.
    """

    def __init__(
        self,
        *,
        model: ModelId,
        phase: str,
        category: str | None,
        explanation: str = "",
    ) -> None:
        self.model = model
        self.phase = phase
        self.category = category
        self.explanation = explanation
        super().__init__(
            f"{model.value} refused {phase} (category={category}); no output was produced."
        )


class OrchestratorProviderError(OrchestratorError):
    """A provider (Anthropic) call failed in a way that's not a timeout —
    API error, rate limit, malformed response, etc."""

    def __init__(
        self,
        *,
        model: ModelId,
        phase: str,
        underlying: Exception,
        message: Optional[str] = None,
    ) -> None:
        self.model = model
        self.phase = phase
        self.underlying = underlying
        msg = message or (
            f"Provider error for {model.value} during {phase}: "
            f"{type(underlying).__name__}: {underlying}"
        )
        super().__init__(msg)
