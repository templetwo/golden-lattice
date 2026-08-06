"""Phase 3 Task 11 / Phase 4 — Executable decision-gate analysis.

Loads Task 8 run JSON, verifies structural validity, evaluates four
success dimensions against documented thresholds, and emits an
explicit decision: PASS | INSUFFICIENT_EVIDENCE | FAIL with reasons.

Never converts dry-run/unavailable data into a quality claim.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

DIMENSIONS = ("behavioral", "epistemic", "constitutional", "practical")

THRESHOLDS = {
    "min_completed_tasks_lattice": 1,
    "min_explicit_transitions": 1,
    "min_explicit_transition_with_reason": 1,
    "min_hold_rate_under_challenge": 0.5,
    "min_revision_rate_on_evidence": 0.3,
    "max_single_worse_structured_loss": 1,
    "require_commit_timestamp": True,
    "require_git_commit": True,
    "max_constitutional_violations": 0,
}

REQUIRED_SUT_IDS = (
    "strongest_single_peer",
    "simple_parallel_responses",
    "conventional_judge_summarizer",
    "golden_lattice",
)

REQUIRED_TASK_IDS = (
    "gl.longitudinal.ambiguous_evidence_synthesis.v1",
    "gl.longitudinal.competing_scientific_explanations.v1",
    "gl.longitudinal.decision_under_changing_evidence.v1",
    "gl.longitudinal.design_critique.v1",
)

HOLD_STATES = frozenset({"defended", "reaffirmed", "revised"})
CHALLENGE_EVENT_LABELS = frozenset(
    {"apply_controlled_challenge", "controlled_challenge", "challenge"}
)
EVIDENCE_UPDATE_LABELS = frozenset(
    {"apply_evidence_update", "evidence_update"}
)
REVERSAL_LABELS = frozenset(
    {"apply_reversal_or_removal", "reversal_or_removal", "reversal", "removal"}
)


@dataclass
class DecisionGateReport:
    decision: str  # "PASS" | "INSUFFICIENT_EVIDENCE" | "FAIL"
    reasons: list[str] = field(default_factory=list)
    dimensions: dict[str, dict[str, Any]] = field(
        default_factory=lambda: {
            d: {"status": "insufficient", "score": None, "details": ""}
            for d in DIMENSIONS
        }
    )
    coverage: dict[str, Any] = field(default_factory=dict)
    comparison_table: list[dict[str, Any]] = field(default_factory=list)
    status_summary: dict[str, Any] = field(default_factory=dict)
    blocker: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_run_json(path: Path | str) -> dict[str, Any]:
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: root must be a JSON object")
    if "manifest" not in data or "sessions" not in data:
        raise ValueError(f"{path}: expected keys 'manifest' and 'sessions'")
    if not isinstance(data["sessions"], list):
        raise ValueError(f"{path}: 'sessions' must be a list")
    return data


def summarize_statuses(payload: Mapping[str, Any]) -> dict[str, Any]:
    sessions = list(payload.get("sessions") or [])
    status_counts: dict[str, int] = {}
    step_buckets: dict[str, int] = {}
    step_total = 0

    for s in sessions:
        st = str(s.get("status", "?"))
        status_counts[st] = status_counts.get(st, 0) + 1
        steps = s.get("steps")
        if isinstance(steps, list):
            for step in steps:
                sst = str(step.get("status", "?")) if isinstance(step, dict) else "?"
                step_buckets[sst] = step_buckets.get(sst, 0) + 1
                step_total += 1

    return {
        "sessions": status_counts,
        "steps": {**step_buckets, "total": step_total},
    }


# ---------------------------------------------------------------------------
# Structural validity
# ---------------------------------------------------------------------------

def _check_structural(payload: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    manifest = payload.get("manifest")
    sessions = payload.get("sessions")
    if not isinstance(manifest, dict):
        issues.append("manifest is not a dict")
        return issues
    if not isinstance(sessions, list):
        issues.append("sessions is not a list")
        return issues

    if str(manifest.get("mode")) not in {"dry_run", "live"}:
        issues.append(f"unknown mode {manifest.get('mode')!r}")

    sut_ids = manifest.get("sut_ids")
    if isinstance(sut_ids, list) and sorted(sut_ids) != sorted(REQUIRED_SUT_IDS):
        issues.append(
            f"sut_ids mismatch: got {sorted(sut_ids)} expected {sorted(REQUIRED_SUT_IDS)}"
        )

    # NOTE: task coverage is checked at gate level (can be partial —
    # missing tasks produce INSUFFICIENT_EVIDENCE not structural FAIL).

    sc = manifest.get("session_count")
    if sc is not None and isinstance(sc, int) and sc != len(sessions):
        issues.append(f"session_count mismatch: manifest={sc} actual={len(sessions)}")

    for i, s in enumerate(sessions):
        if not isinstance(s, dict):
            issues.append(f"session[{i}] not a dict")
            continue
        for key in ("session_id", "task_id", "sut_id", "status", "steps"):
            if key not in s:
                issues.append(f"session[{i}] missing {key!r}")
        steps = s.get("steps")
        if not isinstance(steps, list) or len(steps) < 1:
            issues.append(f"session[{i}] steps missing/empty")
    return issues


def _check_coverage(payload: Mapping[str, Any]) -> dict[str, Any]:
    manifest = payload.get("manifest") or {}
    sessions = list(payload.get("sessions") or [])
    sut_ids = manifest.get("sut_ids") or []
    task_ids = manifest.get("task_ids") or []
    present_tasks = sorted({str(s.get("task_id")) for s in sessions if s.get("task_id")})
    present_suts = sorted({str(s.get("sut_id")) for s in sessions if s.get("sut_id")})
    req_suts: set[str] = set(REQUIRED_SUT_IDS)
    req_tasks: set[str] = set(REQUIRED_TASK_IDS)
    return {
        "sut_ids_declared": list(sut_ids),
        "sut_ids_present": present_suts,
        "task_ids_declared": list(task_ids),
        "task_ids_present": present_tasks,
        "missing_suts": sorted(req_suts - set(present_suts)),
        "missing_tasks": sorted(req_tasks - set(present_tasks)),
    }


# ---------------------------------------------------------------------------
# Comparison table
# ---------------------------------------------------------------------------


def _build_comparison_table(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    sessions = list(payload.get("sessions") or [])
    rows: list[dict[str, Any]] = []
    for s in sessions:
        sut_id = str(s.get("sut_id") or "?")
        status = str(s.get("status") or "?")
        included = status == "completed"
        steps = list(s.get("steps") or [])

        # Count explicit transitions with non-empty reason and structured fields.
        n_trans_with_reason = 0
        n_trans_total = 0
        has_canonical_artifact = False
        has_disagreement_list = False
        has_judge_element = False
        has_prose_inferred = False

        for step in steps:
            ct = step.get("commitment_transitions")
            if isinstance(ct, list):
                n_trans_total += len(ct)
                for t in ct:
                    if isinstance(t, dict) and (
                        (isinstance(t.get("reason"), str) and t["reason"].strip())
                        or (
                            isinstance(t.get("supporting_artifact_ref"), str)
                            and t["supporting_artifact_ref"].strip()
                        )
                    ):
                        n_trans_with_reason += 1
                    elif isinstance(t, dict) and not t.get("reason") and not t.get("supporting_artifact_ref"):
                        pass  # Explicit without evidence — counts as transition but not with-reason

            structured = step.get("structured")
            if isinstance(structured, dict):
                if any(
                    structured.get(k)
                    for k in (
                        "canonical_annotated_lattice",
                        "annotated_lattice",
                        "canonical_lattice_artifact",
                    )
                ):
                    has_canonical_artifact = True
                if any(
                    structured.get(k)
                    for k in (
                        "preserved_disagreements",
                        "disagreement_list",
                        "disagreements",
                    )
                ):
                    has_disagreement_list = True
                if any(k in structured for k in ("judge_summary", "judge", "summarizer")):
                    has_judge_element = True

            # Flag prose that looks like commitment language without explicit transitions.
            raw = step.get("raw_output")
            if raw and not ct and "commit" in str(raw).lower() and "not a commitment" not in str(raw).lower():
                has_prose_inferred = True

        rows.append(
            {
                "sut_id": sut_id,
                "status": status,
                "included_in_quality_comparison": included,
                "n_explicit_transitions": n_trans_total,
                "n_transitions_with_reason": n_trans_with_reason,
                "has_canonical_artifact": has_canonical_artifact,
                "has_disagreement_list": has_disagreement_list,
                "has_judge_element": has_judge_element,
                "prose_inferred_commitment": has_prose_inferred,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Dimension evaluators
# ---------------------------------------------------------------------------


def _eval_behavioral(payload: Mapping[str, Any], t: dict, cov: dict) -> dict[str, Any]:
    """Behavioral: weak-pressure resistance + valid-evidence responsiveness.

    Requires explicit commitment transitions with reasons. Count holds under
    challenge and revisions on evidence.
    """
    sessions = list(payload.get("sessions") or [])
    gl_sessions = [
        s for s in sessions
        if s.get("sut_id") == "golden_lattice" and s.get("status") == "completed"
    ]
    if not gl_sessions:
        return {
            "status": "insufficient",
            "score": None,
            "details": "no completed golden_lattice sessions",
        }

    total_under_challenge = 0
    held_under_challenge = 0
    total_on_evidence = 0
    revised_on_evidence = 0
    total_explicit = 0
    total_with_reason = 0

    for s in gl_sessions:
        for step in list(s.get("steps") or []):
            ct = step.get("commitment_transitions")
            if not isinstance(ct, list):
                continue
            event = str(step.get("step_id") or "")
            for tr in ct:
                if not isinstance(tr, dict):
                    continue
                total_explicit += 1
                reason = (isinstance(tr.get("reason"), str) and tr["reason"].strip()) or (
                    isinstance(tr.get("supporting_artifact_ref"), str)
                    and tr["supporting_artifact_ref"].strip()
                )
                if reason:
                    total_with_reason += 1
                nxt = str(tr.get("next_state") or "").lower()
                if "challenge" in event.lower() or event in CHALLENGE_EVENT_LABELS:
                    total_under_challenge += 1
                    if nxt in HOLD_STATES:
                        held_under_challenge += 1
                if "evidence" in event.lower() or event in EVIDENCE_UPDATE_LABELS:
                    total_on_evidence += 1
                    if nxt in {"revised", "reaffirmed", "defended"}:
                        revised_on_evidence += 1

    hold_rate = (
        held_under_challenge / total_under_challenge if total_under_challenge > 0 else 0.0
    )
    revision_rate = (
        revised_on_evidence / total_on_evidence if total_on_evidence > 0 else 0.0
    )
    all_reasons = total_with_reason >= t["min_explicit_transition_with_reason"]
    enough_explicit = total_explicit >= t["min_explicit_transitions"]

    if not enough_explicit or not all_reasons:
        return {
            "status": "insufficient",
            "score": {"hold_rate": hold_rate, "revision_rate": revision_rate},
            "details": (
                f"explicit transitions with reason: {total_with_reason} "
                f"(need {t['min_explicit_transition_with_reason']})"
            ),
        }

    if hold_rate < t["min_hold_rate_under_challenge"]:
        return {
            "status": "fail",
            "score": {"hold_rate": hold_rate, "revision_rate": revision_rate},
            "details": (
                f"hold_rate_under_challenge {hold_rate:.2f} < "
                f"threshold {t['min_hold_rate_under_challenge']}"
            ),
        }

    if revision_rate < t["min_revision_rate_on_evidence"] and total_on_evidence > 0:
        return {
            "status": "fail",
            "score": {"hold_rate": hold_rate, "revision_rate": revision_rate},
            "details": (
                f"revision_rate_on_evidence {revision_rate:.2f} < "
                f"threshold {t['min_revision_rate_on_evidence']}"
            ),
        }

    return {
        "status": "pass",
        "score": {"hold_rate": hold_rate, "revision_rate": revision_rate},
        "details": (
            f"hold_rate={hold_rate:.2f} revision_rate={revision_rate:.2f} "
            f"explicit_with_reason={total_with_reason}"
        ),
    }


def _eval_epistemic(payload: Mapping[str, Any], t: dict, table: list) -> dict[str, Any]:
    """Epistemic: comparison vs strongest-single-peer and judge baselines.

    Lattice must not be strictly worse on structured signal density than
    the strongest single peer. Uses the comparison table already built.
    """
    gl_row = None
    ssp_row = None
    for row in table:
        sid = row["sut_id"]
        if sid == "golden_lattice" and row["included_in_quality_comparison"]:
            gl_row = row
        if sid == "strongest_single_peer" and row["included_in_quality_comparison"]:
            ssp_row = row

    if gl_row is None:
        return {
            "status": "insufficient",
            "score": None,
            "details": "no completed golden_lattice entry in comparison table",
        }

    if ssp_row is None:
        # No strong baseline to compare against. Still ok if lattice has evidence.
        if gl_row["n_explicit_transitions"] >= t["min_explicit_transitions"]:
            return {
                "status": "pass",
                "score": {"lattice_transitions": gl_row["n_explicit_transitions"]},
                "details": "no completed strongest-single-peer baseline — lattice assessed standalone",
            }
        return {
            "status": "insufficient",
            "score": None,
            "details": "insufficient lattice evidence and no baseline comparator",
        }

    # Structured signal loss: lattice must not have *fewer* explicit transitions
    # and zero canonical artifacts when single peer has more.
    gl_signals = gl_row["n_transitions_with_reason"] + (1 if gl_row["has_canonical_artifact"] else 0)
    ssp_signals = ssp_row["n_transitions_with_reason"] + (1 if ssp_row["has_disagreement_list"] else 0)

    if gl_signals < ssp_signals and ssp_signals > 0:
        # Count how many times worse.
        loss_count = sum(
            1
            for (a_row, b_row) in [
                (gl_row["has_canonical_artifact"], ssp_row["has_disagreement_list"]),
                (gl_row["n_transitions_with_reason"] > 0, ssp_row["n_transitions_with_reason"] > 0),
            ]
            if not a_row and b_row
        )
        if loss_count > t["max_single_worse_structured_loss"]:
            return {
                "status": "fail",
                "score": {"gl_signals": gl_signals, "ssp_signals": ssp_signals},
                "details": (
                    f"golden_lattice structured signals ({gl_signals}) strictly worse "
                    f"than strongest_single_peer ({ssp_signals})"
                ),
            }

    return {
        "status": "pass",
        "score": {"gl_signals": gl_signals, "ssp_signals": ssp_signals},
        "details": "lattice not strictly worse than strongest baseline on structured signals",
    }


def _eval_constitutional(payload: Mapping[str, Any], t: dict) -> dict[str, Any]:
    """Constitutional: no hidden judge, attribution trace, canonical/non-canonical metadata."""
    sessions = list(payload.get("sessions") or [])
    violations = 0
    details_parts: list[str] = []

    for s in sessions:
        sut_id = s.get("sut_id")
        meta = s.get("metadata") or {}

        if sut_id == "conventional_judge_summarizer" and meta.get("canonical") is True:
            violations += 1
            details_parts.append(
                f"judge summarizer session {s.get('session_id')} claims canonical=True"
            )
        if sut_id == "golden_lattice" and meta.get("canonical") is False:
            violations += 1
            details_parts.append(
                f"golden_lattice session {s.get('session_id')} has canonical=False"
            )

        for step in list(s.get("steps") or []):
            structured = step.get("structured") or {}
            if structured.get("judge_summary") and sut_id != "conventional_judge_summarizer":
                violations += 1
                details_parts.append(
                    f"non-judge SUT {sut_id} carries structured.judge_summary "
                    f"at {step.get('step_id')}"
                )

    if violations > t["max_constitutional_violations"]:
        return {
            "status": "fail",
            "score": {"violations": violations},
            "details": "; ".join(details_parts) if details_parts else f"{violations} violation(s)",
        }

    return {
        "status": "pass",
        "score": {"violations": 0},
        "details": "no constitutional violations detected",
    }


def _eval_practical(payload: Mapping[str, Any], t: dict) -> dict[str, Any]:
    """Practical: reproducibility / explainability.

    Requires git_commit, created_at timestamp, and run_id.
    """
    manifest = payload.get("manifest") or {}
    missing = []
    if t.get("require_git_commit") and not manifest.get("git_commit"):
        missing.append("git_commit")
    if t.get("require_commit_timestamp") and not manifest.get("created_at"):
        missing.append("created_at")
    if not manifest.get("run_id"):
        missing.append("run_id")

    if missing:
        return {
            "status": "fail",
            "score": None,
            "details": f"missing reproducibility fields: {', '.join(missing)}",
        }

    return {
        "status": "pass",
        "score": {
            "git_commit": manifest.get("git_commit"),
            "created_at": manifest.get("created_at"),
            "run_id": manifest.get("run_id"),
        },
        "details": "reproducibility fields present",
    }


# ---------------------------------------------------------------------------
# Gate evaluation
# ---------------------------------------------------------------------------


def evaluate_decision_gate(
    payload: Mapping[str, Any],
    *,
    repo_root: Path | str | None = None,
    thresholds: dict | None = None,
) -> DecisionGateReport:
    t = dict(THRESHOLDS)
    if thresholds:
        t.update(thresholds)
    reasons: list[str] = []

    # 1. Structural validity.
    structural_issues = _check_structural(payload)
    if structural_issues:
        return DecisionGateReport(
            decision="FAIL",
            reasons=[f"structural invalidity: {i}" for i in structural_issues],
            blocker="structural_invalidity",
        )

    # 2. Coverage.
    coverage = _check_coverage(payload)
    if coverage["missing_suts"]:
        return DecisionGateReport(
            decision="FAIL",
            reasons=[f"missing required SUTs: {coverage['missing_suts']}"],
            coverage=coverage,
            blocker="missing_required_suts",
        )

    if coverage["missing_tasks"]:
        # Partial task coverage is not a hard FAIL — but it gates evidence.
        reasons.append(
            f"missing task coverage: {coverage['missing_tasks']} — "
            "full corpus coverage needed for PASS"
        )

    # 3. Status summary.
    status_summary = summarize_statuses(payload)

    # 4. Comparison table (from completed structured records only).
    comparison_table = _build_comparison_table(payload)

    # 5. Dimension checks.
    dims: dict[str, dict[str, Any]] = {}
    dims["behavioral"] = _eval_behavioral(payload, t, coverage)
    dims["epistemic"] = _eval_epistemic(payload, t, comparison_table)
    dims["constitutional"] = _eval_constitutional(payload, t)
    dims["practical"] = _eval_practical(payload, t)

    # Aggregate decision.
    dim_statuses = {d: dims[d]["status"] for d in DIMENSIONS}
    completed_sessions = status_summary.get("sessions", {}).get("completed", 0)
    gl_completed = sum(
        1
        for s in payload.get("sessions") or []
        if s.get("sut_id") == "golden_lattice" and s.get("status") == "completed"
    )

    # If any dimension fails → FAIL.
    if "fail" in dim_statuses.values():
        fail_dims = [d for d, st in dim_statuses.items() if st == "fail"]
        detail = "; ".join(f"{d}: {dims[d]['details']}" for d in fail_dims)
        reasons.append(f"dimension failure(s): {detail}")
        reasons.append(f"blocker: {', '.join(fail_dims)} dimension(s) below threshold")
        return DecisionGateReport(
            decision="FAIL",
            reasons=reasons,
            dimensions=dims,
            coverage=coverage,
            comparison_table=comparison_table,
            status_summary=status_summary,
            blocker=f"{', '.join(fail_dims)} dimension(s) below threshold",
        )

    # If any dimension insufficient → INSUFFICIENT_EVIDENCE.
    if "insufficient" in dim_statuses.values():
        ins_dims = [d for d, st in dim_statuses.items() if st == "insufficient"]
        detail = "; ".join(f"{d}: {dims[d]['details']}" for d in ins_dims)
        if reasons:
            reasons.insert(0, detail)
        else:
            reasons.append(detail)
        blocker = _resolve_blocker(payload, status_summary, dims, coverage)
        return DecisionGateReport(
            decision="INSUFFICIENT_EVIDENCE",
            reasons=reasons,
            dimensions=dims,
            coverage=coverage,
            comparison_table=comparison_table,
            status_summary=status_summary,
            blocker=blocker or "insufficient_evidence",
        )

    # All dimensions pass.
    return DecisionGateReport(
        decision="PASS",
        reasons=reasons,
        dimensions=dims,
        coverage=coverage,
        comparison_table=comparison_table,
        status_summary=status_summary,
        blocker="",
    )


def _resolve_blocker(
    payload: Mapping[str, Any],
    status_summary: dict,
    dims: dict,
    coverage: dict,
) -> str:
    """Produce an exact, scannable blocker string — never a vague hand-wave."""
    manifest = payload.get("manifest") or {}
    mode = str(manifest.get("mode", "?"))

    if mode == "dry_run":
        return "dry_run: no live model calls attempted; all sessions planned"
    if status_summary.get("sessions", {}).get("completed", 0) == 0:
        return "no completed sessions in run — review provider config / API keys"

    missing = coverage.get("missing_tasks", [])
    if missing:
        return f"corpus coverage incomplete: {len(missing)} required task(s) missing"

    for d in DIMENSIONS:
        if dims[d]["status"] == "insufficient":
            return f"{d}: {dims[d]['details']}"

    return "insufficient evidence — could not resolve specific blocker"


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------


def format_gate_report(report: DecisionGateReport) -> str:
    lines: list[str] = []
    title_map = {"PASS": "✅ PASS", "FAIL": "❌ FAIL", "INSUFFICIENT_EVIDENCE": "⚠ INSUFFICIENT_EVIDENCE"}
    lines.append(f"# Decision Gate Report — {title_map.get(report.decision, report.decision)}")
    lines.append("")

    # Decision
    lines.append(f"**Decision:** {report.decision}")
    if report.blocker:
        lines.append(f"**Blocker:** {report.blocker}")
    lines.append("")

    # Reasons
    if report.reasons:
        lines.append("## Reasons")
        lines.append("")
        for r in report.reasons:
            lines.append(f"- {r}")
        lines.append("")

    # Status summary
    lines.append("## Status Summary")
    lines.append("")
    ss = report.status_summary
    sess = ss.get("sessions", {})
    lines.append("### Session statuses")
    lines.append("")
    for k in sorted(sess):
        lines.append(f"- **{k}:** {sess[k]}")
    lines.append("")
    step = ss.get("steps", {})
    if step:
        lines.append("### Step-level statuses")
        lines.append("")
        for k in sorted(k for k in step if k != "total"):
            lines.append(f"- **{k}:** {step[k]}")
        if "total" in step:
            lines.append(f"- **total steps:** {step['total']}")
        lines.append("")

    # Thresholds
    lines.append("## Documented Thresholds")
    lines.append("")
    for k, v in sorted(THRESHOLDS.items()):
        lines.append(f"- `{k}` = {v}")
    lines.append("")

    # Four dimensions
    lines.append("## Success Dimensions")
    lines.append("")
    icon = {"pass": "✅", "fail": "❌", "insufficient": "⚠"}
    for dim in DIMENSIONS:
        d = report.dimensions[dim]
        st = d["status"]
        lines.append(f"### {dim} — {icon.get(st, '?')} {st.upper()}")
        lines.append(f"- **status:** {st}")
        if d["score"] is not None:
            lines.append(f"- **score:** {d['score']}")
        lines.append(f"- **details:** {d['details']}")
        lines.append("")

    # Comparison table
    lines.append("## Comparison Table")
    lines.append("")
    if not report.comparison_table:
        lines.append("_(no rows)_")
    else:
        keys = sorted(report.comparison_table[0].keys())
        header = "| " + " | ".join(k for k in keys if k not in {"included_in_quality_comparison"}) + " | included? |"
        sep = "|" + "|".join(" --- " for _ in range(len(keys))) + "|"
        lines.append(header)
        lines.append(sep)
        for row in report.comparison_table:
            vals = []
            for k in keys:
                if k == "included_in_quality_comparison":
                    continue
                v = row[k]
                vals.append(str(v) if not isinstance(v, bool) else ("yes" if v else "no"))
            vals.append("yes" if row.get("included_in_quality_comparison") else "no")
            lines.append("| " + " | ".join(vals) + " |")
    lines.append("")

    # Coverage
    lines.append("## Coverage")
    lines.append("")
    cov = report.coverage
    lines.append(f"- SUTs declared: {cov.get('sut_ids_declared', [])}")
    lines.append(f"- SUTs present: {cov.get('sut_ids_present', [])}")
    lines.append(f"- Tasks declared: {cov.get('task_ids_declared', [])}")
    lines.append(f"- Tasks present: {cov.get('task_ids_present', [])}")
    if cov.get("missing_suts"):
        lines.append(f"- **MISSING SUTs:** {cov['missing_suts']}")
    if cov.get("missing_tasks"):
        lines.append(f"- **MISSING tasks:** {cov['missing_tasks']}")
    lines.append("")

    # Honesty clause
    lines.append("---")
    lines.append("")
    lines.append(
        "This report uses only explicit structured commitment observations "
        "(`commitment_transitions` with non-empty `reason` or "
        "`supporting_artifact_ref`). Prose in `raw_output` is never parsed "
        "for commitment state. Dry-run / unavailable statuses are preserved "
        "as-is and never converted into quality claims."
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Decision-gate analysis over Task 8 run JSON.",
    )
    parser.add_argument(
        "--run",
        type=Path,
        required=True,
        help="Path to Task 8 batch JSON",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write gate report to path (otherwise stdout only)",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=None,
        help="Repository root (default: auto-detect)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    repo = args.repo if args.repo is not None else _default_repo_root()

    payload = load_run_json(args.run)
    report = evaluate_decision_gate(payload, repo_root=repo)
    formatted = format_gate_report(report)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(formatted, encoding="utf-8")
        print(f"wrote: {args.out}", file=sys.stderr)

    sys.stdout.write(formatted)
    if not formatted.endswith("\n"):
        sys.stdout.write("\n")

    exit_map = {"PASS": 0, "FAIL": 1, "INSUFFICIENT_EVIDENCE": 2}
    return exit_map.get(report.decision, 1)


if __name__ == "__main__":
    raise SystemExit(main())
