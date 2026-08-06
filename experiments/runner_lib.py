"""Core library for the Task 8 experiment runner.

Loads/validates the TOML task corpus, builds prompt bundles, dispatches SUTs,
and writes machine-readable JSON + human-readable summary artifacts.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from experiments.baselines import REQUIRED_SUT_IDS, get_sut
from experiments.baselines.protocol import GroundingMode, RunMode, StepStatus

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TASKS_DIR = Path(__file__).resolve().parent / "tasks"

REQUIRED_CATEGORIES = frozenset(
    {
        "design_critique",
        "competing_scientific_explanations",
        "ambiguous_evidence_synthesis",
        "decision_under_changing_evidence",
    }
)

REQUIRED_TOP_LEVEL = frozenset(
    {
        "id",
        "version",
        "category",
        "title",
        "summary",
        "cognition_disclaimer",
        "expected_perturbation_sequence",
        "initial_claim",
        "controlled_challenge",
        "evidence_update",
        "reversal_or_removal",
    }
)

STEP_TO_TABLE = {
    "present_initial_claim": "initial_claim",
    "apply_controlled_challenge": "controlled_challenge",
    "apply_evidence_update": "evidence_update",
    "apply_reversal_or_removal": "reversal_or_removal",
}

PLACEHOLDER_BY_TABLE = {
    "initial_claim": ("{claim_text}", "text"),
    "controlled_challenge": ("{challenge_text}", "text"),
    "evidence_update": ("{evidence_text}", "text"),
    "reversal_or_removal": ("{reversal_text}", "text"),
}


Clock = Callable[[], str]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_task(path: Path | str) -> dict[str, Any]:
    path = Path(path)
    with path.open("rb") as f:
        data = tomllib.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: root must be a TOML table")
    _validate_task(data, source=path.name)
    return data


def load_tasks(tasks_dir: Path | str = DEFAULT_TASKS_DIR) -> list[dict[str, Any]]:
    tasks_dir = Path(tasks_dir)
    if not tasks_dir.is_dir():
        raise FileNotFoundError(f"tasks directory not found: {tasks_dir}")
    paths = sorted(tasks_dir.glob("*.toml"))
    if not paths:
        raise FileNotFoundError(f"no task TOML files under {tasks_dir}")
    tasks = [load_task(p) for p in paths]
    categories = {t["category"] for t in tasks}
    if categories != REQUIRED_CATEGORIES and len(tasks) >= 4:
        # Soft check for full corpus; subset runs still validate per-task.
        missing = REQUIRED_CATEGORIES - categories
        extra = categories - REQUIRED_CATEGORIES
        if missing or extra:
            # Full-corpus expectation when directory holds the v1 set.
            if len(paths) == 4:
                raise ValueError(
                    f"corpus category mismatch missing={sorted(missing)} extra={sorted(extra)}"
                )
    ids = [t["id"] for t in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate task ids in {tasks_dir}")
    return tasks


def _validate_task(task: Mapping[str, Any], *, source: str) -> None:
    missing = REQUIRED_TOP_LEVEL - set(task.keys())
    if missing:
        raise ValueError(f"{source}: missing fields {sorted(missing)}")
    if task["category"] not in REQUIRED_CATEGORIES:
        raise ValueError(f"{source}: unknown category {task['category']!r}")
    seq = task["expected_perturbation_sequence"]
    if not isinstance(seq, list) or not seq:
        raise ValueError(f"{source}: expected_perturbation_sequence must be non-empty list")
    for step in seq:
        if step not in STEP_TO_TABLE:
            raise ValueError(f"{source}: unsupported step_id {step!r}")
        table_name = STEP_TO_TABLE[step]
        table = task[table_name]
        if not isinstance(table, dict) or "id" not in table or "prompt" not in table or "text" not in table:
            raise ValueError(f"{source}: invalid table {table_name}")


def build_prompt_bundle(task: Mapping[str, Any], *, step_id: str) -> dict[str, Any]:
    if step_id not in STEP_TO_TABLE:
        raise ValueError(f"unknown step_id {step_id!r}")
    table_name = STEP_TO_TABLE[step_id]
    source = task[table_name]
    placeholder, text_key = PLACEHOLDER_BY_TABLE[table_name]
    text = str(source[text_key]).strip()
    prompt_template = str(source["prompt"])
    rendered = prompt_template.replace(placeholder, text)
    # Defensive: collapse any residual identical placeholder repeats.
    if placeholder in rendered:
        rendered = rendered.replace(placeholder, text)
    return {
        "task_id": task["id"],
        "step_id": step_id,
        "perturbation_id": source["id"],
        "table": table_name,
        "kind": source.get("kind"),
        "placeholder": placeholder,
        "rendered_prompt": rendered,
        "source": {
            "id": source["id"],
            "text": text,
            "prompt": prompt_template.strip(),
            "kind": source.get("kind"),
        },
    }


def build_prompt_bundles_for_task(task: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        build_prompt_bundle(task, step_id=step_id)
        for step_id in task["expected_perturbation_sequence"]
    ]


def make_session_id(*, run_id: str, task_id: str, sut_id: str) -> str:
    """Deterministic session id for fixed run_id + task + sut."""
    digest = hashlib.sha256(f"{run_id}|{task_id}|{sut_id}".encode("utf-8")).hexdigest()[:12]
    safe_task = re.sub(r"[^a-zA-Z0-9_.-]+", "_", task_id)[:48]
    return f"exp_{run_id}_{sut_id}_{safe_task}_{digest}"


def detect_git_commit(repo_root: Path = REPO_ROOT) -> Optional[str]:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip() or None
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None


def run_batch(
    *,
    tasks_dir: Path | str = DEFAULT_TASKS_DIR,
    sut_ids: Optional[Sequence[str]] = None,
    task_ids: Optional[Sequence[str]] = None,
    mode: RunMode | str = RunMode.DRY_RUN,
    grounding_mode: GroundingMode | str = GroundingMode.NONE,
    output_dir: Optional[Path | str] = None,
    run_id: Optional[str] = None,
    clock: Optional[Clock] = None,
    git_commit: Optional[str] = None,
) -> dict[str, Any]:
    """Run (or plan) one batch: selected tasks × selected SUTs.

    Returns a dict with ``manifest`` and ``sessions``. When ``output_dir`` is
    set, also writes JSON + summary via ``write_batch_outputs``.
    """
    if isinstance(mode, str):
        mode = RunMode(mode)
    if isinstance(grounding_mode, str):
        grounding_mode = GroundingMode(grounding_mode)
    clock = clock or utc_now_iso
    created_at = clock()
    run_id = run_id or f"run_{created_at.replace(':', '').replace('+', 'p')}"

    all_tasks = load_tasks(tasks_dir)
    if task_ids is not None:
        wanted = set(task_ids)
        tasks = [t for t in all_tasks if t["id"] in wanted]
        missing = wanted - {t["id"] for t in tasks}
        if missing:
            raise ValueError(f"unknown task_ids: {sorted(missing)}")
    else:
        tasks = all_tasks

    if sut_ids is None:
        selected_suts = list(REQUIRED_SUT_IDS)
    else:
        selected_suts = list(sut_ids)
        unknown = [s for s in selected_suts if s not in REQUIRED_SUT_IDS]
        if unknown:
            raise ValueError(f"unknown sut_ids: {unknown}")

    commit = git_commit if git_commit is not None else detect_git_commit()

    sessions: list[dict[str, Any]] = []
    for task in tasks:
        bundles = build_prompt_bundles_for_task(task)
        for sut_id in selected_suts:
            sut = get_sut(sut_id)
            session_id = make_session_id(run_id=run_id, task_id=task["id"], sut_id=sut_id)
            result = sut.run_session(
                task,
                mode=mode,
                session_id=session_id,
                prompt_bundles=bundles,
                grounding_mode=grounding_mode,
            )
            sessions.append(result.to_dict())

    # Stable order for determinism.
    sessions.sort(key=lambda s: (s["task_id"], s["sut_id"]))

    payload = {
        "manifest": {
            "run_id": run_id,
            "mode": mode.value if isinstance(mode, RunMode) else str(mode),
            "grounding_mode": grounding_mode.value,
            "created_at": created_at,
            "git_commit": commit,
            "tasks_dir": str(Path(tasks_dir)),
            "task_ids": [t["id"] for t in tasks],
            "sut_ids": list(selected_suts),
            "protocol": "experiments/protocol.md",
            "corpus": "experiments/tasks",
            "session_count": len(sessions),
            "status_vocabulary": [s.value for s in StepStatus],
            "notes": (
                "Commitment states are recorded only from explicit structured "
                "artifacts. Prose is never interpreted as commitment. "
                "conventional_judge_summarizer is optional and non-canonical."
            ),
        },
        "sessions": sessions,
    }

    if output_dir is not None:
        write_batch_outputs(payload, Path(output_dir))

    return payload


def write_batch_outputs(payload: Mapping[str, Any], output_dir: Path | str) -> dict[str, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = payload["manifest"]["run_id"]
    json_path = output_dir / f"{run_id}.json"
    summary_path = output_dir / f"{run_id}.summary.md"

    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    summary_path.write_text(render_human_summary(payload), encoding="utf-8")
    return {"json": json_path, "summary": summary_path}


def render_human_summary(payload: Mapping[str, Any]) -> str:
    m = payload["manifest"]
    lines: list[str] = [
        f"# Experiment batch {m['run_id']}",
        "",
        f"- mode: `{m['mode']}`",
        f"- created_at: `{m['created_at']}`",
        f"- git_commit: `{m.get('git_commit')}`",
        f"- tasks: {', '.join(m.get('task_ids') or [])}",
        f"- suts: {', '.join(m.get('sut_ids') or [])}",
        f"- sessions: {m.get('session_count', len(payload.get('sessions') or []))}",
        "",
        m.get("notes", ""),
        "",
        "## Sessions",
        "",
    ]
    for session in payload.get("sessions") or []:
        reason = session.get("unavailable_reason") or ""
        reason_bit = f" — {reason}" if reason else ""
        lines.append(
            f"### `{session['sut_id']}` × `{session['task_id']}`"
        )
        lines.append(
            f"- session_id: `{session['session_id']}`"
        )
        lines.append(
            f"- status: **{session['status']}**{reason_bit}"
        )
        if session.get("notes"):
            lines.append(f"- notes: {session['notes']}")
        lines.append("- steps:")
        for step in session.get("steps") or []:
            out = step.get("raw_output")
            if out is None:
                out_label = "raw_output=null (no model output)"
            else:
                out_label = f"raw_output={len(out)} chars"
            lat = step.get("latency_ms")
            lat_label = f", latency_ms={lat}" if lat is not None else ""
            lines.append(
                f"  - `{step['step_id']}` / `{step['perturbation_id']}`: "
                f"{step['status']} — {out_label}{lat_label}"
            )
            if step.get("reason"):
                lines.append(f"    - reason: {step['reason']}")
        lines.append("")

    lines.extend(
        [
            "## Honesty constraints",
            "",
            "- Dry-run/planning never fabricates responses.",
            "- Unavailable providers are recorded with reason; status is not completed.",
            "- Do not infer commitment states from prose.",
            "- `conventional_judge_summarizer` is optional and non-canonical.",
            "",
        ]
    )
    return "\n".join(lines)
