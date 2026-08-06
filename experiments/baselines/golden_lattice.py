"""Golden Lattice SUT — reuses canonical run_lattice_session_async; no synthesis fork.

Each longitudinal step is a fresh lattice session whose prompt is the step's
rendered stimulus plus an explicit prior-step transcript block for continuity.
Phase 4 synthesis remains whatever the product already emits — this adapter
does not reimplement or shadow it.

Live path constructs AnthropicClient + LatticeConfig the same way
scripts/run_lattice_live.py does (without TUI). All steps of one longitudinal
session run inside a single asyncio event loop via run_lattice_session_async
so the shared AsyncAnthropic transport is never rebound across closed loops.
The underlying AsyncAnthropic client is closed in that same loop when the
session finishes (success or mid-sequence error). Dry-run only plans prompts.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import sys
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Optional

from experiments.baselines._common import (
    grounding_availability,
    planned_session,
    unavailable_session,
)
from experiments.baselines.protocol import (
    Availability,
    GroundingMode,
    RunMode,
    SessionResult,
    StepRecord,
    StepStatus,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Async orchestrator entry: (prompt, *, config, client, session_id, ...) -> Session
RunSessionAsync = Callable[..., Awaitable[Any]]


class GoldenLatticeSUT:
    sut_id = "golden_lattice"
    canonical = True
    optional = False

    def availability(
        self,
        mode: RunMode,
        *,
        grounding_mode: GroundingMode = GroundingMode.NONE,
    ) -> Availability:
        return grounding_availability(mode, grounding_mode)

    def run_session(
        self,
        task: Mapping[str, Any],
        *,
        mode: RunMode,
        session_id: str,
        prompt_bundles: list[dict[str, Any]],
        grounding_mode: GroundingMode = GroundingMode.NONE,
    ) -> SessionResult:
        task_id = str(task["id"])
        meta = {
            "baseline": self.sut_id,
            "reuses": "golden_lattice.orchestrator.run_lattice_session_async",
            "synthesis": "canonical_phase_4",
            "continuity": "prior_step_transcript_injected_into_prompt",
            "lifecycle": "single_event_loop_per_longitudinal_session",
            "grounding_mode": grounding_mode.value,
        }
        enriched = [
            {
                **dict(b),
                "lattice": {
                    "entry": "run_lattice_session_async",
                    "note": (
                        "Full lattice pipeline per step inside one event loop; "
                        "prior step outputs appended as transcript context only."
                    ),
                },
            }
            for b in prompt_bundles
        ]

        if mode is RunMode.DRY_RUN:
            return planned_session(
                sut_id=self.sut_id,
                task_id=task_id,
                session_id=session_id,
                prompt_bundles=enriched,
                notes=(
                    "Golden Lattice dry-run: prompt bundles prepared for each "
                    "perturbation step. No orchestrator call; no fabricated synthesis."
                ),
                metadata=meta,
            )

        avail = self.availability(mode, grounding_mode=grounding_mode)
        if not avail.available:
            return unavailable_session(
                sut_id=self.sut_id,
                task_id=task_id,
                session_id=session_id,
                prompt_bundles=enriched,
                reason=avail.reason or "provider unavailable",
                metadata=meta,
            )

        return self._run_live(
            task_id,
            session_id,
            enriched,
            meta,
            grounding_mode=grounding_mode,
        )

    def _run_live(
        self,
        task_id: str,
        session_id: str,
        prompt_bundles: list[dict[str, Any]],
        meta: dict[str, Any],
        *,
        grounding_mode: GroundingMode,
    ) -> SessionResult:
        _ensure_src_on_path()
        try:
            from golden_lattice.orchestrator import (  # type: ignore
                AnthropicClient,
                LatticeConfig,
                run_lattice_session_async,
            )
        except ImportError as exc:
            return unavailable_session(
                sut_id=self.sut_id,
                task_id=task_id,
                session_id=session_id,
                prompt_bundles=prompt_bundles,
                reason=f"golden_lattice import failed: {exc}",
                metadata=meta,
            )

        tavily_client_cls = None
        if grounding_mode is GroundingMode.TAVILY:
            try:
                from golden_lattice.exchange.tavily_search_client import (
                    TavilySearchClient,
                )
            except ImportError as exc:
                return unavailable_session(
                    sut_id=self.sut_id,
                    task_id=task_id,
                    session_id=session_id,
                    prompt_bundles=prompt_bundles,
                    reason=f"tavily search client import failed: {exc}",
                    metadata=meta,
                )
            tavily_client_cls = TavilySearchClient

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        try:
            config = LatticeConfig(api_key=api_key)
        except Exception as exc:  # noqa: BLE001
            return unavailable_session(
                sut_id=self.sut_id,
                task_id=task_id,
                session_id=session_id,
                prompt_bundles=prompt_bundles,
                reason=f"lattice client init failed: {exc}",
                metadata=meta,
            )

        # One asyncio.run for the full longitudinal sequence — never nest
        # per-step asyncio.run calls against a shared AsyncAnthropic client.
        return asyncio.run(
            _run_longitudinal_async(
                sut_id=self.sut_id,
                task_id=task_id,
                session_id=session_id,
                prompt_bundles=prompt_bundles,
                meta=meta,
                config=config,
                anthropic_client_cls=AnthropicClient,
                anthropic_api_key=api_key,
                run_session_async=run_lattice_session_async,
                grounding_mode=grounding_mode,
                tavily_client_cls=tavily_client_cls,
            )
        )


async def _run_longitudinal_async(
    *,
    sut_id: str,
    task_id: str,
    session_id: str,
    prompt_bundles: list[dict[str, Any]],
    meta: dict[str, Any],
    config: Any,
    anthropic_client_cls: Any,
    anthropic_api_key: Optional[str],
    run_session_async: RunSessionAsync,
    grounding_mode: GroundingMode,
    tavily_client_cls: Any = None,
) -> SessionResult:
    """Drive every longitudinal step inside the caller's running event loop."""
    steps: list[StepRecord] = []
    prior_transcript: list[str] = []
    client: Any = None
    search_client: Any = None
    try:
        client = anthropic_client_cls(api_key=anthropic_api_key)
        if grounding_mode is GroundingMode.TAVILY:
            search_client = tavily_client_cls(
                api_key=os.environ["TAVILY_API_KEY"],
            )
        for index, bundle in enumerate(prompt_bundles):
            prompt = _compose_lattice_prompt(
                rendered_step=str(bundle["rendered_prompt"]),
                prior_transcript=prior_transcript,
                step_index=index,
                total_steps=len(prompt_bundles),
            )
            bundle_out = {
                **dict(bundle),
                "lattice_prompt": prompt,
            }
            t0 = time.perf_counter()
            try:
                lattice_session = await run_session_async(
                    prompt,
                    config=config,
                    client=client,
                    session_id=f"{session_id}__step{index + 1}",
                    phase_0_client=client
                    if grounding_mode is GroundingMode.TAVILY
                    else None,
                    search_client=search_client,
                )
            except Exception as exc:  # noqa: BLE001
                latency_ms = (time.perf_counter() - t0) * 1000.0
                steps.append(
                    StepRecord(
                        step_id=str(bundle["step_id"]),
                        perturbation_id=str(bundle["perturbation_id"]),
                        status=StepStatus.ERROR.value,
                        prompt_bundle=bundle_out,
                        raw_output=None,
                        latency_ms=latency_ms,
                        reason=f"lattice error: {exc}",
                    )
                )
                for later in prompt_bundles[len(steps) :]:
                    steps.append(
                        StepRecord(
                            step_id=str(later["step_id"]),
                            perturbation_id=str(later["perturbation_id"]),
                            status=StepStatus.ABORTED.value,
                            prompt_bundle=dict(later),
                            raw_output=None,
                            reason="aborted after prior step error",
                        )
                    )
                return SessionResult(
                    session_id=session_id,
                    task_id=task_id,
                    sut_id=sut_id,
                    status=StepStatus.ABORTED.value,
                    steps=steps,
                    metadata=meta,
                )

            latency_ms = (time.perf_counter() - t0) * 1000.0
            raw = _extract_lattice_output(lattice_session)
            # Continuity for the next step: only what the product actually emitted.
            prior_transcript.append(
                f"[step {index + 1} / {bundle['step_id']}]\n{raw}"
            )

            structured: dict[str, Any] = {
                "lattice_session_id": getattr(lattice_session, "session_id", None),
            }
            # Explicit commitment transitions only — never inferred from prose.
            transitions = _explicit_commitment_transitions(lattice_session)
            steps.append(
                StepRecord(
                    step_id=str(bundle["step_id"]),
                    perturbation_id=str(bundle["perturbation_id"]),
                    status=StepStatus.COMPLETED.value,
                    prompt_bundle=bundle_out,
                    raw_output=raw,
                    latency_ms=latency_ms,
                    structured=structured,
                    commitment_transitions=transitions,
                )
            )

        return SessionResult(
            session_id=session_id,
            task_id=task_id,
            sut_id=sut_id,
            status=StepStatus.COMPLETED.value,
            steps=steps,
            metadata=meta,
        )
    finally:
        if client is not None:
            await _aclose_provider_client(client)
        if search_client is not None:
            await _aclose_provider_client(search_client)


