"""Phase 2 Task 8 — baseline experiment runner contract tests.

Strict TDD surface for:
- SUT registry completeness
- deterministic dry-run / planning output (no fabricated responses)
- task and perturbation id propagation
- unavailable-provider behavior when live execution is not configured

No network calls. No fabricated model outputs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from experiments.baselines import REQUIRED_SUT_IDS, SUT_REGISTRY, get_sut
from experiments.baselines.protocol import GroundingMode, RunMode, StepStatus
from experiments.runner_lib import (
    build_prompt_bundle,
    load_task,
    load_tasks,
    run_batch,
    write_batch_outputs,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS_DIR = REPO_ROOT / "experiments" / "tasks"

DEFAULT_SEQUENCE = (
    "present_initial_claim",
    "apply_controlled_challenge",
    "apply_evidence_update",
    "apply_reversal_or_removal",
)

STEP_TO_TABLE = {
    "present_initial_claim": "initial_claim",
    "apply_controlled_challenge": "controlled_challenge",
    "apply_evidence_update": "evidence_update",
    "apply_reversal_or_removal": "reversal_or_removal",
}


# ---------------------------------------------------------------------------
# SUT registry
# ---------------------------------------------------------------------------


def test_required_sut_ids_are_exactly_the_four_declared_comparators() -> None:
    assert REQUIRED_SUT_IDS == (
        "strongest_single_peer",
        "simple_parallel_responses",
        "conventional_judge_summarizer",
        "golden_lattice",
    )


def test_sut_registry_contains_every_required_sut() -> None:
    assert set(SUT_REGISTRY) == set(REQUIRED_SUT_IDS)
    for sut_id in REQUIRED_SUT_IDS:
        sut = get_sut(sut_id)
        assert sut.sut_id == sut_id


def test_conventional_judge_is_marked_non_canonical_and_optional() -> None:
    sut = get_sut("conventional_judge_summarizer")
    assert sut.canonical is False
    assert sut.optional is True
    # Other declared SUTs remain part of the primary comparison set.
    assert get_sut("golden_lattice").canonical is True
    assert get_sut("strongest_single_peer").optional is False


# ---------------------------------------------------------------------------
# Task loading / validation
# ---------------------------------------------------------------------------


def test_load_tasks_returns_four_validated_tasks() -> None:
    tasks = load_tasks(TASKS_DIR)
    assert len(tasks) == 4
    ids = {t["id"] for t in tasks}
    assert len(ids) == 4
    for task in tasks:
        assert tuple(task["expected_perturbation_sequence"]) == DEFAULT_SEQUENCE


def test_load_task_preserves_nested_perturbation_ids() -> None:
    path = TASKS_DIR / "design_critique_v1.toml"
    task = load_task(path)
    assert task["id"] == "gl.longitudinal.design_critique.v1"
    assert task["initial_claim"]["id"] == "dc.v1.initial_claim"
    assert task["controlled_challenge"]["id"] == "dc.v1.controlled_challenge"
    assert task["evidence_update"]["id"] == "dc.v1.evidence_update"
    assert task["reversal_or_removal"]["id"] == "dc.v1.reversal_or_removal"


# ---------------------------------------------------------------------------
# Prompt bundles (planning surface shared by all SUTs)
# ---------------------------------------------------------------------------


def test_build_prompt_bundle_renders_placeholders_and_keeps_ids() -> None:
    task = load_task(TASKS_DIR / "design_critique_v1.toml")
    bundle = build_prompt_bundle(task, step_id="present_initial_claim")
    assert bundle["step_id"] == "present_initial_claim"
    assert bundle["perturbation_id"] == "dc.v1.initial_claim"
    assert bundle["task_id"] == task["id"]
    assert "{claim_text}" not in bundle["rendered_prompt"]
    assert "offset/limit pagination" in bundle["rendered_prompt"]
    assert bundle["source"]["text"].strip()
    assert bundle["source"]["prompt"].strip()


@pytest.mark.parametrize("step_id", DEFAULT_SEQUENCE)
def test_build_prompt_bundle_covers_all_four_steps(step_id: str) -> None:
    task = load_task(TASKS_DIR / "ambiguous_evidence_synthesis_v1.toml")
    bundle = build_prompt_bundle(task, step_id=step_id)
    table = task[STEP_TO_TABLE[step_id]]
    assert bundle["perturbation_id"] == table["id"]
    assert bundle["step_id"] == step_id
    # Never leave unresolved template braces for known placeholders.
    assert "{claim_text}" not in bundle["rendered_prompt"]
    assert "{challenge_text}" not in bundle["rendered_prompt"]
    assert "{evidence_text}" not in bundle["rendered_prompt"]
    assert "{reversal_text}" not in bundle["rendered_prompt"]


# ---------------------------------------------------------------------------
# Dry-run batch: deterministic structure, no fabricated responses
# ---------------------------------------------------------------------------


def test_dry_run_batch_covers_all_tasks_and_suts(tmp_path: Path) -> None:
    result = run_batch(
        tasks_dir=TASKS_DIR,
        sut_ids=list(REQUIRED_SUT_IDS),
        mode=RunMode.DRY_RUN,
        output_dir=tmp_path,
        run_id="test_dry_run_fixed",
        clock=lambda: "2026-08-06T00:00:00+00:00",
    )
    assert result["manifest"]["mode"] == "dry_run"
    assert result["manifest"]["run_id"] == "test_dry_run_fixed"
    assert set(result["manifest"]["sut_ids"]) == set(REQUIRED_SUT_IDS)
    # 4 tasks × 4 SUTs
    assert len(result["sessions"]) == 16
    pairs = {(s["task_id"], s["sut_id"]) for s in result["sessions"]}
    assert len(pairs) == 16


def test_run_manifest_records_default_ungrounded_mode() -> None:
    result = run_batch(
        tasks_dir=TASKS_DIR,
        task_ids=["gl.longitudinal.design_critique.v1"],
        sut_ids=["golden_lattice"],
        mode=RunMode.DRY_RUN,
        output_dir=None,
        run_id="grounding_default",
        clock=lambda: "2026-08-06T00:00:00+00:00",
    )
    assert result["manifest"]["grounding_mode"] == GroundingMode.NONE.value
    assert result["sessions"][0]["metadata"]["grounding_mode"] == GroundingMode.NONE.value


def test_run_batch_propagates_tavily_grounding_mode() -> None:
    result = run_batch(
        tasks_dir=TASKS_DIR,
        task_ids=["gl.longitudinal.design_critique.v1"],
        sut_ids=["golden_lattice"],
        mode=RunMode.DRY_RUN,
        grounding_mode=GroundingMode.TAVILY,
        output_dir=None,
        run_id="grounding_tavily",
        clock=lambda: "2026-08-06T00:00:00+00:00",
    )
    assert result["manifest"]["grounding_mode"] == GroundingMode.TAVILY.value
    assert result["sessions"][0]["metadata"]["grounding_mode"] == GroundingMode.TAVILY.value


def test_dry_run_never_fabricates_raw_output() -> None:
    result = run_batch(
        tasks_dir=TASKS_DIR,
        sut_ids=list(REQUIRED_SUT_IDS),
        mode=RunMode.DRY_RUN,
        output_dir=None,
        run_id="dry_no_output",
        clock=lambda: "2026-08-06T00:00:00+00:00",
    )
    for session in result["sessions"]:
        assert session["status"] == StepStatus.PLANNED.value
        assert len(session["steps"]) == 4
        for step in session["steps"]:
            assert step["status"] == StepStatus.PLANNED.value
            assert step["raw_output"] is None
            assert step["prompt_bundle"] is not None
            assert step["prompt_bundle"]["rendered_prompt"]
            assert step["step_id"] in DEFAULT_SEQUENCE
            assert step["perturbation_id"]
            # Honesty: dry-run must not invent latency/cost either.
            assert step.get("latency_ms") is None
            assert step.get("cost_usd") is None
            # Never invent commitment states from prose (field absent or empty).
            transitions = step.get("commitment_transitions")
            assert transitions in (None, [], ())


def test_dry_run_is_deterministic_given_fixed_run_id_and_clock() -> None:
    kwargs = dict(
        tasks_dir=TASKS_DIR,
        sut_ids=list(REQUIRED_SUT_IDS),
        mode=RunMode.DRY_RUN,
        output_dir=None,
        run_id="det_run",
        clock=lambda: "2026-08-06T12:00:00+00:00",
        git_commit="deadbeef",
    )
    a = run_batch(**kwargs)
    b = run_batch(**kwargs)
    # Drop any non-deterministic wall-clock fields if present; core payload stable.
    assert a["manifest"]["run_id"] == b["manifest"]["run_id"]
    assert a["manifest"]["created_at"] == b["manifest"]["created_at"]
    assert a["manifest"]["git_commit"] == b["manifest"]["git_commit"]
    assert _strip_volatile(a) == _strip_volatile(b)


def test_task_and_perturbation_ids_propagate_into_step_records() -> None:
    task = load_task(TASKS_DIR / "competing_scientific_explanations_v1.toml")
    result = run_batch(
        tasks_dir=TASKS_DIR,
        task_ids=[task["id"]],
        sut_ids=["strongest_single_peer"],
        mode=RunMode.DRY_RUN,
        output_dir=None,
        run_id="prop_ids",
        clock=lambda: "2026-08-06T00:00:00+00:00",
    )
    assert len(result["sessions"]) == 1
    session = result["sessions"][0]
    assert session["task_id"] == task["id"]
    assert session["sut_id"] == "strongest_single_peer"
    assert session["session_id"]
    expected = [
        (step_id, task[STEP_TO_TABLE[step_id]]["id"])
        for step_id in DEFAULT_SEQUENCE
    ]
    got = [(s["step_id"], s["perturbation_id"]) for s in session["steps"]]
    assert got == expected


def test_write_batch_outputs_writes_json_and_human_summary(tmp_path: Path) -> None:
    result = run_batch(
        tasks_dir=TASKS_DIR,
        sut_ids=["golden_lattice"],
        task_ids=["gl.longitudinal.design_critique.v1"],
        mode=RunMode.DRY_RUN,
        output_dir=None,
        run_id="write_out",
        clock=lambda: "2026-08-06T00:00:00+00:00",
    )
    paths = write_batch_outputs(result, tmp_path)
    assert paths["json"].is_file()
    assert paths["summary"].is_file()
    payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert payload["manifest"]["run_id"] == "write_out"
    assert len(payload["sessions"]) == 1
    summary = paths["summary"].read_text(encoding="utf-8")
    assert "write_out" in summary
    assert "golden_lattice" in summary
    assert "gl.longitudinal.design_critique.v1" in summary
    assert "planned" in summary.lower() or "PLANNED" in summary
    # Summary must honestly mark missing model output (not invent answers).
    assert re.search(r"raw_output=null|no model output", summary, re.I)
    # Must not present dry-run as a completed live success.
    assert "**completed**" not in summary.lower()


# ---------------------------------------------------------------------------
# Unavailable provider / live without credentials
# ---------------------------------------------------------------------------


def test_live_mode_without_credentials_records_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    # Ensure no ambient keys make this accidentally live.
    for key in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "XAI_API_KEY",
        "GOLDEN_LATTICE_EXPERIMENT_LIVE",
    ):
        monkeypatch.delenv(key, raising=False)

    result = run_batch(
        tasks_dir=TASKS_DIR,
        task_ids=["gl.longitudinal.design_critique.v1"],
        sut_ids=list(REQUIRED_SUT_IDS),
        mode=RunMode.LIVE,
        output_dir=None,
        run_id="live_unavail",
        clock=lambda: "2026-08-06T00:00:00+00:00",
    )
    assert len(result["sessions"]) == 4
    for session in result["sessions"]:
        assert session["status"] == StepStatus.UNAVAILABLE.value, session
        assert session.get("unavailable_reason") or session.get("reason")
        reason = (session.get("unavailable_reason") or session.get("reason") or "").lower()
        assert "not configured" in reason or "unavailable" in reason or "api" in reason
        # No fabricated completions when skipped as unavailable.
        for step in session["steps"]:
            assert step["status"] == StepStatus.UNAVAILABLE.value
            assert step["raw_output"] is None
            # Prompt bundles still useful for planning/audit even when live is blocked.
            assert step["prompt_bundle"] is not None


def test_live_mode_does_not_silently_fall_back_to_fake_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GOLDEN_LATTICE_EXPERIMENT_LIVE", raising=False)
    result = run_batch(
        tasks_dir=TASKS_DIR,
        task_ids=["gl.longitudinal.decision_under_changing_evidence.v1"],
        sut_ids=["strongest_single_peer"],
        mode=RunMode.LIVE,
        output_dir=None,
        run_id="no_fake",
        clock=lambda: "2026-08-06T00:00:00+00:00",
    )
    session = result["sessions"][0]
    assert session["status"] != StepStatus.COMPLETED.value
    assert all(s.get("raw_output") is None for s in session["steps"])


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _strip_volatile(payload: dict) -> dict:
    """Normalize payload for equality (session_id may be derived deterministically)."""
    # Full structural equality expected under fixed run_id + clock + git_commit.
    return json.loads(json.dumps(payload, sort_keys=True))
