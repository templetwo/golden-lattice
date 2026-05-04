"""LatticeConfig — configuration for an orchestrator run.

Pydantic frozen model. Schema-as-constitution discipline applied to
configuration: invalid configurations refused at construction. The same
substrate-style refusal pattern that the rest of the codebase uses.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, model_validator

from golden_lattice.memory_graph.base import (
    DEFAULT_OUTPUT_MODE,
    OutputMode,
)


class LatticeConfig(BaseModel):
    """Configuration for a Lattice session run.

    Frozen and validated. Pass one instance per session to the orchestrator;
    do not mutate after construction.

    Timeouts are seconds. Phase 1 default 90s per ARCHITECTURE.md §5.1.
    Self-reflection timeout is shorter than Phase 1 because reflection
    happens during latency gap on already-completed Phase 1 — it's a
    smaller cognitive task and shouldn't take a full Phase 1 budget.
    """

    model_config = ConfigDict(frozen=True)

    api_key: Optional[str] = None
    timeout_phase_1_seconds: float = 90.0
    timeout_self_reflection_seconds: float = 30.0
    timeout_phase_2_seconds: float = 60.0
    timeout_phase_3_seconds: float = 60.0
    confidence_threshold: float = 0.7
    output_mode: OutputMode = DEFAULT_OUTPUT_MODE

    @model_validator(mode="after")
    def _validate(self) -> "LatticeConfig":
        if self.timeout_phase_1_seconds <= 0:
            raise ValueError("timeout_phase_1_seconds must be positive")
        if self.timeout_self_reflection_seconds <= 0:
            raise ValueError("timeout_self_reflection_seconds must be positive")
        if self.timeout_phase_2_seconds <= 0:
            raise ValueError("timeout_phase_2_seconds must be positive")
        if self.timeout_phase_3_seconds <= 0:
            raise ValueError("timeout_phase_3_seconds must be positive")
        if not 0.0 <= self.confidence_threshold <= 1.0:
            raise ValueError(
                f"confidence_threshold {self.confidence_threshold} outside [0, 1]"
            )
        return self
