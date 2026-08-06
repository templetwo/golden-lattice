"""Human-readable case reports from Task 8 run JSON (Phase 2 Task 9).

Pure formatting over machine-readable experiment artifacts. Never:
- infers commitment states from free-form prose
- invents model answers for planned/unavailable/error steps
- calls a hidden generative judge or any network client
- modifies canonical Phase 4 synthesis

Required report sections (clearly separated):
  1. Canonical annotated lattice artifact (labeled canonical) when present
  2. Optional reader-facing interpretation (labeled non-canonical)
  3. Commitment timeline from explicit transition records only
  4. Preserved disagreement list when supplied, else explicitly unavailable
  5. Baseline comparison table across all four required SUTs
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from experiments.baselines import REQUIRED_SUT_IDS

# Well-known keys where structured fields may appear (session or step.structured).
_CANONICAL_KEYS = (
    "canonical_annotated_lattice",
    "annotated_lattice",
    "canonical_lattice_artifact",
    "phase_4_annotated",
)
_DISAGREEMENT_KEYS = (
    "preserved_disagreements",
    "disagreement_list",
    "disagreements",
    "preserved_disagreement_list",
)
_INTERPRETATION_KEYS = (
    "reader_facing_interpretation",
    "non_canonical_interpretation",
    "operator_interpretation",
    "reader_interpretation",
)


def load_run_json(path: Path | str) -> dict[str, Any]:
    """Load a Task 8 batch JSON artifact (manifest + sessions)."""
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: root must be a JSON object")
    if "manifest" not in data or "sessions" not in data:
        raise ValueError(f"{path}: expected keys 'manifest' and 'sessions'")
    if not isinstance(data["sessions"], list):
        raise ValueError(f"{path}: 'sessions' must be a list")
    return data


def write_case_report(
    payload: Mapping[str, Any],
    output_path: Path | str,
    *,
    task_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Path:
    """Generate a case report and write it to ``output_path``."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    text = generate_case_report(
        payload, task_id=task_id, session_id=session_id
    )
    output_path.write_text(text, encoding="utf-8")
    return output_path


