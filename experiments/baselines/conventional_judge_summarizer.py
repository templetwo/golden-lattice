"""Conventional judge/summarizer baseline — OPTIONAL and NON-CANONICAL.

This is a comparison foil only. It must never feed Golden Lattice's Phase 4
synthesis path. Pattern: independent peer drafts + one judge summary call.

Marked optional=True, canonical=False so operators can exclude it without
breaking the required registry declaration.
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
from experiments.baselines.simple_parallel_responses import DEFAULT_PEER_MODELS

DEFAULT_JUDGE_MODEL = "claude-opus-4-20250514"

JUDGE_INSTRUCTION = """\
You are a conventional external judge/summarizer (NOT part of Golden Lattice).
Read the independent peer responses below and produce a single concise summary
of positions, disagreements, and a recommended answer if one is warranted.
Do not claim to be a lattice peer. Do not invent commitments or beliefs.
"""


class ConventionalJudgeSummarizerSUT:
    """Non-canonical comparator: parallel peers + external judge summary."""

    sut_id = "conventional_judge_summarizer"
    canonical = False
    optional = True

    def __init__(
        self,
        *,
        peer_models: tuple[str, ...] = DEFAULT_PEER_MODELS,
        judge_model: str = DEFAULT_JUDGE_MODEL,
    ) -> None:
        self.peer_models = peer_models
        self.judge_model = judge_model

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
            "canonical": False,
            "optional": True,
            "peer_models": list(self.peer_models),
            "judge_model": self.judge_model,
            "grounding_mode": grounding_mode.value,
            "warning": (
                "NON-CANONICAL comparator. Outputs must not enter Golden Lattice "
                "Phase 4 synthesis or seat authority."
            ),
        }
        enriched = [_enrich_judge_bundle(b, self.peer_models, self.judge_model) for b in prompt_bundles]

        if mode is RunMode.DRY_RUN:
            return planned_session(
                sut_id=self.sut_id,
                task_id=task_id,
                session_id=session_id,
                prompt_bundles=enriched,
                notes=(
                    "NON-CANONICAL optional baseline: independent peers then one "
                    "judge/summarizer call per step. Dry-run only plans prompts."
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
                notes="non-canonical baseline unavailable",
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
        peer_histories: dict[str, list[dict[str, str]]] = {
            m: [] for m in self.peer_models
        }
        steps: list[StepRecord] = []

        for bundle in prompt_bundles:
            user_text = str(bundle["rendered_prompt"])
            peer_outputs: dict[str, str] = {}
            t0 = time.perf_counter()
            step_error: str | None = None

            for model in self.peer_models:
                peer_histories[model].append({"role": "user", "content": user_text})
                try:
                    resp = client.messages.create(
                        model=model,
                        max_tokens=4096,
                        messages=list(peer_histories[model]),
                    )
                except Exception as exc:  # noqa: BLE001
                    step_error = f"peer {model}: {exc}"
                    break
                text = _anthropic_text(resp)
                peer_histories[model].append({"role": "assistant", "content": text})
                peer_outputs[model] = text

            judge_text: str | None = None
            if step_error is None:
                judge_user = _build_judge_user(user_text, peer_outputs)
                try:
                    jresp = client.messages.create(
                        model=self.judge_model,
                        max_tokens=4096,
                        system=JUDGE_INSTRUCTION,
                        messages=[{"role": "user", "content": judge_user}],
                    )
                    judge_text = _anthropic_text(jresp)
                except Exception as exc:  # noqa: BLE001
                    step_error = f"judge {self.judge_model}: {exc}"

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
                    notes="NON-CANONICAL session aborted",
                    metadata=meta,
                )

            raw = (
                "\n\n".join(
                    f"=== peer:{model} ===\n{peer_outputs[model]}"
                    for model in self.peer_models
                )
                + f"\n\n=== judge:{self.judge_model} (NON-CANONICAL) ===\n{judge_text}"
            )
            steps.append(
                StepRecord(
                    step_id=str(bundle["step_id"]),
                    perturbation_id=str(bundle["perturbation_id"]),
                    status=StepStatus.COMPLETED.value,
                    prompt_bundle=dict(bundle),
                    raw_output=raw,
                    latency_ms=latency_ms,
                    structured={
                        "peer_outputs": peer_outputs,
                        "judge_output": judge_text,
                        "canonical": False,
                    },
                )
            )

        return SessionResult(
            session_id=session_id,
            task_id=task_id,
            sut_id=self.sut_id,
            status=StepStatus.COMPLETED.value,
            steps=steps,
            notes="NON-CANONICAL judge/summarizer comparator complete",
            metadata=meta,
        )


def _enrich_judge_bundle(
    bundle: Mapping[str, Any],
    peer_models: tuple[str, ...],
    judge_model: str,
) -> dict[str, Any]:
    out = dict(bundle)
    out["parallel_peers"] = [
        {"model": m, "role": "independent_responder"} for m in peer_models
    ]
    out["judge"] = {
        "model": judge_model,
        "role": "external_summarizer",
        "canonical": False,
        "system_instruction": JUDGE_INSTRUCTION.strip(),
    }
    return out


def _build_judge_user(step_prompt: str, peer_outputs: Mapping[str, str]) -> str:
    parts = [
        "STEP PROMPT PRESENTED TO PEERS:",
        step_prompt.strip(),
        "",
        "INDEPENDENT PEER RESPONSES:",
    ]
    for model, text in peer_outputs.items():
        parts.append(f"--- {model} ---")
        parts.append(text.strip())
        parts.append("")
    parts.append("Produce the judge/summarizer output now.")
    return "\n".join(parts)


def _anthropic_text(resp: Any) -> str:
    parts: list[str] = []
    for block in getattr(resp, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts)
