"""Strongest single peer baseline — one model, multi-step session continuity.

Not the lattice. One sequential conversation across the four protocol steps.
Live execution uses the Anthropic Messages API when configured; otherwise the
session is recorded as unavailable with reason (never fabricated).
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

# Seat label only — not a claim about which provider endpoint is strongest.
DEFAULT_PEER_MODEL = "claude-opus-4-20250514"


class StrongestSinglePeerSUT:
    sut_id = "strongest_single_peer"
    canonical = True
    optional = False

    def __init__(self, *, model: str = DEFAULT_PEER_MODEL) -> None:
        self.model = model

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
            "model": self.model,
            "session_continuity": "multi_turn_messages",
            "grounding_mode": grounding_mode.value,
        }
        if mode is RunMode.DRY_RUN:
            return planned_session(
                sut_id=self.sut_id,
                task_id=task_id,
                session_id=session_id,
                prompt_bundles=prompt_bundles,
                notes=(
                    "Single-peer multi-turn plan: each step appends user turn; "
                    "assistant replies retained for continuity. No model call in dry_run."
                ),
                metadata=meta,
            )

        avail = self.availability(mode)
        if not avail.available:
            return unavailable_session(
                sut_id=self.sut_id,
                task_id=task_id,
                session_id=session_id,
                prompt_bundles=prompt_bundles,
                reason=avail.reason or "provider unavailable",
                metadata=meta,
            )

        return self._run_live(task_id, session_id, prompt_bundles, meta)

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
        messages: list[dict[str, str]] = []
        steps: list[StepRecord] = []

        for bundle in prompt_bundles:
            user_text = str(bundle["rendered_prompt"])
            messages.append({"role": "user", "content": user_text})
            t0 = time.perf_counter()
            try:
                resp = client.messages.create(
                    model=self.model,
                    max_tokens=4096,
                    messages=list(messages),
                )
            except Exception as exc:  # noqa: BLE001 — record honestly, abort sequence
                latency_ms = (time.perf_counter() - t0) * 1000.0
                steps.append(
                    StepRecord(
                        step_id=str(bundle["step_id"]),
                        perturbation_id=str(bundle["perturbation_id"]),
                        status=StepStatus.ERROR.value,
                        prompt_bundle=dict(bundle),
                        raw_output=None,
                        latency_ms=latency_ms,
                        reason=f"provider error: {exc}",
                    )
                )
                # Mark remaining steps aborted without calling the model.
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
                    notes="session aborted mid-sequence",
                    metadata=meta,
                )

            latency_ms = (time.perf_counter() - t0) * 1000.0
            text = _anthropic_text(resp)
            messages.append({"role": "assistant", "content": text})
            usage = getattr(resp, "usage", None)
            cost_usd = None  # cost tables drift; leave unset unless operator supplies
            steps.append(
                StepRecord(
                    step_id=str(bundle["step_id"]),
                    perturbation_id=str(bundle["perturbation_id"]),
                    status=StepStatus.COMPLETED.value,
                    prompt_bundle={
                        **dict(bundle),
                        "messages_snapshot_len": len(messages),
                    },
                    raw_output=text,
                    latency_ms=latency_ms,
                    cost_usd=cost_usd,
                    structured={
                        "usage": {
                            "input_tokens": getattr(usage, "input_tokens", None),
                            "output_tokens": getattr(usage, "output_tokens", None),
                        }
                        if usage is not None
                        else None
                    },
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


def _anthropic_text(resp: Any) -> str:
    parts: list[str] = []
    for block in getattr(resp, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts)