def generate_case_report(
    payload: Mapping[str, Any],
    *,
    task_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> str:
    """Render a human-readable case report for one run (optionally one task).

    Parameters
    ----------
    payload:
        Task 8 batch dict with ``manifest`` and ``sessions``.
    task_id:
        If set, only sessions for this task are included.
    session_id:
        If set, only this session is primary for artifact sections; baseline
        comparison still covers peer SUTs for the same task when available.
    """
    manifest = dict(payload.get("manifest") or {})
    sessions = list(payload.get("sessions") or [])
    sessions = _filter_sessions(sessions, task_id=task_id, session_id=session_id)

    # Resolve the task scope for baseline comparison.
    task_ids = sorted({str(s.get("task_id")) for s in sessions if s.get("task_id")})
    if task_id:
        focus_tasks = [task_id]
    elif len(task_ids) == 1:
        focus_tasks = task_ids
    else:
        focus_tasks = task_ids

    lines: list[str] = []
    lines.append(f"# Case report — run `{manifest.get('run_id', '?')}`")
    lines.append("")
    lines.append("## Run metadata")
    lines.append("")
    lines.append(f"- run_id: `{manifest.get('run_id', '?')}`")
    lines.append(f"- mode: `{manifest.get('mode', '?')}`")
    lines.append(f"- grounding_mode: `{manifest.get('grounding_mode', '?')}`")
    lines.append(f"- created_at: `{manifest.get('created_at', '?')}`")
    lines.append(f"- git_commit: `{manifest.get('git_commit')}`")
    if task_id:
        lines.append(f"- task_id filter: `{task_id}`")
    if session_id:
        lines.append(f"- session_id filter: `{session_id}`")
    lines.append(
        f"- sessions in scope: {len(sessions)}"
    )
    if manifest.get("notes"):
        lines.append(f"- manifest notes: {manifest['notes']}")
    lines.append("")
    lines.append(
        "This report is a **read-only view** of experiment artifacts. "
        "It does not call models, does not invent answers for planned/"
        "unavailable steps, and never infers commitment states from prose."
    )
    lines.append("")

    if not sessions:
        lines.append("_No sessions matched the requested filter._")
        lines.append("")
        # Still emit empty required sections for a stable contract.
        lines.extend(_empty_required_sections())
        return "\n".join(lines)

    # One report body per focus task (usually one).
    for focus_task in focus_tasks:
        task_sessions = [s for s in sessions if s.get("task_id") == focus_task]
        if not task_sessions and task_id is None:
            continue
        if len(focus_tasks) > 1:
            lines.append(f"# Task `{focus_task}`")
            lines.append("")
        else:
            lines.append(f"## Task `{focus_task}`")
            lines.append("")

        primary = _select_primary_session(task_sessions, session_id=session_id)

        lines.extend(_section_canonical_annotated_lattice(primary, task_sessions))
        lines.extend(_section_reader_interpretation(primary, task_sessions))
        lines.extend(_section_commitment_timeline(primary, task_sessions))
        lines.extend(_section_disagreements(primary, task_sessions))
        lines.extend(_section_baseline_comparison(task_sessions, task_id=focus_task))

    lines.extend(
        [
            "## Honesty constraints",
            "",
            "- Canonical annotated lattice is labeled **canonical** when present.",
            "- Reader-facing interpretation is labeled **non-canonical** when present.",
            "- Commitment timeline uses **explicit transition records only**.",
            "- Disagreement list is shown only when supplied; otherwise **unavailable**.",
            "- Planned / unavailable / error statuses are preserved without fabricated answers.",
            "- No hidden judge is invoked by this generator.",
            "",
        ]
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def _section_canonical_annotated_lattice(
    primary: Optional[Mapping[str, Any]],
    task_sessions: Sequence[Mapping[str, Any]],
) -> list[str]:
    lines = [
        "## Canonical annotated lattice artifact",
        "",
        "_Label: **canonical** — authoritative lattice synthesis record when present. "
        "Not operator commentary._",
        "",
    ]
    artifact, source = _find_canonical_artifact(primary, task_sessions)
    if artifact is None:
        lines.append(
            "**Status: not present / unavailable** — no canonical annotated "
            "lattice artifact was supplied in the run JSON for this scope "
            "(common for `planned` dry-run and `unavailable` live skips)."
        )
        lines.append("")
        return lines

    lines.append(f"_Source: `{source}`_")
    lines.append("")
    lines.append("```text")
    lines.append(artifact.rstrip())
    lines.append("```")
    lines.append("")
    return lines


def _section_reader_interpretation(
    primary: Optional[Mapping[str, Any]],
    task_sessions: Sequence[Mapping[str, Any]],
) -> list[str]:
    lines = [
        "## Reader-facing interpretation (non-canonical)",
        "",
        "_Label: **non-canonical** — optional human/operator reading. "
        "Must not be promoted into the canonical lattice record._",
        "",
    ]
    text, source = _find_interpretation(primary, task_sessions)
    if text is None:
        lines.append(
            "**Status: unavailable** — no reader-facing interpretation was supplied."
        )
        lines.append("")
        return lines

    lines.append(f"_Source: `{source}`_")
    lines.append("")
    lines.append(text.rstrip())
    lines.append("")
    return lines


def _section_commitment_timeline(
    primary: Optional[Mapping[str, Any]],
    task_sessions: Sequence[Mapping[str, Any]],
) -> list[str]:
    lines = [
        "## Commitment timeline",
        "",
        "_Explicit `commitment_transitions` records only. "
        "Prose is never parsed for commitment state._",
        "",
    ]
    records = _collect_commitment_transitions(primary, task_sessions)
    if not records:
        lines.append(
            "**Status: unavailable** — no explicit commitment transition "
            "records were supplied (empty timeline; nothing inferred from raw_output)."
        )
        lines.append("")
        return lines

    lines.append("| seq | claim_id | prior → next | source_event | sut / step | reason |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for row in records:
        prior = row.get("prior_state", "?")
        nxt = row.get("next_state", "?")
        reason = _cell(row.get("reason") or row.get("supporting_artifact_ref") or "")
        lines.append(
            "| {seq} | `{claim}` | {prior} → {nxt} | `{event}` | `{where}` | {reason} |".format(
                seq=row.get("sequence_index", ""),
                claim=_cell(row.get("claim_id", "?")),
                prior=_cell(prior),
                nxt=_cell(nxt),
                event=_cell(row.get("source_event", "?")),
                where=_cell(row.get("_where", "")),
                reason=reason,
            )
        )
    lines.append("")
    lines.append(f"_Explicit transitions listed: {len(records)}_")
    lines.append("")
    return lines


def _section_disagreements(
    primary: Optional[Mapping[str, Any]],
    task_sessions: Sequence[Mapping[str, Any]],
) -> list[str]:
    lines = [
        "## Preserved disagreement list",
        "",
        "_Shown only when the run JSON supplies a structured disagreement list. "
        "Otherwise explicitly unavailable — never scraped from prose._",
        "",
    ]
    items, source = _find_disagreements(primary, task_sessions)
    if not items:
        lines.append(
            "**Status: unavailable** — no preserved disagreement list was supplied."
        )
        lines.append("")
        return lines

    lines.append(f"_Source: `{source}`_")
    lines.append("")
    for i, item in enumerate(items, start=1):
        if isinstance(item, Mapping):
            claim = item.get("claim_id", item.get("id", "?"))
            disputers = item.get("disputers") or item.get("peers") or item.get("models")
            summary = item.get("summary") or item.get("text") or item.get("reason") or ""
            lines.append(f"{i}. claim `{claim}`")
            if disputers:
                if isinstance(disputers, (list, tuple)):
                    lines.append(f"   - disputers: {', '.join(str(d) for d in disputers)}")
                else:
                    lines.append(f"   - disputers: {disputers}")
            if summary:
                lines.append(f"   - {summary}")
        else:
            lines.append(f"{i}. {item}")
    lines.append("")
    return lines


def _section_baseline_comparison(
    task_sessions: Sequence[Mapping[str, Any]],
    *,
    task_id: str,
) -> list[str]:
    lines = [
        "## Baseline comparison",
        "",
        f"_All four required SUTs for task `{task_id}`. "
        "Statuses are taken from session records; missing SUT rows are marked absent._",
        "",
    ]
    by_sut = {str(s.get("sut_id")): s for s in task_sessions if s.get("sut_id")}

    lines.append(
        "| sut_id | status | steps (status summary) | raw_output | "
        "explicit transitions | latency_ms (sum) | notes |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")

    for sut_id in REQUIRED_SUT_IDS:
        session = by_sut.get(sut_id)
        if session is None:
            lines.append(
                f"| `{sut_id}` | **absent** | — | — | — | — | no session in scope |"
            )
            continue
        status = str(session.get("status") or "?")
        steps = list(session.get("steps") or [])
        step_summary = ", ".join(
            f"{st.get('step_id', '?')}={st.get('status', '?')}" for st in steps
        ) or "—"
        raw_states = []
        for st in steps:
            if st.get("raw_output") is None:
                raw_states.append("null")
            else:
                raw_states.append(f"{len(str(st['raw_output']))}ch")
        raw_label = "/".join(raw_states) if raw_states else "—"
        n_trans = 0
        for st in steps:
            ct = st.get("commitment_transitions")
            if isinstance(ct, list):
                n_trans += len(ct)
        lat_vals = [
            float(st["latency_ms"])
            for st in steps
            if st.get("latency_ms") is not None
        ]
        lat_label = f"{sum(lat_vals):.1f}" if lat_vals else "—"
        note_bits = []
        if session.get("unavailable_reason"):
            note_bits.append(str(session["unavailable_reason"]))
        meta = session.get("metadata") or {}
        if meta.get("optional"):
            note_bits.append("optional non-canonical SUT")
        if meta.get("canonical") is True:
            note_bits.append("canonical SUT")
        if session.get("notes"):
            # Keep table cells short.
            note_bits.append(_short(str(session["notes"]), 80))
        notes = "; ".join(note_bits) if note_bits else "—"
        lines.append(
            f"| `{sut_id}` | **{status}** | {step_summary} | {raw_label} | "
            f"{n_trans} | {lat_label} | {_cell(notes)} |"
        )

    lines.append("")
    # Honest callout when everything is planned/unavailable.
    statuses = {str(by_sut[s]["status"]) for s in by_sut}
    if statuses and statuses <= {"planned"}:
        lines.append(
            "> All in-scope sessions are **planned** (dry-run / prompt bundles only). "
            "No model outputs were fabricated for comparison."
        )
        lines.append("")
    elif statuses and statuses <= {"unavailable", "planned"}:
        lines.append(
            "> Sessions are **unavailable** and/or **planned**. "
            "No completed answers are implied."
        )
        lines.append("")
    return lines


def _empty_required_sections() -> list[str]:
    return [
        "## Canonical annotated lattice artifact",
        "",
        "_Label: **canonical**_",
        "",
        "**Status: not present / unavailable**",
        "",
        "## Reader-facing interpretation (non-canonical)",
        "",
        "_Label: **non-canonical**_",
        "",
        "**Status: unavailable** — no reader-facing interpretation was supplied.",
        "",
        "## Commitment timeline",
        "",
        "**Status: unavailable** — no explicit commitment transition records were supplied.",
        "",
        "## Preserved disagreement list",
        "",
        "**Status: unavailable** — no preserved disagreement list was supplied.",
        "",
        "## Baseline comparison",
        "",
        "_No sessions in scope._",
        "",
    ]


# ---------------------------------------------------------------------------
# Extraction helpers (structured fields only)
# ---------------------------------------------------------------------------


def _filter_sessions(
    sessions: Sequence[Mapping[str, Any]],
    *,
    task_id: Optional[str],
    session_id: Optional[str],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [dict(s) for s in sessions]
    if session_id is not None:
        # Keep the named session plus sibling SUTs on the same task for comparison.
        match = [s for s in out if s.get("session_id") == session_id]
        if not match:
            return []
        tid = match[0].get("task_id")
        out = [s for s in out if s.get("task_id") == tid]
    if task_id is not None:
        out = [s for s in out if s.get("task_id") == task_id]
    out.sort(key=lambda s: (str(s.get("task_id")), str(s.get("sut_id"))))
    return out


def _select_primary_session(
    task_sessions: Sequence[Mapping[str, Any]],
    *,
    session_id: Optional[str],
) -> Optional[Mapping[str, Any]]:
    if not task_sessions:
        return None
    if session_id is not None:
        for s in task_sessions:
            if s.get("session_id") == session_id:
                return s
    # Prefer golden_lattice for canonical artifact sections.
    for s in task_sessions:
        if s.get("sut_id") == "golden_lattice":
            return s
    for s in task_sessions:
        meta = s.get("metadata") or {}
        if meta.get("canonical") is True:
            return s
    return task_sessions[0]


def _find_canonical_artifact(
    primary: Optional[Mapping[str, Any]],
    task_sessions: Sequence[Mapping[str, Any]],
) -> tuple[Optional[str], str]:
    """Return (artifact_text, source_label) from structured fields only.

    For golden_lattice completed steps, ``structured.canonical_annotated_lattice``
    (and aliases) is preferred. Falling back to ``raw_output`` is allowed only
    when the step is completed AND the SUT is the canonical golden_lattice SUT
    AND structured explicitly marks the raw output as the annotated artifact
    via ``structured.raw_output_is_canonical_annotated`` — we do **not** treat
    arbitrary prose as canonical. If only structured key holds the artifact,
    use that. Additionally accept session-level keys.
    """
    candidates: list[Mapping[str, Any]] = []
    if primary is not None:
        candidates.append(primary)
    for s in task_sessions:
        if s is primary:
            continue
        if s.get("sut_id") == "golden_lattice" or (s.get("metadata") or {}).get(
            "canonical"
        ):
            candidates.append(s)

    for session in candidates:
        # Session-level structured keys
        for key in _CANONICAL_KEYS:
            val = session.get(key)
            if isinstance(val, str) and val.strip():
                return val, f"session:{session.get('session_id')}:{key}"
        structured = session.get("structured")
        if isinstance(structured, Mapping):
            for key in _CANONICAL_KEYS:
                val = structured.get(key)
                if isinstance(val, str) and val.strip():
                    return val, f"session.structured:{key}"

        for step in session.get("steps") or []:
            st = step.get("structured")
            if isinstance(st, Mapping):
                for key in _CANONICAL_KEYS:
                    val = st.get(key)
                    if isinstance(val, str) and val.strip():
                        return (
                            val,
                            f"step:{step.get('step_id')}:structured.{key}",
                        )
    return None, ""


def _find_interpretation(
    primary: Optional[Mapping[str, Any]],
    task_sessions: Sequence[Mapping[str, Any]],
) -> tuple[Optional[str], str]:
    ordered: list[Mapping[str, Any]] = []
    if primary is not None:
        ordered.append(primary)
    ordered.extend(s for s in task_sessions if s is not primary)

    for session in ordered:
        for key in _INTERPRETATION_KEYS:
            val = session.get(key)
            if isinstance(val, str) and val.strip():
                return val, f"session:{key}"
        meta = session.get("metadata") or {}
        if isinstance(meta, Mapping):
            for key in _INTERPRETATION_KEYS:
                val = meta.get(key)
                if isinstance(val, str) and val.strip():
                    return val, f"session.metadata:{key}"
        for step in session.get("steps") or []:
            st = step.get("structured")
            if isinstance(st, Mapping):
                for key in _INTERPRETATION_KEYS:
                    val = st.get(key)
                    if isinstance(val, str) and val.strip():
                        return val, f"step:{step.get('step_id')}:structured.{key}"
    return None, ""


def _collect_commitment_transitions(
    primary: Optional[Mapping[str, Any]],
    task_sessions: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Gather only list-typed commitment_transitions fields — never prose."""
    ordered: list[Mapping[str, Any]] = []
    if primary is not None:
        ordered.append(primary)
    ordered.extend(s for s in task_sessions if s is not primary)

    rows: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for session in ordered:
        # Session-level list (if a runner attaches it).
        top = session.get("commitment_transitions")
        if isinstance(top, list):
            for item in top:
                if not isinstance(item, Mapping):
                    continue
                row = dict(item)
                row["_where"] = f"{session.get('sut_id')}/session"
                key = _transition_dedupe_key(row)
                if key not in seen:
                    seen.add(key)
                    rows.append(row)
        for step in session.get("steps") or []:
            ct = step.get("commitment_transitions")
            if not isinstance(ct, list):
                continue
            for item in ct:
                if not isinstance(item, Mapping):
                    continue
                row = dict(item)
                row["_where"] = (
                    f"{session.get('sut_id')}/{step.get('step_id')}"
                )
                key = _transition_dedupe_key(row)
                if key not in seen:
                    seen.add(key)
                    rows.append(row)
    rows.sort(
        key=lambda r: (
            int(r["sequence_index"])
            if isinstance(r.get("sequence_index"), int)
            else 10**9,
            str(r.get("_where", "")),
            str(r.get("claim_id", "")),
        )
    )
    return rows


def _transition_dedupe_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("claim_id"),
        row.get("prior_state"),
        row.get("next_state"),
        row.get("source_event"),
        row.get("sequence_index"),
        row.get("_where"),
    )


def _find_disagreements(
    primary: Optional[Mapping[str, Any]],
    task_sessions: Sequence[Mapping[str, Any]],
) -> tuple[list[Any], str]:
    ordered: list[Mapping[str, Any]] = []
    if primary is not None:
        ordered.append(primary)
    ordered.extend(s for s in task_sessions if s is not primary)

    for session in ordered:
        for key in _DISAGREEMENT_KEYS:
            val = session.get(key)
            if isinstance(val, list) and val:
                return list(val), f"session:{key}"
        structured = session.get("structured")
        if isinstance(structured, Mapping):
            for key in _DISAGREEMENT_KEYS:
                val = structured.get(key)
                if isinstance(val, list) and val:
                    return list(val), f"session.structured:{key}"
        for step in session.get("steps") or []:
            st = step.get("structured")
            if isinstance(st, Mapping):
                for key in _DISAGREEMENT_KEYS:
                    val = st.get(key)
                    if isinstance(val, list) and val:
                        return (
                            list(val),
                            f"step:{step.get('step_id')}:structured.{key}",
                        )
    return [], ""


def _cell(value: Any) -> str:
    text = str(value).replace("|", "\\|").replace("\n", " ").strip()
    return text


def _short(text: str, n: int) -> str:
    text = text.replace("\n", " ").strip()
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"
