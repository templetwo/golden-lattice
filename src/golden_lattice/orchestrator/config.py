"""LatticeConfig — configuration for an orchestrator run.

Pydantic frozen model. Schema-as-constitution discipline applied to
configuration: invalid configurations refused at construction. The same
substrate-style refusal pattern that the rest of the codebase uses.

Seat identity vs provider endpoint
----------------------------------
``ModelId`` is the protocol seat / attribution identity (who authored a
claim in the lattice record). ``seat_endpoints`` maps each seat to the
provider model string actually sent to the API. These are deliberately
separate: presence of ``ModelId.FABLE`` in the roster is not a claim that
any particular provider endpoint is live or available.
"""

from __future__ import annotations

from typing import Mapping, Optional, AbstractSet

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from golden_lattice.memory_graph.base import (
    DEFAULT_OUTPUT_MODE,
    ModelId,
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

    ``seat_endpoints`` (optional): explicit seat → provider-model mapping.
    When omitted, legacy callers get the identity default
    (``endpoint_for(seat) == seat.value``). An explicit map must not contain
    empty or duplicate endpoint strings; coverage of invited seats is checked
    by ``validate_provider_capabilities`` against the session roster.
    """

    model_config = ConfigDict(frozen=True)

    api_key: Optional[str] = None
    timeout_phase_1_seconds: float = 90.0
    timeout_self_reflection_seconds: float = 30.0
    timeout_phase_2_seconds: float = 60.0
    timeout_phase_3_seconds: float = 60.0
    confidence_threshold: float = 0.7
    output_mode: OutputMode = DEFAULT_OUTPUT_MODE
    seat_endpoints: Optional[dict[ModelId, str]] = None

    @field_validator("seat_endpoints", mode="before")
    @classmethod
    def _coerce_seat_endpoints(
        cls, value: object
    ) -> Optional[dict[ModelId, str]]:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise ValueError("seat_endpoints must be a mapping of ModelId → str")
        # Materialize as a plain dict so frozen config is hash-stable / copyable.
        return {ModelId(k) if not isinstance(k, ModelId) else k: v for k, v in value.items()}

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
        if self.seat_endpoints is not None:
            cleaned: dict[ModelId, str] = {}
            seen_endpoints: dict[str, ModelId] = {}
            for seat, endpoint in self.seat_endpoints.items():
                if not isinstance(endpoint, str) or not endpoint.strip():
                    raise ValueError(
                        f"seat_endpoints[{seat.name}] has empty endpoint assignment"
                    )
                normalized = endpoint.strip()
                if normalized in seen_endpoints:
                    other = seen_endpoints[normalized]
                    raise ValueError(
                        f"duplicate endpoint assignment {normalized!r} "
                        f"for seats {other.name} and {seat.name}"
                    )
                seen_endpoints[normalized] = seat
                cleaned[seat] = normalized
            # Store stripped endpoints (model is frozen — use object.__setattr__).
            object.__setattr__(self, "seat_endpoints", cleaned)
        return self

    def endpoint_for(self, seat: ModelId) -> str:
        """Resolve the provider model string for a protocol seat.

        Legacy default (no map): identity ``seat.value``.
        Explicit map: the configured endpoint when present, else identity.
        Coverage of invited seats is enforced by
        ``validate_provider_capabilities``, not by this lookup alone.
        """
        if self.seat_endpoints is not None and seat in self.seat_endpoints:
            return self.seat_endpoints[seat]
        return seat.value


def validate_provider_capabilities(
    *,
    invited_models: tuple[ModelId, ...],
    config: LatticeConfig,
    available_endpoints: Optional[AbstractSet[str]] = None,
) -> None:
    """Preflight: seat→endpoint mapping + optional availability check.

    Always validates that an *explicit* ``config.seat_endpoints`` map covers
    every invited seat (partial maps are a configuration error — no silent
    identity fill for omitted seats when the operator provided a map).

    When ``available_endpoints`` is provided, each resolved endpoint must
    appear in that set. Unit tests inject a frozenset; no network I/O is
    performed here. Omitting ``available_endpoints`` skips the live-
    availability half of the check (structural mapping only).

    Raises:
        OrchestratorCapabilityError: missing seat coverage or unavailable
            endpoint, with seat + endpoint diagnostic context.
    """
    from golden_lattice.orchestrator.errors import OrchestratorCapabilityError

    if not invited_models:
        raise ValueError("invited_models must be non-empty")

    if config.seat_endpoints is not None:
        missing = [seat for seat in invited_models if seat not in config.seat_endpoints]
        if missing:
            seat = missing[0]
            raise OrchestratorCapabilityError(
                seat=seat,
                endpoint="",
                reason=(
                    f"seat_endpoints mapping is missing invited seat {seat.name}; "
                    f"explicit maps must cover each invited seat"
                ),
                message=(
                    f"Provider endpoint mapping missing for seat {seat.name}: "
                    f"explicit seat_endpoints must cover each invited seat"
                ),
            )

    for seat in invited_models:
        endpoint = config.endpoint_for(seat)
        if available_endpoints is not None and endpoint not in available_endpoints:
            raise OrchestratorCapabilityError(
                seat=seat,
                endpoint=endpoint,
                reason=(
                    f"endpoint {endpoint!r} is not in the available provider "
                    f"model set; seat identity {seat.name} does not imply "
                    f"provider availability"
                ),
            )