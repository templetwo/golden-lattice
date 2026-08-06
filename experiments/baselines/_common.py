"""Shared helpers for baseline SUT adapters."""

from __future__ import annotations

import os
from typing import Any, Mapping, Optional, Sequence

from experiments.baselines.protocol import (
    Availability,
    GroundingMode,
    RunMode,
    SessionResult,
    StepRecord,
    StepStatus,
)


def live_provider_configured() -> tuple[bool, str]:
    """Return (ok, reason). Live runs require an explicit opt-in + API key.

    Honesty gate: presence of a key alone is not enough; operators must set
    GOLDEN_LATTICE_EXPERIMENT_LIVE=1 so accidental live spend cannot happen
    from a dry-run-oriented checkout.
    """
    if os.environ.get("GOLDEN_LATTICE_EXPERIMENT_LIVE", "").strip() not in {
        "1",
        "true",
        "TRUE",
        "yes",
        "YES",
    }:
        return (
            False,
            "live execution not configured: set GOLDEN_LATTICE_EXPERIMENT_LIVE=1 "
            "and provide a provider API key",
        )
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return True, ""
    return (
        False,
        "live execution unavailable: ANTHROPIC_API_KEY not set "
        "(GOLDEN_LATTICE_EXPERIMENT_LIVE is set but no provider key)",
    )


def default_availability(mode: RunMode) -> Availability:
    if mode is RunMode.DRY_RUN:
        return Availability(True, "dry_run planning mode")
    ok, reason = live_provider_configured()
    return Availability(ok, reason)


def grounding_availability(
    mode: RunMode,
    grounding_mode: GroundingMode,
) -> Availability:
    """Apply the provider gate required by the selected grounding mode."""
    availability = default_availability(mode)
    if not availability.available or grounding_mode is GroundingMode.NONE:
        return availability
    if not os.environ.get("TAVILY_API_KEY", "").strip():
        return Availability(
            False,
            "grounded live execution unavailable: TAVILY_API_KEY not set",
        )
    return availability


def planned_session(
    *,
    sut_id: str,
    task_id: str,
    session_id: str,
    prompt_bundles: Sequence[Mapping[str, Any]],
    notes: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> SessionResult:
    steps = [
        StepRecord(
            step_id=str(bundle["step_id"]),
            perturbation_id=str(bundle["perturbation_id"]),
            status=StepStatus.PLANNED.value,
            prompt_bundle=dict(bundle),
            raw_output=None,
            latency_ms=None,
            cost_usd=None,
            reason="dry_run: prompt bundle prepared; no model call",
        )
        for bundle in prompt_bundles
    ]
    return SessionResult(
        session_id=session_id,
        task_id=task_id,
        sut_id=sut_id,
        status=StepStatus.PLANNED.value,
        steps=steps,
        notes=notes,
        metadata=dict(metadata or {}),
    )


def unavailable_session(
    *,
    sut_id: str,
    task_id: str,
    session_id: str,
    prompt_bundles: Sequence[Mapping[str, Any]],
    reason: str,
    notes: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> SessionResult:
    steps = [
        StepRecord(
            step_id=str(bundle["step_id"]),
            perturbation_id=str(bundle["perturbation_id"]),
            status=StepStatus.UNAVAILABLE.value,
            prompt_bundle=dict(bundle),
            raw_output=None,
            latency_ms=None,
            cost_usd=None,
            reason=reason,
        )
        for bundle in prompt_bundles
    ]
    return SessionResult(
        session_id=session_id,
        task_id=task_id,
        sut_id=sut_id,
        status=StepStatus.UNAVAILABLE.value,
        steps=steps,
        unavailable_reason=reason,
        notes=notes,
        metadata=dict(metadata or {}),
    )