async def _aclose_provider_client(client: Any) -> None:
    """Close the underlying AsyncAnthropic (or compatible) client in this loop.

    AnthropicClient stores the SDK client on ``_client``. Prefer closing that
    handle so HTTP transports are torn down on the same loop that created them.
    Falls back to ``client.close`` / ``client.aclose`` when no nested client
    is present (tests / alternate fakes).
    """
    underlying = getattr(client, "_client", None)
    targets: list[Any] = []
    if underlying is not None:
        targets.append(underlying)
    targets.append(client)

    seen: set[int] = set()
    for target in targets:
        marker = id(target)
        if marker in seen:
            continue
        seen.add(marker)
        closer = getattr(target, "close", None)
        if closer is None:
            closer = getattr(target, "aclose", None)
        if closer is None or not callable(closer):
            continue
        try:
            result = closer()
            if inspect.isawaitable(result):
                await result
            return
        except Exception:  # noqa: BLE001 — cleanup must not mask session outcome
            return


def _ensure_src_on_path() -> None:
    src = str(_REPO_ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)


def _compose_lattice_prompt(
    *,
    rendered_step: str,
    prior_transcript: list[str],
    step_index: int,
    total_steps: int,
) -> str:
    header = (
        f"Longitudinal experiment step {step_index + 1} of {total_steps}.\n"
        "Treat the following as the operator prompt for this lattice session.\n"
    )
    if not prior_transcript:
        return header + "\n" + rendered_step
    prior = (
        "\n\n--- Prior step outputs from this longitudinal session "
        "(continuity context; not peer seats) ---\n\n"
        + "\n\n".join(prior_transcript)
        + "\n\n--- End prior outputs ---\n\n"
    )
    return header + prior + rendered_step


def _extract_lattice_output(session: Any) -> str:
    phase_4 = getattr(session, "phase_4", None)
    if phase_4 is not None:
        output = getattr(phase_4, "output", None)
        if isinstance(output, str) and output.strip():
            return output
    # Honest fallback: no synthesis available — do not invent one.
    sid = getattr(session, "session_id", "?")
    return f"(lattice session {sid} completed without phase_4.output)"


def _explicit_commitment_transitions(session: Any) -> Optional[list[dict[str, Any]]]:
    """Copy only structured commitment transition artifacts if present."""
    raw = getattr(session, "commitment_transitions", None)
    if not raw:
        return None
    out: list[dict[str, Any]] = []
    for item in raw:
        if hasattr(item, "model_dump"):
            out.append(item.model_dump())
        elif isinstance(item, dict):
            out.append(dict(item))
        else:
            # Unknown shape — record type name only; do not parse prose.
            out.append({"repr_type": type(item).__name__})
    return out or None
