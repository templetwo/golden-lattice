"""Phase 2 Task 9 — human-readable case report generator contract tests.

Strict TDD surface for:
- five required report sections with clear labels
- explicit commitment transitions only (never inferred from prose)
- honest planned/unavailable representation (no fabricated answers)
- baseline comparison across all four required SUTs
- no hidden generative judge

No network calls. No synthesis path changes.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path

import pytest

from experiments.baselines import REQUIRED_SUT_IDS

REPO_ROOT = Path(__file__).resolve().parents[2]
DRY_RUN_JSON = (
    REPO_ROOT / "experiments" / "baselines" / "runs" / "task8_dryrun_all.json"
)
TASK_DESIGN = "gl.longitudinal.design_critique.v1"

# Section markers the report must expose (human-readable headings).
SECTION_CANONICAL = re.compile(
    r"^#+\s+.*canonical.*annotated.*lattice", re.I | re.M
)
SECTION_INTERPRETATION = re.compile(
    r"^#+\s+.*(?:reader[- ]facing|non-canonical).*interpret", re.I | re.M
)
SECTION_COMMITMENT = re.compile(r"^#+\s+.*commitment\s+timeline", re.I | re.M)
SECTION_DISAGREEMENT = re.compile(
    r"^#+\s+.*(?:preserved\s+)?disagreement", re.I | re.M
)
SECTION_BASELINE = re.compile(r"^#+\s+.*baseline\s+comparison", re.I | re.M)


def _require_generator():
    """Import under test — fails RED until the module exists."""
    from experiments.reports.generator import (  # noqa: WPS433
        generate_case_report,
        load_run_json,
        write_case_report,
    )

    return generate_case_report, load_run_json, write_case_report


def _minimal_run_payload(
    *,
    include_canonical: bool = False,
    include_transitions: bool = False,
    include_disagreements: bool = False,
    include_interpretation: bool = False,
    statuses: dict[str, str] | None = None,
) -> dict:
    """Synthetic Task-8-shaped run JSON for one task × four SUTs."""
    statuses = statuses or {sid: "planned" for sid in REQUIRED_SUT_IDS}
    task_id = TASK_DESIGN
    sessions = []
    for sut_id in REQUIRED_SUT_IDS:
        status = statuses.get(sut_id, "planned")
        steps = []
        for i, step_id in enumerate(
            (
                "present_initial_claim",
                "apply_controlled_challenge",
                "apply_evidence_update",
                "apply_reversal_or_removal",
            )
        ):
            transitions = None
            structured = None
            raw = None
            if status == "completed":
                raw = f"[{sut_id}] step {step_id} model prose (not a commitment)"
                structured = {}
                if sut_id == "golden_lattice" and include_canonical and i == 3:
                    # Final step carries the annotated lattice artifact.
                    structured["canonical_annotated_lattice"] = (
                        "CANONICAL_LATTICE_ARTIFACT_v1: claim A [opus]; "
                        "claim B [sonnet] [DISPUTED: haiku]"
                    )
                    raw = structured["canonical_annotated_lattice"]
                if include_transitions and sut_id == "golden_lattice" and i == 1:
                    transitions = [
                        {
                            "claim_id": "c1",
                            "source_model": "opus",
                            "prior_state": "proposed",
                            "next_state": "challenged",
                            "source_event": "dc.v1.controlled_challenge",
                            "reason": "explicit structured transition from observer",
                            "sequence_index": 0,
                            "occurred_at": "2026-08-06T12:00:00+00:00",
                        }
                    ]
                if include_disagreements and sut_id == "golden_lattice" and i == 2:
                    structured["preserved_disagreements"] = [
                        {
                            "claim_id": "c1",
                            "disputers": ["haiku", "sonnet"],
                            "summary": "whether offset/limit remains default",
                        }
                    ]
            step = {
                "step_id": step_id,
                "perturbation_id": f"dc.v1.{step_id}",
                "status": status if status != "error" else ("error" if i == 0 else "aborted"),
                "prompt_bundle": {
                    "step_id": step_id,
                    "task_id": task_id,
                    "rendered_prompt": f"prompt for {step_id}",
                },
                "raw_output": raw,
                "latency_ms": 12.5 if status == "completed" else None,
                "cost_usd": 0.01 if status == "completed" else None,
                "reason": None if status == "completed" else f"{status}: honest record",
                "structured": structured,
                "commitment_transitions": transitions,
            }
            steps.append(step)

        session_status = status
        if status == "error":
            session_status = "aborted"
        session = {
            "session_id": f"exp_test_{sut_id}_{task_id}",
            "task_id": task_id,
            "sut_id": sut_id,
            "status": session_status,
            "steps": steps,
            "metadata": {
                "baseline": sut_id,
                "canonical": sut_id == "golden_lattice",
                "optional": sut_id == "conventional_judge_summarizer",
            },
            "notes": f"fixture notes for {sut_id}",
        }
        if status == "unavailable":
            session["unavailable_reason"] = "provider not configured (fixture)"
        if include_interpretation and sut_id == "golden_lattice":
            session["reader_facing_interpretation"] = (
                "Operator note: design held under challenge (NON-CANONICAL human read)."
            )
        if include_disagreements and sut_id == "golden_lattice":
            # Also allow session-level supply path.
            session["preserved_disagreements"] = [
                {
                    "claim_id": "c1",
                    "disputers": ["haiku", "sonnet"],
                    "summary": "whether offset/limit remains default",
                }
            ]
        sessions.append(session)

    return {
        "manifest": {
            "run_id": "fixture_task9",
            "mode": "live" if any(s == "completed" for s in statuses.values()) else "dry_run",
            "created_at": "2026-08-06T00:00:00+00:00",
            "git_commit": "deadbeef",
            "task_ids": [task_id],
            "sut_ids": list(REQUIRED_SUT_IDS),
            "session_count": len(sessions),
            "status_vocabulary": [
                "planned",
                "completed",
                "unavailable",
                "skipped",
                "aborted",
                "error",
            ],
            "notes": "fixture",
        },
        "sessions": sessions,
    }


# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------


def test_generator_module_exports_callable_and_loader() -> None:
    generate_case_report, load_run_json, write_case_report = _require_generator()
    assert callable(generate_case_report)
    assert callable(load_run_json)
    assert callable(write_case_report)


# ---------------------------------------------------------------------------
# Required sections + dry-run honesty
# ---------------------------------------------------------------------------


def test_report_contains_five_required_sections() -> None:
    generate_case_report, _, _ = _require_generator()
    report = generate_case_report(_minimal_run_payload(), task_id=TASK_DESIGN)
    assert SECTION_CANONICAL.search(report), report
    assert SECTION_INTERPRETATION.search(report), report
    assert SECTION_COMMITMENT.search(report), report
    assert SECTION_DISAGREEMENT.search(report), report
    assert SECTION_BASELINE.search(report), report


def test_dry_run_report_does_not_fabricate_answers_or_commitments() -> None:
    generate_case_report, _, _ = _require_generator()
    payload = _minimal_run_payload(statuses={s: "planned" for s in REQUIRED_SUT_IDS})
    report = generate_case_report(payload, task_id=TASK_DESIGN)

    # All four SUTs appear as planned in the comparison table.
    for sut_id in REQUIRED_SUT_IDS:
        assert sut_id in report
    assert re.search(r"planned", report, re.I)

    # Must not invent model answers from empty raw_output.
    assert not re.search(r"\bcompleted\b", report, re.I) or "planned" in report.lower()
    # No fake lattice body.
    assert "CANONICAL_LATTICE_ARTIFACT" not in report
    # No commitment rows fabricated from prose.
    assert "proposed" not in report.lower() or "unavailable" in report.lower()
    # Explicit unavailable markers for missing structured fields.
    assert re.search(r"unavailable|not\s+present|none\s+supplied", report, re.I)


def test_unavailable_sessions_are_represented_honestly() -> None:
    generate_case_report, _, _ = _require_generator()
    payload = _minimal_run_payload(
        statuses={s: "unavailable" for s in REQUIRED_SUT_IDS}
    )
    report = generate_case_report(payload, task_id=TASK_DESIGN)
    assert re.search(r"unavailable", report, re.I)
    assert "provider not configured" in report
    # Must not show fabricated raw answers.
    assert "model prose" not in report


# ---------------------------------------------------------------------------
# Section content contracts
# ---------------------------------------------------------------------------


def test_canonical_section_labeled_and_includes_artifact_when_present() -> None:
    generate_case_report, _, _ = _require_generator()
    payload = _minimal_run_payload(
        include_canonical=True,
        statuses={s: "completed" for s in REQUIRED_SUT_IDS},
    )
    report = generate_case_report(payload, task_id=TASK_DESIGN)
    # Section must label the artifact as canonical.
    canon_match = SECTION_CANONICAL.search(report)
    assert canon_match
    # Body after heading contains the artifact text.
    assert "CANONICAL_LATTICE_ARTIFACT_v1" in report
    assert re.search(r"\bcanonical\b", report, re.I)


def test_reader_facing_interpretation_labeled_non_canonical() -> None:
    generate_case_report, _, _ = _require_generator()
    payload = _minimal_run_payload(
        include_interpretation=True,
        include_canonical=True,
        statuses={s: "completed" for s in REQUIRED_SUT_IDS},
    )
    report = generate_case_report(payload, task_id=TASK_DESIGN)
    assert SECTION_INTERPRETATION.search(report)
    assert "Operator note: design held under challenge" in report
    # Must be labeled non-canonical near the interpretation content.
    assert re.search(r"non-canonical", report, re.I)


def test_commitment_timeline_uses_explicit_records_only() -> None:
    generate_case_report, _, _ = _require_generator()
    payload = _minimal_run_payload(
        include_transitions=True,
        statuses={s: "completed" for s in REQUIRED_SUT_IDS},
    )
    # Poison raw_output with commitment-like prose that must NOT be scraped.
    for session in payload["sessions"]:
        for step in session["steps"]:
            if step.get("raw_output"):
                step["raw_output"] = (
                    step["raw_output"]
                    + "\nI changed my mind from proposed to withdrawn. (prose trap)"
                )

    report = generate_case_report(payload, task_id=TASK_DESIGN)
    assert "c1" in report
    assert re.search(r"proposed\s*→\s*challenged|proposed.*challenged", report, re.I)
    assert "dc.v1.controlled_challenge" in report
    # Prose trap must not create extra transitions; "withdrawn" only if explicit.
    # Our explicit record goes to challenged, not withdrawn.
    timeline = report.split("Commitment")[1].split("##")[0] if "Commitment" in report else report
    # Count explicit next_state appearances in commitment section — withdrawn must not appear.
    commit_section = _section_body(report, SECTION_COMMITMENT)
    assert "withdrawn" not in commit_section.lower()
    assert "prose trap" not in commit_section.lower()


def test_commitment_timeline_empty_when_no_explicit_records() -> None:
    generate_case_report, _, _ = _require_generator()
    payload = _minimal_run_payload(
        statuses={s: "completed" for s in REQUIRED_SUT_IDS},
        include_transitions=False,
    )
    # Even with completed prose, no transitions → explicit unavailable/empty.
    report = generate_case_report(payload, task_id=TASK_DESIGN)
    body = _section_body(report, SECTION_COMMITMENT)
    assert re.search(r"unavailable|none|no explicit|empty|not supplied", body, re.I)


def test_disagreement_list_when_supplied_else_unavailable() -> None:
    generate_case_report, _, _ = _require_generator()

    with_d = generate_case_report(
        _minimal_run_payload(
            include_disagreements=True,
            statuses={s: "completed" for s in REQUIRED_SUT_IDS},
        ),
        task_id=TASK_DESIGN,
    )
    body_with = _section_body(with_d, SECTION_DISAGREEMENT)
    assert "offset/limit" in body_with
    assert "haiku" in body_with

    without = generate_case_report(
        _minimal_run_payload(
            include_disagreements=False,
            statuses={s: "completed" for s in REQUIRED_SUT_IDS},
        ),
        task_id=TASK_DESIGN,
    )
    body_without = _section_body(without, SECTION_DISAGREEMENT)
    assert re.search(r"unavailable|not supplied|none supplied", body_without, re.I)


def test_baseline_comparison_table_covers_all_four_suts() -> None:
    generate_case_report, _, _ = _require_generator()
    payload = _minimal_run_payload(
        statuses={
            "strongest_single_peer": "planned",
            "simple_parallel_responses": "unavailable",
            "conventional_judge_summarizer": "error",
            "golden_lattice": "completed",
        }
    )
    # Fix error session shape: first step error, rest aborted already in fixture.
    report = generate_case_report(payload, task_id=TASK_DESIGN)
    body = _section_body(report, SECTION_BASELINE)
    for sut_id in REQUIRED_SUT_IDS:
        assert sut_id in body
    assert re.search(r"planned", body, re.I)
    assert re.search(r"unavailable", body, re.I)
    assert re.search(r"completed", body, re.I)
    # conventional judge path should surface aborted/error honestly
    assert re.search(r"aborted|error", body, re.I)


# ---------------------------------------------------------------------------
# Scope: one run vs one task; load/write helpers
# ---------------------------------------------------------------------------


def test_task_filter_limits_sessions_in_report() -> None:
    generate_case_report, _, _ = _require_generator()
    payload = _minimal_run_payload()
    other = deepcopy(payload["sessions"][0])
    other["task_id"] = "gl.longitudinal.ambiguous_evidence_synthesis.v1"
    other["session_id"] = "other_task_session"
    payload["sessions"].append(other)
    payload["manifest"]["task_ids"] = [
        TASK_DESIGN,
        "gl.longitudinal.ambiguous_evidence_synthesis.v1",
    ]

    report = generate_case_report(payload, task_id=TASK_DESIGN)
    assert TASK_DESIGN in report
    # Other task must not dominate; session id from other task absent.
    assert "other_task_session" not in report


def test_load_and_write_roundtrip(tmp_path: Path) -> None:
    generate_case_report, load_run_json, write_case_report = _require_generator()
    src = tmp_path / "run.json"
    payload = _minimal_run_payload()
    src.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_run_json(src)
    assert loaded["manifest"]["run_id"] == "fixture_task9"

    out = write_case_report(loaded, tmp_path / "report.md", task_id=TASK_DESIGN)
    assert out.is_file()
    text = out.read_text(encoding="utf-8")
    assert SECTION_BASELINE.search(text)


def test_report_surfaces_grounding_condition() -> None:
    generate_case_report, _, _ = _require_generator()
    payload = _minimal_run_payload()
    payload["manifest"]["grounding_mode"] = "tavily"

    report = generate_case_report(payload, task_id=TASK_DESIGN)

    assert "- grounding_mode: `tavily`" in report


@pytest.mark.skipif(not DRY_RUN_JSON.is_file(), reason="Task 8 dry-run artifact missing")
def test_exercise_against_task8_dryrun_json() -> None:
    """Integration: real Task 8 dry-run artifact remains legible and honest."""
    generate_case_report, load_run_json, _ = _require_generator()
    payload = load_run_json(DRY_RUN_JSON)
    report = generate_case_report(payload, task_id=TASK_DESIGN)

    assert payload["manifest"]["run_id"] in report
    assert TASK_DESIGN in report
    for sut_id in REQUIRED_SUT_IDS:
        assert sut_id in report
    assert re.search(r"planned", report, re.I)
    # Dry-run must not invent completed lattice answers.
    assert "CANONICAL_LATTICE_ARTIFACT" not in report
    for heading_re in (
        SECTION_CANONICAL,
        SECTION_INTERPRETATION,
        SECTION_COMMITMENT,
        SECTION_DISAGREEMENT,
        SECTION_BASELINE,
    ):
        assert heading_re.search(report), report
    # No prose-inferred commitments from prompt bundles.
    commit_body = _section_body(report, SECTION_COMMITMENT)
    assert re.search(r"unavailable|none|no explicit|empty|not supplied", commit_body, re.I)


def test_never_invokes_hidden_judge(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report generation is pure formatting — no model/judge client import side effects."""
    generate_case_report, _, _ = _require_generator()

    def boom(*_a, **_k):  # pragma: no cover - fail if called
        raise AssertionError("report generator must not call external clients")

    # If the generator accidentally imports/calls these, fail.
    import builtins

    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        blocked = (
            "anthropic",
            "openai",
            "httpx",
            "requests",
        )
        if name in blocked or name.split(".")[0] in blocked:
            raise AssertionError(f"hidden client import blocked: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    report = generate_case_report(_minimal_run_payload(), task_id=TASK_DESIGN)
    assert SECTION_BASELINE.search(report)
    # silence unused
    _ = boom


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _section_body(report: str, heading_re: re.Pattern[str]) -> str:
    m = heading_re.search(report)
    assert m, f"heading not found: {heading_re.pattern}\n{report}"
    start = m.end()
    rest = report[start:]
    # Next markdown heading at same or higher level (## or #)
    nxt = re.search(r"\n#{1,3}\s+\S", rest)
    if nxt:
        return rest[: nxt.start()]
    return rest
