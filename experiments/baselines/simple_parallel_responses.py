"""Simple parallel responses baseline — independent peer answers, no lattice.

At each step, N peer prompts are issued independently (no cross-reading, no
canonical synthesis). Outputs are concatenated for the step record. Session
continuity is per-peer multi-turn history when live.
"""

from __future__ import annotations

import os
import time
from typing import Any, Mapping

from experiments.baselines._common import (
    default_availability,
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

DEFAULT_PEER_MODELS: tuple[str, ...] = (
    "claude-opus-4-20250514",
    "claude-sonnet-4-20250514",
    "claude-haiku-4-20250414",
)


class SimpleParallelResponsesSUT:
    sut_id = "simple_parallel_responses"
    canonical = True
    optional = False

    def __init__(self, *, peer_models: tuple[str, ...] = DEFAULT_PEER_MODELS) -> None:
        self.peer_models = peer_models

    def availability(self, mode: RunMode) -> Availability:
        return default_availability(mode)

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
            "peer_models": list(self.peer_models),
            "synthesis": "none",
            "grounding_mode": grounding_mode.value,
        }
        # Enrich prompt bundles with per-peer plan surface (still no outputs).
        enriched = [_enrich_parallel_bundle(b, self.peer_models) for b in prompt_bundles]

        if mode is RunMode.DRY_RUN:
            return planned_session(
                sut_id=self.sut_id,
                task_id=task_id,
                session_id=session_id,
                prompt_bundles=enriched,
                notes=(
                    "Parallel independent peers per step; no cross-reading and no "
                    "judge. Dry-run emits prompt bundles only."
                ),
                metadata=meta,
            )

        avail = self.availability(mode)
        if not avail.available:
            return unavailable_session(
                sut_id=self.sut_id,
                task_id=task_id,
                session_id=session_id,
                prompt_bundles=enriched,
                reason=avail.reason or "provider unavailable",
                metadata=meta,
            )

        return self._run_live(task_id, session_id, enriched, meta)

    def _run_live(
        self,
        task_id: str,
        session_id: str,
        prompt_bundles: list[dict[str, Any]],
        meta: dict[str, Any],
    ) -> SessionResult:
        try:
            import anthropic
        except ImportError:
            return unavailable_session(
                sut_id=self.sut_id,
                task_id=task_id,
                session_id=session_id,
                prompt_bundles=prompt_bundles,
                reason="anthropic SDK not installed",
                metadata=meta,
            )

        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        histories: dict[str, list[dict[str, str]]] = {
            m: [] for m in self.peer_models
        }
        steps: list[StepRecord] = []

        for bundle in prompt_bundles:
            user_text = str(bundle["rendered_prompt"])
            peer_outputs: dict[str, str] = {}
            t0 = time.perf_counter()
            step_error: str | None = None
            for model in self.peer_models:
                histories[model].append({"role": "user", "content": user_text})
                try:
                    resp = client.messages.create(
                        model=model,
                        max_tokens=4096,
                        messages=list(histories[model]),
                    )
                except Exception as exc:  # noqa: BLE001
                    step_error = f"{model}: {exc}"
                    break
                text = _anthropic_text(resp)
                histories[model].append({"role": "assistant", "content": text})
                peer_outputs[model] = text

            latency_ms = (time.perf_counter() - t0) * 1000.0
            if step_error is not None:
                steps.append(
                    StepRecord(
                        step_id=str(bundle["step_id"]),
                        perturbation_id=str(bundle["perturbation_id"]),
                        status=StepStatus.ERROR.value,
                        prompt_bundle=dict(bundle),
                        raw_output=None,
                        latency_ms=latency_ms,
                        reason=f"provider error: {step_error}",
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
                    sut_id=self.sut_id,
                    status=StepStatus.ABORTED.value,
                    steps=steps,
                    metadata=meta,
                )

            # Concatenate peer outputs with clear labels — not a synthesis.
            raw = "\n\n".join(
                f"=== peer:{model} ===\n{peer_outputs[model]}" for model in self.peer_models
            )
            steps.append(
                StepRecord(
                    step_id=str(bundle["step_id"]),
                    perturbation_id=str(bundle["perturbation_id"]),
                    status=StepStatus.COMPLETED.value,
                    prompt_bundle=dict(bundle),
                    raw_output=raw,
                    latency_ms=latency_ms,
                    structured={"peer_outputs": peer_outputs},
                )
            )

        return SessionResult(
            session_id=session_id,
            task_id=task_id,
            sut_id=self.sut_id,
            status=StepStatus.COMPLETED.value,
            steps=steps,
            metadata=meta,
        )


def _enrich_parallel_bundle(
    bundle: Mapping[str, Any], peer_models: tuple[str, ...]
) -> dict[str, Any]:
    out = dict(bundle)
    out["parallel_peers"] = [
        {"model": m, "role": "independent_responder"} for m in peer_models
    ]
    return out


def _anthropic_text(resp: Any) -> str:
    parts: list[str] = []
    for block in getattr(resp, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts)
