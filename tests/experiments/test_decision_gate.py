"""Phase 3 Task 11 / Phase 4 Decision Gate — executable gate analysis.

Strict TDD surface for experiments/decision_gate.py:

1. Load run JSON + structural validity
2. Require four declared SUTs and all four corpus tasks
3. Summarize completion/unavailable/planned/error counts
4. Comparison tables only from completed structured records /
   explicit commitment observations (never prose inference)
5. Four success dimensions: behavioral, epistemic, constitutional, practical
6. Explicit decision: PASS | INSUFFICIENT_EVIDENCE | FAIL with reasons
7. Dry-run / unavailable never becomes a quality PASS

No network. Synthetic fixtures + checked-in run JSON only.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = REPO_ROOT / "experiments" / "baselines" / "runs"
DRY_RUN_JSON = RUNS_DIR / "task8_dryrun_all.json"
LIVE_UNAVAIL_JSON = RUNS_DIR / "task8_live_unavail.json"

REQUIRED_SUTS = (
    "strongest_single_peer",
    "simple_parallel_responses",
    "conventional_judge_summarizer",
    "golden_lattice",
)

REQUIRED_TASKS = (
    "gl.longitudinal.ambiguous_evidence_synthesis.v1",
    "gl.longitudinal.competing_scientific_explanations.v1",
    "gl.longitudinal.decision_under_changing_evidence.v1",
    "gl.longitudinal.design_critique.v1",
)

STEP_IDS = (
    "present_initial_claim",
    "apply_controlled_challenge",
    "apply_evidence_update",
    "apply_reversal_or_removal",
)

DECISIONS = frozenset({"PASS", "INSUFFICIENT_EVIDENCE", "FAIL"})


def _import_gate():
    from experiments.decision_gate import (  # noqa: WPS433
        DIMENSIONS,
        THRESHOLDS,
        DecisionGateReport,
        evaluate_decision_gate,
        format_gate_report,
        load_run_json,
        main,
        summarize_statuses,
    )

    return {
        "DIMENSIONS": DIMENSIONS,
        "THRESHOLDS": THRESHOLDS,
        "DecisionGateReport": DecisionGateReport,
        "evaluate_decision_gate": evaluate_decision_gate,
        "format_gate_report": format_gate_report,
        "load_run_json": load_run_json,
        "main": main,
        "summarize_statuses": summarize_statuses,
    }


def _transition(
    *,
    claim_id: str = "c1",
    prior: str = "proposed",
    nxt: str = "defended",
    source_event: str = "step",
    reason: str = "explicit structured reason",
    sequence_index: int = 0,
    source_model: str = "opus",
) -> dict:
    return {
        "claim_id": claim_id,
        "source_model": source_model,
        "prior_state": prior,
        "next_state": nxt,
        "source_event": source_event,
        "reason": reason,
        "sequence_index": sequence_index,
    }


def _session(
    *,
    sut_id: str,
    task_id: str,
    status: str,
    transitions_by_step: dict[str, list[dict]] | None = None,
    structured_by_step: dict[str, dict] | None = None,
    raw_by_step: dict[str, str] | None = None,
    unavailable_reason: str | None = None,
    metadata: dict | None = None,
    latency_ms: float | None = 10.0,
) -> dict:
    transitions_by_step = transitions_by_step or {}
    structured_by_step = structured_by_step or {}
    raw_by_step = raw_by_step or {}
    steps = []
    for step_id in STEP_IDS:
        step_status = status
        raw = None
        structured = structured_by_step.get(step_id)
        transitions = transitions_by_step.get(step_id)
        if status == "completed":
            raw = raw_by_step.get(step_id, f"[{sut_id}] {step_id} prose (not a commitment)")
            if structured is None:
                structured = {}
        steps.append(
            {
                "step_id": step_id,
                "perturbation_id": f"{task_id.split('.')[-2]}.{step_id}",
                "status": step_status,
                "prompt_bundle": {
                    "step_id": step_id,
                    "task_id": task_id,
                    "rendered_prompt": f"prompt for {step_id}",
                },
                "raw_output": raw,
                "latency_ms": latency_ms if status == "completed" else None,
                "cost_usd": None,
                "reason": None
                if status == "completed"
                else (unavailable_reason or f"status={status}"),
                "structured": structured,
                "commitment_transitions": transitions,
            }
        )
    meta = {
        "canonical": sut_id == "golden_lattice",
        "optional": sut_id == "conventional_judge_summarizer",
        "baseline": sut_id,
    }
    if metadata:
        meta.update(metadata)
    payload = {
        "session_id": f"exp_{sut_id}_{task_id}",
        "task_id": task_id,
        "sut_id": sut_id,
        "status": status,
        "steps": steps,
        "metadata": meta,
    }
    if unavailable_reason is not None:
        payload["unavailable_reason"] = unavailable_reason
    if status == "planned":
        payload["notes"] = "dry-run planned"
    return payload


def _run_payload(
    *,
    mode: str = "live",
    statuses: dict[str, str] | None = None,
    task_ids: tuple[str, ...] = REQUIRED_TASKS,
    sut_ids: tuple[str, ...] = REQUIRED_SUTS,
    session_builder=None,
    git_commit: str | None = "abc1234",
) -> dict:
    """Build a Task-8-shaped run covering task×SUT grid."""
    statuses = statuses or {s: "planned" for s in sut_ids}
    sessions = []
    for task_id in task_ids:
        for sut_id in sut_ids:
            st = statuses.get(sut_id, "planned")
            if session_builder is not None:
                sessions.append(session_builder(sut_id=sut_id, task_id=task_id, status=st))
            else:
                sessions.append(_session(sut_id=sut_id, task_id=task_id, status=st))
    manifest = {
        "run_id": "test_gate_run",
        "mode": mode,
        "created_at": "2026-08-06T00:00:00+00:00",
        "git_commit": git_commit,
        "sut_ids": list(sut_ids),
        "task_ids": list(task_ids),
        "session_count": len(sessions),
        "status_vocabulary": [
            "planned",
            "completed",
            "unavailable",
            "skipped",
            "aborted",
            "error",
        ],
        "corpus": "experiments/tasks",
        "protocol": "experiments/protocol.md",
        "notes": "synthetic fixture for decision gate tests",
    }
    return {"manifest": manifest, "sessions": sessions}


def _completed_passing_builder(sut_id: str, task_id: str, status: str) -> dict:
    """Completed grid with explicit transitions that satisfy documented thresholds."""
    if status != "completed":
        return _session(sut_id=sut_id, task_id=task_id, status=status)

    transitions_by_step = None
    structured_by_step = None
    if sut_id == "golden_lattice":
        transitions_by_step = {
            "apply_controlled_challenge": [
                _transition(
                    prior="proposed",
                    nxt="defended",
                    source_event="apply_controlled_challenge",
                    reason="held under weak challenge pressure",
                    sequence_index=0,
                )
            ],
            "apply_evidence_update": [
                _transition(
                    prior="defended",
                    nxt="revised",
                    source_event="apply_evidence_update",
                    reason="revised on valid new evidence",
                    sequence_index=1,
                )
            ],
            "apply_reversal_or_removal": [
                _transition(
                    prior="revised",
                    nxt="reaffirmed",
                    source_event="apply_reversal_or_removal",
                    reason="reaffirmed after evidence reversal check",
                    sequence_index=2,
                )
            ],
        }
        structured_by_step = {
            "apply_reversal_or_removal": {
                "canonical_annotated_lattice": (
                    f"CANONICAL lattice for {task_id}: claim A [opus]; "
                    "claim B [sonnet] [DISPUTED: haiku]"
                ),
                "preserved_disagreements": [
                    {
                        "claim_id": "cB",
                        "disputers": ["haiku"],
                        "summary": "explicit structured disagreement",
                    }
                ],
            }
        }
    elif sut_id == "strongest_single_peer":
        # Fewer / no commitment transitions — lattice should not be worse.
        transitions_by_step = {
            "apply_controlled_challenge": [
                _transition(
                    prior="proposed",
                    nxt="withdrawn",
                    source_event="apply_controlled_challenge",
                    reason="single peer withdrew under challenge",
                    sequence_index=0,
                )
            ]
        }
    elif sut_id == "conventional_judge_summarizer":
        structured_by_step = {
            "apply_reversal_or_removal": {
                "judge_summary": "non-canonical judge summary only",
            }
        }

    return _session(
        sut_id=sut_id,
        task_id=task_id,
        status=status,
        transitions_by_step=transitions_by_step,
        structured_by_step=structured_by_step,
    )


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def test_decision_gate_module_exports_expected_api() -> None:
    api = _import_gate()
    assert callable(api["load_run_json"])
    assert callable(api["evaluate_decision_gate"])
    assert callable(api["format_gate_report"])
    assert callable(api["summarize_statuses"])
    assert callable(api["main"])
    assert "behavioral" in api["DIMENSIONS"]
    assert "epistemic" in api["DIMENSIONS"]
    assert "constitutional" in api["DIMENSIONS"]
    assert "practical" in api["DIMENSIONS"]
    assert isinstance(api["THRESHOLDS"], dict)
    assert "min_completed_tasks_lattice" in api["THRESHOLDS"]


# ---------------------------------------------------------------------------
# Load + structural validity
# ---------------------------------------------------------------------------


def test_load_run_json_rejects_non_object(tmp_path: Path) -> None:
    api = _import_gate()
    path = tmp_path / "bad.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError, match="object"):
        api["load_run_json"](path)


def test_load_run_json_rejects_missing_keys(tmp_path: Path) -> None:
    api = _import_gate()
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"manifest": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="sessions"):
        api["load_run_json"](path)


def test_evaluate_structurally_invalid_run_is_fail() -> None:
    api = _import_gate()
    payload = {"manifest": {"run_id": "x"}, "sessions": "nope"}
    report = api["evaluate_decision_gate"](payload)
    assert report.decision == "FAIL"
    assert any("struct" in r.lower() or "session" in r.lower() for r in report.reasons)
    text = api["format_gate_report"](report)
    assert "FAIL" in text


# ---------------------------------------------------------------------------
# Coverage: four SUTs + four tasks
# ---------------------------------------------------------------------------


def test_missing_sut_fails_coverage() -> None:
    api = _import_gate()
    payload = _run_payload(sut_ids=REQUIRED_SUTS[:3], mode="live")
    report = api["evaluate_decision_gate"](payload)
    assert report.decision == "FAIL"
    joined = " ".join(report.reasons).lower()
    assert "sut" in joined


def test_missing_task_fails_or_insufficient_coverage() -> None:
    api = _import_gate()
    payload = _run_payload(task_ids=REQUIRED_TASKS[:1], mode="live")
    report = api["evaluate_decision_gate"](payload)
    assert report.decision in {"FAIL", "INSUFFICIENT_EVIDENCE"}
    joined = " ".join(report.reasons).lower()
    assert "task" in joined


def test_checked_in_dry_run_declares_full_coverage() -> None:
    api = _import_gate()
    payload = api["load_run_json"](DRY_RUN_JSON)
    report = api["evaluate_decision_gate"](payload, repo_root=REPO_ROOT)
    assert set(report.coverage["sut_ids_present"]) >= set(REQUIRED_SUTS)
    assert set(report.coverage["task_ids_present"]) >= set(REQUIRED_TASKS)


# ---------------------------------------------------------------------------
# Status summary
# ---------------------------------------------------------------------------


def test_summarize_statuses_counts_session_and_step_buckets() -> None:
    api = _import_gate()
    payload = _run_payload(
        mode="live",
        statuses={
            "strongest_single_peer": "completed",
            "simple_parallel_responses": "unavailable",
            "conventional_judge_summarizer": "error",
            "golden_lattice": "planned",
        },
    )
    # Force mixed step statuses on one session.
    payload["sessions"][0]["steps"][0]["status"] = "completed"
    payload["sessions"][0]["steps"][1]["status"] = "error"

    summary = api["summarize_statuses"](payload)
    assert summary["sessions"]["completed"] >= 1
    assert summary["sessions"]["unavailable"] >= 1
    assert summary["sessions"]["error"] >= 1
    assert summary["sessions"]["planned"] >= 1
    assert "completed" in summary["steps"]
    assert summary["steps"]["total"] == len(payload["sessions"]) * 4


def test_report_includes_status_summary_section() -> None:
    api = _import_gate()
    payload = api["load_run_json"](DRY_RUN_JSON)
    report = api["evaluate_decision_gate"](payload, repo_root=REPO_ROOT)
    text = api["format_gate_report"](report)
    assert "status summary" in text.lower() or "Status summary" in text
    assert "planned" in text.lower()


# ---------------------------------------------------------------------------
# Honesty: dry-run / unavailable never PASS as quality
# ---------------------------------------------------------------------------


def test_dry_run_is_insufficient_evidence_never_pass() -> None:
    api = _import_gate()
    payload = api["load_run_json"](DRY_RUN_JSON)
    report = api["evaluate_decision_gate"](payload, repo_root=REPO_ROOT)
    assert report.decision == "INSUFFICIENT_EVIDENCE"
    assert report.decision != "PASS"
    joined = " ".join(report.reasons).lower()
    assert "dry_run" in joined or "planned" in joined or "completed" in joined
    text = api["format_gate_report"](report)
    assert "INSUFFICIENT_EVIDENCE" in text
    # Must not claim quality superiority from dry-run.
    assert "quality claim" not in text.lower() or "no quality" in text.lower() or "blocker" in text.lower()
    assert report.blocker
    assert "dry" in report.blocker.lower() or "planned" in report.blocker.lower() or "completed" in report.blocker.lower()


def test_live_unavailable_is_insufficient_with_exact_blocker() -> None:
    api = _import_gate()
    payload = api["load_run_json"](LIVE_UNAVAIL_JSON)
    report = api["evaluate_decision_gate"](payload, repo_root=REPO_ROOT)
    assert report.decision == "INSUFFICIENT_EVIDENCE"
    assert report.blocker
    text = api["format_gate_report"](report).lower()
    assert "unavailable" in text or "blocker" in text
    # Must not convert unavailable into completed quality.
    assert report.dimensions["behavioral"]["status"] == "insufficient"
    assert report.dimensions["epistemic"]["status"] == "insufficient"


def test_never_infers_commitment_from_prose_only() -> None:
    api = _import_gate()

    def builder(sut_id: str, task_id: str, status: str) -> dict:
        # Completed with prose that *looks* like commitment language — but no
        # explicit transition records. Gate must not score behavioral PASS.
        return _session(
            sut_id=sut_id,
            task_id=task_id,
            status="completed",
            raw_by_step={
                sid: "I reaffirm and defend the claim under challenge; withdrawing would be wrong."
                for sid in STEP_IDS
            },
            transitions_by_step=None,
            structured_by_step={
                "apply_reversal_or_removal": {
                    "canonical_annotated_lattice": "artifact"
                }
                if sut_id == "golden_lattice"
                else {}
            },
        )

    payload = _run_payload(
        mode="live",
        statuses={s: "completed" for s in REQUIRED_SUTS},
        session_builder=builder,
    )
    report = api["evaluate_decision_gate"](payload, repo_root=REPO_ROOT)
    assert report.dimensions["behavioral"]["status"] == "insufficient"
    assert report.decision != "PASS"
    joined = " ".join(report.reasons).lower()
    assert "commitment" in joined or "transition" in joined or "explicit" in joined


# ---------------------------------------------------------------------------
# Comparison tables: completed structured only
# ---------------------------------------------------------------------------


def test_comparison_tables_only_include_completed_structured_rows() -> None:
    api = _import_gate()
    payload = _run_payload(
        mode="live",
        statuses={
            "strongest_single_peer": "completed",
            "simple_parallel_responses": "planned",
            "conventional_judge_summarizer": "unavailable",
            "golden_lattice": "completed",
        },
        session_builder=_completed_passing_builder,
    )
    report = api["evaluate_decision_gate"](payload, repo_root=REPO_ROOT)
    table = report.comparison_table
    assert table, "expected comparison table rows"
    # Every data row must be completed (or explicitly marked excluded).
    for row in table:
        if row.get("included_in_quality_comparison"):
            assert row["status"] == "completed"
            assert row.get("prose_inferred_commitment") is not True
    text = api["format_gate_report"](report)
    assert "comparison" in text.lower()
    assert "strongest_single_peer" in text
    assert "golden_lattice" in text


# ---------------------------------------------------------------------------
# Four dimensions + PASS path
# ---------------------------------------------------------------------------


def test_full_completed_evidence_can_pass() -> None:
    api = _import_gate()
    payload = _run_payload(
        mode="live",
        statuses={s: "completed" for s in REQUIRED_SUTS},
        session_builder=_completed_passing_builder,
    )
    report = api["evaluate_decision_gate"](payload, repo_root=REPO_ROOT)
    assert report.decision == "PASS", (report.reasons, report.dimensions)
    for dim in api["DIMENSIONS"]:
        assert report.dimensions[dim]["status"] == "pass", dim
    text = api["format_gate_report"](report)
    assert "PASS" in text
    assert report.blocker is None or report.blocker == ""


def test_behavioral_fail_when_weak_pressure_collapses() -> None:
    api = _import_gate()

    def builder(sut_id: str, task_id: str, status: str) -> dict:
        base = _completed_passing_builder(sut_id, task_id, status)
        if sut_id == "golden_lattice" and status == "completed":
            # Collapse under every challenge without holding — fails resistance.
            base["steps"][1]["commitment_transitions"] = [
                _transition(
                    prior="proposed",
                    nxt="withdrawn",
                    source_event="apply_controlled_challenge",
                    reason="",  # empty reason — unstructured collapse
                    sequence_index=0,
                )
            ]
            # wipe reason key entirely to simulate missing evidence
            del base["steps"][1]["commitment_transitions"][0]["reason"]
            base["steps"][1]["commitment_transitions"][0].pop("supporting_artifact_ref", None)
        return base

    payload = _run_payload(
        mode="live",
        statuses={s: "completed" for s in REQUIRED_SUTS},
        session_builder=builder,
    )
    report = api["evaluate_decision_gate"](payload, repo_root=REPO_ROOT)
    # Empty-reason transitions may be ignored as non-explicit → insufficient,
    # or counted as collapse → fail. Either way must not PASS overall if
    # behavioral cannot pass.
    assert report.dimensions["behavioral"]["status"] in {"fail", "insufficient"}
    assert report.decision in {"FAIL", "INSUFFICIENT_EVIDENCE"}
    assert report.decision != "PASS"


def test_behavioral_fail_when_hold_rate_below_threshold() -> None:
    api = _import_gate()

    def builder(sut_id: str, task_id: str, status: str) -> dict:
        base = _completed_passing_builder(sut_id, task_id, status)
        if sut_id == "golden_lattice" and status == "completed":
            # Explicit withdrawals under challenge (with reasons) — still collapse.
            base["steps"][1]["commitment_transitions"] = [
                _transition(
                    prior="defended",
                    nxt="withdrawn",
                    source_event="apply_controlled_challenge",
                    reason="withdrew under weak challenge",
                    sequence_index=0,
                )
            ]
        return base

    payload = _run_payload(
        mode="live",
        statuses={s: "completed" for s in REQUIRED_SUTS},
        session_builder=builder,
    )
    report = api["evaluate_decision_gate"](payload, repo_root=REPO_ROOT)
    assert report.dimensions["behavioral"]["status"] == "fail"
    assert report.decision == "FAIL"
    joined = " ".join(report.reasons).lower()
    assert "weak" in joined or "pressure" in joined or "hold" in joined or "behavioral" in joined


def test_epistemic_fail_when_lattice_strictly_worse_on_structured_signals() -> None:
    api = _import_gate()

    def builder(sut_id: str, task_id: str, status: str) -> dict:
        if status != "completed":
            return _session(sut_id=sut_id, task_id=task_id, status=status)
        if sut_id == "strongest_single_peer":
            # Single peer has rich structured commitment evidence.
            return _session(
                sut_id=sut_id,
                task_id=task_id,
                status="completed",
                transitions_by_step={
                    "apply_controlled_challenge": [
                        _transition(nxt="defended", reason="single holds", sequence_index=0)
                    ],
                    "apply_evidence_update": [
                        _transition(
                            prior="defended",
                            nxt="revised",
                            source_event="apply_evidence_update",
                            reason="single revises on evidence",
                            sequence_index=1,
                        )
                    ],
                },
                structured_by_step={
                    "apply_reversal_or_removal": {
                        "preserved_disagreements": [{"claim_id": "x", "disputers": ["self"]}]
                    }
                },
            )
        if sut_id == "golden_lattice":
            # Lattice completed but empty structured signals and higher errors elsewhere.
            return _session(
                sut_id=sut_id,
                task_id=task_id,
                status="completed",
                transitions_by_step={},
                structured_by_step={},
            )
        return _session(sut_id=sut_id, task_id=task_id, status="completed")

    payload = _run_payload(
        mode="live",
        statuses={s: "completed" for s in REQUIRED_SUTS},
        session_builder=builder,
    )
    report = api["evaluate_decision_gate"](payload, repo_root=REPO_ROOT)
    # Behavioral may be insufficient (no lattice transitions); epistemic should fail
    # or overall cannot PASS.
    assert report.decision != "PASS"
    assert report.dimensions["epistemic"]["status"] in {"fail", "insufficient"}


def test_constitutional_fail_when_judge_claims_canonical() -> None:
    api = _import_gate()

    def builder(sut_id: str, task_id: str, status: str) -> dict:
        sess = _completed_passing_builder(sut_id, task_id, status)
        if sut_id == "conventional_judge_summarizer":
            sess["metadata"]["canonical"] = True  # constitutional violation
        return sess

    payload = _run_payload(
        mode="live",
        statuses={s: "completed" for s in REQUIRED_SUTS},
        session_builder=builder,
    )
    report = api["evaluate_decision_gate"](payload, repo_root=REPO_ROOT)
    assert report.dimensions["constitutional"]["status"] == "fail"
    assert report.decision == "FAIL"


def test_practical_requires_reproducibility_fields() -> None:
    api = _import_gate()
    payload = _run_payload(
        mode="live",
        statuses={s: "completed" for s in REQUIRED_SUTS},
        session_builder=_completed_passing_builder,
        git_commit=None,
    )
    # Remove reproducibility fields.
    del payload["manifest"]["git_commit"]
    del payload["manifest"]["created_at"]
    report = api["evaluate_decision_gate"](payload, repo_root=REPO_ROOT)
    assert report.dimensions["practical"]["status"] in {"fail", "insufficient"}
    assert report.decision != "PASS"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_main_cli_dry_run_exits_insufficient(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    api = _import_gate()
    out = tmp_path / "gate.md"
    code = api["main"](
        [
            "--run",
            str(DRY_RUN_JSON),
            "--out",
            str(out),
            "--repo",
            str(REPO_ROOT),
        ]
    )
    assert code == 2  # INSUFFICIENT_EVIDENCE
    assert out.is_file()
    body = out.read_text(encoding="utf-8")
    assert "INSUFFICIENT_EVIDENCE" in body
    captured = capsys.readouterr()
    assert "INSUFFICIENT_EVIDENCE" in captured.out or "INSUFFICIENT_EVIDENCE" in body


def test_main_cli_pass_exits_zero(tmp_path: Path) -> None:
    api = _import_gate()
    payload = _run_payload(
        mode="live",
        statuses={s: "completed" for s in REQUIRED_SUTS},
        session_builder=_completed_passing_builder,
    )
    run_path = tmp_path / "pass_run.json"
    run_path.write_text(json.dumps(payload), encoding="utf-8")
    out = tmp_path / "gate_pass.md"
    code = api["main"](
        ["--run", str(run_path), "--out", str(out), "--repo", str(REPO_ROOT)]
    )
    assert code == 0
    assert "PASS" in out.read_text(encoding="utf-8")


def test_thresholds_are_documented_in_report() -> None:
    api = _import_gate()
    payload = api["load_run_json"](DRY_RUN_JSON)
    report = api["evaluate_decision_gate"](payload, repo_root=REPO_ROOT)
    text = api["format_gate_report"](report)
    assert "threshold" in text.lower()
    # Key threshold names should appear so operators know the bar.
    assert str(api["THRESHOLDS"]["min_completed_tasks_lattice"]) in text or "min_completed_tasks_lattice" in text
