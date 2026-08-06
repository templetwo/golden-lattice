"""Shared protocol boundary for experiment systems under test (SUTs).

Every SUT receives the same four longitudinal steps and returns structured
step records. Runners must never infer commitment states from free-form prose.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional, Protocol, runtime_checkable


class RunMode(str, Enum):
    """Execution mode for a batch or session."""

    DRY_RUN = "dry_run"
    LIVE = "live"


class GroundingMode(str, Enum):
    """Whether a run uses Golden Lattice Phase 0 external retrieval."""

    NONE = "none"
    TAVILY = "tavily"


class StepStatus(str, Enum):
    """Honest step/session status vocabulary.

    planned      — dry-run/planning: prompt bundle prepared; no model call
    completed    — live call succeeded; raw_output present
    unavailable  — live requested but provider/config not available
    skipped      — intentionally not run (batch policy)
    aborted      — mid-sequence failure; later steps not attempted
    error        — step attempted and failed
    """

    PLANNED = "planned"
    COMPLETED = "completed"
    UNAVAILABLE = "unavailable"
    SKIPPED = "skipped"
    ABORTED = "aborted"
    ERROR = "error"


@dataclass(frozen=True)
class Availability:
    available: bool
    reason: str = ""


@dataclass
class StepRecord:
    """One perturbation step outcome for one SUT session.

    commitment_transitions is only populated when the SUT emits *explicit*
    structured transition artifacts. Never fill this by parsing prose.
    """

    step_id: str
    perturbation_id: str
    status: str
    prompt_bundle: dict[str, Any]
    raw_output: Optional[str] = None
    latency_ms: Optional[float] = None
    cost_usd: Optional[float] = None
    reason: Optional[str] = None
    structured: Optional[dict[str, Any]] = None
    commitment_transitions: Optional[list[dict[str, Any]]] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SessionResult:
    session_id: str
    task_id: str
    sut_id: str
    status: str
    steps: list[StepRecord] = field(default_factory=list)
    unavailable_reason: Optional[str] = None
    notes: Optional[str] = None
    # Optional bookkeeping — never used as quality scores.
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "session_id": self.session_id,
            "task_id": self.task_id,
            "sut_id": self.sut_id,
            "status": self.status,
            "steps": [s.to_dict() for s in self.steps],
        }
        if self.unavailable_reason is not None:
            payload["unavailable_reason"] = self.unavailable_reason
        if self.notes is not None:
            payload["notes"] = self.notes
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


@runtime_checkable
class SUT(Protocol):
    """Adapter boundary: same four steps in, structured records out."""

    sut_id: str
    canonical: bool
    optional: bool

    def availability(self, mode: RunMode) -> Availability:
        """Whether live execution can proceed under current environment."""
        ...

    def run_session(
        self,
        task: Mapping[str, Any],
        *,
        mode: RunMode,
        session_id: str,
        prompt_bundles: list[dict[str, Any]],
        grounding_mode: GroundingMode = GroundingMode.NONE,
    ) -> SessionResult:
        """Execute or plan the full perturbation sequence for one task.

        ``prompt_bundles`` is pre-built by the runner (one per step, in order)
        so every SUT sees identical stimuli. SUTs must not rewrite task text.
        """
        ...
