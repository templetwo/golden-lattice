"""Core validation checks for Golden Lattice (Phase 2 Task 10)."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence, Union

# ---------------------------------------------------------------------------
# Report types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    check_id: str
    severity: str  # "error" | "warning" | "info"
    message: str
    path: Optional[str] = None

    def __post_init__(self) -> None:
        if self.severity not in {"error", "warning", "info"}:
            raise ValueError(f"unknown severity {self.severity!r}")


@dataclass
class ValidationReport:
    findings: tuple[Finding, ...] = ()
    checks_run: tuple[str, ...] = ()

    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors()

    def summary(self) -> str:
        parts = [
            f"checks={len(self.checks_run)}",
            f"errors={len(self.errors())}",
            f"warnings={len(self.warnings())}",
        ]
        lines = [", ".join(parts)]
        for f in self.findings:
            loc = f" ({f.path})" if f.path else ""
            lines.append(f"  [{f.severity}] {f.check_id}: {f.message}{loc}")
        return "\n".join(lines)

    def merge(self, other: "ValidationReport") -> "ValidationReport":
        return ValidationReport(
            findings=tuple(self.findings) + tuple(other.findings),
            checks_run=tuple(self.checks_run) + tuple(other.checks_run),
        )


def _error(check_id: str, message: str, path: Optional[str] = None) -> Finding:
    return Finding(check_id=check_id, severity="error", message=message, path=path)


def _info(check_id: str, message: str, path: Optional[str] = None) -> Finding:
    return Finding(check_id=check_id, severity="info", message=message, path=path)


def _report(check: str, findings: Iterable[Finding]) -> ValidationReport:
    return ValidationReport(findings=tuple(findings), checks_run=(check,))


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

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

REQUIRED_CLAIM_FIELDS = frozenset({"id", "text", "prompt"})
REQUIRED_CONDITION_FIELDS = frozenset({"id", "text", "prompt", "kind"})
ALLOWED_CONDITION_KINDS = frozenset({"challenge", "evidence_update", "reversal", "removal"})
KIND_BY_FIELD = {
    "controlled_challenge": frozenset({"challenge"}),
    "evidence_update": frozenset({"evidence_update"}),
    "reversal_or_removal": frozenset({"reversal", "removal"}),
}

STABLE_ID_RE = re.compile(r"^gl\.longitudinal\.[a-z0-9_]+\.v\d+$")
NESTED_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.]*$", re.IGNORECASE)

REQUIRED_SUT_IDS = (
    "strongest_single_peer",
    "simple_parallel_responses",
    "conventional_judge_summarizer",
    "golden_lattice",
)

MANIFEST_REQUIRED = frozenset(
    {
        "run_id",
        "mode",
        "created_at",
        "sut_ids",
        "task_ids",
        "session_count",
        "status_vocabulary",
    }
)

SESSION_REQUIRED = frozenset({"session_id", "task_id", "sut_id", "status", "steps"})
STEP_REQUIRED = frozenset({"step_id", "perturbation_id", "status", "prompt_bundle"})
STATUS_VOCAB = frozenset(
    {"planned", "completed", "unavailable", "skipped", "aborted", "error"}
)

COMMITMENT_REQUIRED_KEYS = frozenset(
    {
        "claim_id",
        "source_model",
        "prior_state",
        "next_state",
        "source_event",
        "sequence_index",
    }
)

ACTIVE_DOC_RELPATHS = (
    "README.md",
    "ARCHITECTURE.md",
    "CONTRIBUTING.md",
    "experiments/README.md",
    "experiments/protocol.md",
    "experiments/baselines/README.md",
    "experiments/reports/README.md",
)

# Stale claims that must not appear in active documentation.
STALE_DOC_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "stale_test_count_69",
        re.compile(r"\b69\s+tests\b", re.IGNORECASE),
    ),
    (
        "operational_layer_absent",
        re.compile(
            r"(what is not in this repo yet|these will ship next|"
            r"remaining layers are forthcoming|"
            r"operational layer\.?\s+phase 2 cross-reading wire format)",
            re.IGNORECASE,
        ),
    ),
    (
        "triadic_default_claim",
        re.compile(
            r"(triadic[- ]only|default\s+triadic|three-seat\s+default|"
            r"default\s+roster\s+is\s+triadic|"
            r"three\s+peers\s+only|"
            r"opus,\s*sonnet,\s*and\s*haiku\s+as\s+the\s+three\s+peers)",
            re.IGNORECASE,
        ),
    ),
)

ACTIVE_SEAT_DISPLAY = ("Fable", "Opus", "Sonnet", "Haiku")


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# (1) Corpus
# ---------------------------------------------------------------------------


def validate_corpus(
    *,
    repo_root: Optional[Path] = None,
    tasks_dir: Optional[Path] = None,
) -> ValidationReport:
    repo_root = Path(repo_root) if repo_root is not None else _default_repo_root()
    tasks_dir = Path(tasks_dir) if tasks_dir is not None else repo_root / "experiments" / "tasks"
    findings: list[Finding] = []
    check = "corpus"

    if not tasks_dir.is_dir():
        return _report(
            check,
            [_error("corpus.missing_dir", f"tasks directory not found: {tasks_dir}", str(tasks_dir))],
        )

    paths = sorted(tasks_dir.glob("*.toml"))
    if not paths:
        return _report(
            check,
            [_error("corpus.empty", f"no task TOML files under {tasks_dir}", str(tasks_dir))],
        )

    tasks: list[tuple[Path, dict[str, Any]]] = []
    for path in paths:
        try:
            with path.open("rb") as fh:
                data = tomllib.load(fh)
        except Exception as exc:  # noqa: BLE001 — surface parse errors as findings
            findings.append(_error("corpus.parse", f"failed to parse TOML: {exc}", str(path)))
            continue
        if not isinstance(data, dict):
            findings.append(_error("corpus.root", "root value must be a table", str(path)))
            continue
        tasks.append((path, data))
        findings.extend(_validate_one_task(path, data))

    if len(paths) == 4 or len(tasks) >= 4:
        categories = {t.get("category") for _, t in tasks}
        if categories != REQUIRED_CATEGORIES:
            findings.append(
                _error(
                    "corpus.categories",
                    f"category set mismatch: got {sorted(x for x in categories if x)}, "
                    f"expected {sorted(REQUIRED_CATEGORIES)}",
                    str(tasks_dir),
                )
            )

    # Corpus-level reversal/removal coverage when full v1 set present.
    if len(tasks) >= 4:
        kinds = set()
        for _, task in tasks:
            rev = task.get("reversal_or_removal")
            if isinstance(rev, dict) and isinstance(rev.get("kind"), str):
                kinds.add(rev["kind"])
        if "reversal" not in kinds:
            findings.append(
                _error(
                    "corpus.reversal_coverage",
                    "corpus must include at least one reversal_or_removal.kind=reversal",
                    str(tasks_dir),
                )
            )
        if "removal" not in kinds:
            findings.append(
                _error(
                    "corpus.removal_coverage",
                    "corpus must include at least one reversal_or_removal.kind=removal",
                    str(tasks_dir),
                )
            )

    ids = [t.get("id") for _, t in tasks if isinstance(t.get("id"), str)]
    if len(ids) != len(set(ids)):
        findings.append(_error("corpus.duplicate_task_ids", f"duplicate task ids: {ids}"))

    if not findings:
        findings.append(
            _info("corpus.ok", f"validated {len(tasks)} task file(s)", str(tasks_dir))
        )
    return _report(check, findings)


def _validate_one_task(path: Path, task: Mapping[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    name = path.name
    missing = REQUIRED_TOP_LEVEL - set(task.keys())
    if missing:
        findings.append(
            _error("corpus.fields", f"missing top-level fields {sorted(missing)}", name)
        )
        return findings

    tid = task["id"]
    if not isinstance(tid, str) or not STABLE_ID_RE.match(tid):
        findings.append(
            _error(
                "corpus.task_id",
                f"id {tid!r} must match {STABLE_ID_RE.pattern}",
                name,
            )
        )

    if not isinstance(task.get("version"), int) or task["version"] < 1:
        findings.append(_error("corpus.version", "version must be int >= 1", name))

    if task.get("category") not in REQUIRED_CATEGORIES:
        findings.append(
            _error("corpus.category", f"unknown category {task.get('category')!r}", name)
        )

    seq = task.get("expected_perturbation_sequence")
    if not isinstance(seq, list) or not seq:
        findings.append(
            _error("corpus.sequence", "expected_perturbation_sequence must be non-empty list", name)
        )
    else:
        if tuple(seq) != DEFAULT_SEQUENCE:
            findings.append(
                _error(
                    "corpus.sequence_order",
                    f"v1 sequence must be {DEFAULT_SEQUENCE}, got {tuple(seq)}",
                    name,
                )
            )
        for step in seq:
            if step not in STEP_TO_TABLE:
                findings.append(
                    _error("corpus.sequence_step", f"unsupported step_id {step!r}", name)
                )

    claim = task.get("initial_claim")
    if not isinstance(claim, dict):
        findings.append(_error("corpus.initial_claim", "initial_claim must be a table", name))
    else:
        missing_c = REQUIRED_CLAIM_FIELDS - set(claim.keys())
        if missing_c:
            findings.append(
                _error("corpus.initial_claim", f"missing {sorted(missing_c)}", name)
            )
        else:
            if not NESTED_ID_RE.match(str(claim["id"])):
                findings.append(_error("corpus.perturbation_id", "bad initial_claim.id", name))
            if "{claim_text}" not in str(claim.get("prompt", "")):
                findings.append(
                    _error(
                        "corpus.placeholder",
                        "initial_claim.prompt must include {claim_text}",
                        name,
                    )
                )

    nested_ids: list[str] = []
    if isinstance(claim, dict) and isinstance(claim.get("id"), str):
        nested_ids.append(claim["id"])

    for field_name, expected_kinds in KIND_BY_FIELD.items():
        cond = task.get(field_name)
        if not isinstance(cond, dict):
            findings.append(_error(f"corpus.{field_name}", f"{field_name} must be a table", name))
            continue
        missing_f = REQUIRED_CONDITION_FIELDS - set(cond.keys())
        if missing_f:
            findings.append(
                _error(f"corpus.{field_name}", f"missing {sorted(missing_f)}", name)
            )
            continue
        if not NESTED_ID_RE.match(str(cond["id"])):
            findings.append(
                _error("corpus.perturbation_id", f"bad {field_name}.id", name)
            )
        nested_ids.append(str(cond["id"]))
        kind = cond.get("kind")
        if kind not in ALLOWED_CONDITION_KINDS:
            findings.append(
                _error("corpus.kind", f"{field_name}.kind={kind!r} not allowed", name)
            )
        elif kind not in expected_kinds:
            findings.append(
                _error(
                    "corpus.kind_slot",
                    f"{field_name}.kind={kind!r} not in {sorted(expected_kinds)}",
                    name,
                )
            )
        text = str(cond.get("text", "")).strip()
        prompt = str(cond.get("prompt", "")).strip()
        if not text or not prompt:
            findings.append(
                _error(f"corpus.{field_name}", "text and prompt must be non-empty", name)
            )

    # Placeholder discipline
    ch = task.get("controlled_challenge")
    ev = task.get("evidence_update")
    rv = task.get("reversal_or_removal")
    if isinstance(ch, dict) and "{challenge_text}" not in str(ch.get("prompt", "")):
        findings.append(
            _error("corpus.placeholder", "controlled_challenge.prompt needs {challenge_text}", name)
        )
    if isinstance(ev, dict) and "{evidence_text}" not in str(ev.get("prompt", "")):
        findings.append(
            _error("corpus.placeholder", "evidence_update.prompt needs {evidence_text}", name)
        )
    if isinstance(rv, dict) and "{reversal_text}" not in str(rv.get("prompt", "")):
        findings.append(
            _error("corpus.placeholder", "reversal_or_removal.prompt needs {reversal_text}", name)
        )

    if nested_ids and len(nested_ids) != len(set(nested_ids)):
        findings.append(
            _error(
                "corpus.duplicate_perturbation_ids",
                f"nested perturbation ids must be unique, got {nested_ids}",
                name,
            )
        )

    disclaimer = str(task.get("cognition_disclaimer", "")).lower()
    if "cognition" not in disclaimer or "not" not in disclaimer:
        findings.append(
            _error(
                "corpus.disclaimer",
                "cognition_disclaimer must explicitly deny cognition labeling",
                name,
            )
        )

    return findings


# ---------------------------------------------------------------------------
# (2) Baseline SUT registry
# ---------------------------------------------------------------------------


def validate_baseline_registry(*, repo_root: Optional[Path] = None) -> ValidationReport:
    del repo_root  # registry is import-based; path reserved for future FS checks
    findings: list[Finding] = []
    check = "baseline"

    try:
        from experiments.baselines import REQUIRED_SUT_IDS as REG_IDS
        from experiments.baselines import SUT_REGISTRY, get_sut
    except Exception as exc:  # noqa: BLE001
        return _report(
            check,
            [_error("baseline.import", f"cannot import experiments.baselines: {exc}")],
        )

    if tuple(REG_IDS) != REQUIRED_SUT_IDS:
        findings.append(
            _error(
                "baseline.required_ids",
                f"REQUIRED_SUT_IDS mismatch: got {tuple(REG_IDS)}, expected {REQUIRED_SUT_IDS}",
            )
        )

    if set(SUT_REGISTRY) != set(REQUIRED_SUT_IDS):
        findings.append(
            _error(
                "baseline.registry_keys",
                f"SUT_REGISTRY keys {sorted(SUT_REGISTRY)} != {list(REQUIRED_SUT_IDS)}",
            )
        )

    for sut_id in REQUIRED_SUT_IDS:
        try:
            sut = get_sut(sut_id)
        except Exception as exc:  # noqa: BLE001
            findings.append(_error("baseline.get_sut", f"{sut_id}: {exc}"))
            continue
        if getattr(sut, "sut_id", None) != sut_id:
            findings.append(
                _error("baseline.sut_id", f"{sut_id}: sut.sut_id={getattr(sut, 'sut_id', None)!r}")
            )
        if not hasattr(sut, "canonical") or not hasattr(sut, "optional"):
            findings.append(
                _error(
                    "baseline.metadata",
                    f"{sut_id}: missing explicit canonical/optional metadata",
                )
            )
            continue
        # Type discipline
        if not isinstance(sut.canonical, bool) or not isinstance(sut.optional, bool):
            findings.append(
                _error(
                    "baseline.metadata_type",
                    f"{sut_id}: canonical/optional must be bool",
                )
            )

    # Explicit canonical-vs-noncanonical policy for the lattice path vs judge foil.
    try:
        gl = get_sut("golden_lattice")
        judge = get_sut("conventional_judge_summarizer")
        single = get_sut("strongest_single_peer")
        parallel = get_sut("simple_parallel_responses")
    except Exception as exc:  # noqa: BLE001
        findings.append(_error("baseline.policy_lookup", str(exc)))
    else:
        if gl.canonical is not True or gl.optional is not False:
            findings.append(
                _error(
                    "baseline.golden_lattice_flags",
                    "golden_lattice must be canonical=True, optional=False",
                )
            )
        if judge.canonical is not False or judge.optional is not True:
            findings.append(
                _error(
                    "baseline.judge_flags",
                    "conventional_judge_summarizer must be canonical=False, optional=True",
                )
            )
        # Required comparators must declare explicit metadata and remain non-optional.
        for sut in (single, parallel):
            if not isinstance(sut.canonical, bool):
                findings.append(
                    _error(
                        "baseline.comparator_canonical_type",
                        f"{sut.sut_id}.canonical must be an explicit bool",
                    )
                )
            if sut.optional is not False:
                findings.append(
                    _error(
                        "baseline.required_comparator",
                        f"{sut.sut_id} must be optional=False (required comparator)",
                    )
                )

    if not findings:
        findings.append(_info("baseline.ok", "SUT registry complete with explicit canonical flags"))
    return _report(check, findings)


# ---------------------------------------------------------------------------
# (3) Run / report JSON
# ---------------------------------------------------------------------------


RunJsonInput = Union[Path, str, Mapping[str, Any]]


def validate_run_json(source: RunJsonInput) -> ValidationReport:
    findings: list[Finding] = []
    check = "run_json"
    path_label: Optional[str] = None

    if isinstance(source, (str, Path)):
        path = Path(source)
        path_label = str(path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            return _report(
                check,
                [_error("run_json.load", f"failed to load JSON: {exc}", path_label)],
            )
    else:
        payload = dict(source)

    if not isinstance(payload, dict):
        return _report(check, [_error("run_json.root", "root must be a JSON object", path_label)])

    if "manifest" not in payload or "sessions" not in payload:
        findings.append(
            _error(
                "run_json.keys",
                "expected top-level keys 'manifest' and 'sessions'",
                path_label,
            )
        )
        return _report(check, findings)

    manifest = payload["manifest"]
    sessions = payload["sessions"]
    if not isinstance(manifest, dict):
        findings.append(_error("run_json.manifest_type", "manifest must be an object", path_label))
        return _report(check, findings)
    if not isinstance(sessions, list):
        findings.append(_error("run_json.sessions_type", "sessions must be a list", path_label))
        return _report(check, findings)

    missing_m = MANIFEST_REQUIRED - set(manifest.keys())
    if missing_m:
        findings.append(
            _error(
                "run_json.manifest_fields",
                f"manifest missing required keys {sorted(missing_m)}",
                path_label,
            )
        )

    sut_ids = manifest.get("sut_ids")
    if isinstance(sut_ids, list):
        missing_suts = set(REQUIRED_SUT_IDS) - set(sut_ids)
        # Full-corpus runs should include all four; partial runs allowed if
        # session_count matches. Only error when sessions reference unknown SUTs
        # or when declared set is non-empty but misses required without subset note.
        unknown = set(sut_ids) - set(REQUIRED_SUT_IDS)
        if unknown:
            findings.append(
                _error(
                    "run_json.unknown_sut",
                    f"unknown sut_ids in manifest: {sorted(unknown)}",
                    path_label,
                )
            )
    else:
        if "sut_ids" in manifest:
            findings.append(_error("run_json.sut_ids_type", "sut_ids must be a list", path_label))

    status_vocab = set(manifest.get("status_vocabulary") or [])
    if status_vocab and not STATUS_VOCAB.issubset(status_vocab) and status_vocab - STATUS_VOCAB:
        # Allow equal or superset; flag unknown statuses only.
        extra = status_vocab - STATUS_VOCAB
        if extra:
            findings.append(
                _error(
                    "run_json.status_vocab",
                    f"unknown status vocabulary entries: {sorted(extra)}",
                    path_label,
                )
            )

    sc = manifest.get("session_count")
    if isinstance(sc, int) and sc != len(sessions):
        findings.append(
            _error(
                "run_json.session_count",
                f"manifest.session_count={sc} != len(sessions)={len(sessions)}",
                path_label,
            )
        )

    for i, session in enumerate(sessions):
        findings.extend(_validate_session(session, index=i, path_label=path_label))

    if not findings:
        findings.append(
            _info(
                "run_json.ok",
                f"run JSON structure valid ({len(sessions)} session(s))",
                path_label,
            )
        )
    return _report(check, findings)


def _validate_session(
    session: Any, *, index: int, path_label: Optional[str]
) -> list[Finding]:
    findings: list[Finding] = []
    label = f"{path_label or 'payload'}#session[{index}]"
    if not isinstance(session, dict):
        return [_error("run_json.session_type", "session must be an object", label)]

    missing = SESSION_REQUIRED - set(session.keys())
    if missing:
        findings.append(
            _error("run_json.session_fields", f"missing {sorted(missing)}", label)
        )
        return findings

    if session.get("sut_id") not in REQUIRED_SUT_IDS and session.get("sut_id") is not None:
        # Allow only known registry ids in structured runs.
        findings.append(
            _error(
                "run_json.session_sut",
                f"unknown sut_id {session.get('sut_id')!r}",
                label,
            )
        )

    status = session.get("status")
    if status not in STATUS_VOCAB:
        findings.append(
            _error("run_json.session_status", f"invalid status {status!r}", label)
        )

    steps = session.get("steps")
    if not isinstance(steps, list) or not steps:
        findings.append(
            _error("run_json.steps", "session.steps must be a non-empty list", label)
        )
        return findings

    step_ids: list[str] = []
    for j, step in enumerate(steps):
        findings.extend(_validate_step(step, index=j, path_label=label))
        if isinstance(step, dict) and isinstance(step.get("step_id"), str):
            step_ids.append(step["step_id"])

    # When a full four-step session is present, order must match protocol.
    if len(step_ids) == 4 and tuple(step_ids) != DEFAULT_SEQUENCE:
        findings.append(
            _error(
                "run_json.step_order",
                f"step order {tuple(step_ids)} != {DEFAULT_SEQUENCE}",
                label,
            )
        )

    meta = session.get("metadata") or {}
    if isinstance(meta, dict) and "canonical" in meta:
        if not isinstance(meta["canonical"], bool):
            findings.append(
                _error("run_json.metadata_canonical", "metadata.canonical must be bool", label)
            )
        if session.get("sut_id") == "golden_lattice" and meta.get("canonical") is False:
            findings.append(
                _error(
                    "run_json.gl_canonical_meta",
                    "golden_lattice session metadata.canonical must not be false",
                    label,
                )
            )
        if session.get("sut_id") == "conventional_judge_summarizer" and meta.get("canonical") is True:
            findings.append(
                _error(
                    "run_json.judge_canonical_meta",
                    "conventional_judge_summarizer must not claim canonical=true",
                    label,
                )
            )

    return findings


def _validate_step(step: Any, *, index: int, path_label: str) -> list[Finding]:
    findings: list[Finding] = []
    label = f"{path_label}/step[{index}]"
    if not isinstance(step, dict):
        return [_error("run_json.step_type", "step must be an object", label)]

    missing = STEP_REQUIRED - set(step.keys())
    if missing:
        findings.append(_error("run_json.step_fields", f"missing {sorted(missing)}", label))

    if step.get("status") not in STATUS_VOCAB and "status" in step:
        findings.append(
            _error("run_json.step_status", f"invalid status {step.get('status')!r}", label)
        )

    if not str(step.get("perturbation_id") or "").strip() and "perturbation_id" in step:
        findings.append(
            _error("run_json.perturbation_id", "perturbation_id must be non-empty", label)
        )

    pb = step.get("prompt_bundle")
    if pb is not None and not isinstance(pb, dict):
        findings.append(
            _error("run_json.prompt_bundle", "prompt_bundle must be an object", label)
        )

    # Honesty: planned/unavailable must not fabricate raw_output in checked artifacts.
    if step.get("status") in {"planned", "unavailable"} and step.get("raw_output") not in (
        None,
        "",
    ):
        findings.append(
            _error(
                "run_json.fabricated_output",
                f"status={step.get('status')} must not carry raw_output",
                label,
            )
        )

    transitions = step.get("commitment_transitions")
    if transitions is None:
        return findings
    if not isinstance(transitions, list):
        findings.append(
            _error(
                "run_json.commitment_type",
                "commitment_transitions must be a list or null",
                label,
            )
        )
        return findings

    for k, tr in enumerate(transitions):
        tlabel = f"{label}/commitment[{k}]"
        if not isinstance(tr, dict):
            findings.append(
                _error("run_json.commitment_item", "commitment transition must be an object", tlabel)
            )
            continue
        missing_t = COMMITMENT_REQUIRED_KEYS - set(tr.keys())
        if missing_t:
            findings.append(
                _error(
                    "run_json.commitment_fields",
                    f"commitment transition missing required keys {sorted(missing_t)}; "
                    "transitions must be explicit structured records, never prose inference",
                    tlabel,
                )
            )
            continue
        # Require evidence: reason or supporting_artifact_ref
        reason_ok = isinstance(tr.get("reason"), str) and bool(tr["reason"].strip())
        ref_ok = isinstance(tr.get("supporting_artifact_ref"), str) and bool(
            tr["supporting_artifact_ref"].strip()
        )
        if not reason_ok and not ref_ok:
            findings.append(
                _error(
                    "run_json.commitment_evidence",
                    "commitment transition requires non-empty reason or supporting_artifact_ref",
                    tlabel,
                )
            )
    return findings


# ---------------------------------------------------------------------------
# (4) Roster consistency
# ---------------------------------------------------------------------------


def validate_roster_consistency(
    *,
    repo_root: Optional[Path] = None,
    doc_root: Optional[Path] = None,
) -> ValidationReport:
    repo_root = Path(repo_root) if repo_root is not None else _default_repo_root()
    doc_root = Path(doc_root) if doc_root is not None else repo_root
    findings: list[Finding] = []
    check = "roster"

    # --- Code side: live DEFAULT_INVITED_MODELS ---
    try:
        from golden_lattice.memory_graph.base import ModelId
        from golden_lattice.orchestrator import DEFAULT_INVITED_MODELS
        from golden_lattice.synthesis import attribution as attr_mod
    except Exception as exc:  # noqa: BLE001
        findings.append(_error("roster.import", f"cannot import roster surfaces: {exc}"))
        return _report(check, findings)

    expected = (ModelId.FABLE, ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU)
    if DEFAULT_INVITED_MODELS != expected:
        findings.append(
            _error(
                "roster.default_invited",
                f"DEFAULT_INVITED_MODELS={DEFAULT_INVITED_MODELS!r} != {expected!r}",
            )
        )
    if len(DEFAULT_INVITED_MODELS) != 4:
        findings.append(
            _error(
                "roster.seat_count",
                f"default roster must be four seats, got {len(DEFAULT_INVITED_MODELS)}",
            )
        )

    markers = getattr(attr_mod, "_MARKER_BY_MODEL", {})
    for seat in DEFAULT_INVITED_MODELS:
        if seat not in markers:
            findings.append(
                _error(
                    "roster.attribution_marker",
                    f"missing attribution marker for active seat {seat.name}",
                )
            )

    # TUI color map should cover active seats (soft if module absent).
    try:
        from golden_lattice.tui import colors as colors_mod

        color_map = getattr(colors_mod, "MODEL_COLORS", None) or getattr(
            colors_mod, "SEAT_COLORS", None
        )
        # Fall back to scanning module source for each seat name if no map.
        if isinstance(color_map, dict):
            for seat in DEFAULT_INVITED_MODELS:
                if seat not in color_map:
                    findings.append(
                        _error(
                            "roster.tui_color",
                            f"TUI color map missing seat {seat.name}",
                        )
                    )
    except Exception:
        pass

    # --- Doc side ---
    # Primary docs must name all four active seats.
    primary_docs = ["README.md", "ARCHITECTURE.md"]
    for rel in primary_docs:
        path = doc_root / rel
        if not path.is_file():
            findings.append(_error("roster.doc_missing", f"missing active doc {rel}", rel))
            continue
        text = path.read_text(encoding="utf-8")
        for seat_name in ACTIVE_SEAT_DISPLAY:
            if seat_name.lower() not in text.lower():
                findings.append(
                    _error(
                        "roster.doc_seat",
                        f"{rel} must document active seat {seat_name}",
                        rel,
                    )
                )

    # Scan active documentation for stale claims.
    for rel in ACTIVE_DOC_RELPATHS:
        path = doc_root / rel
        if not path.is_file():
            # Optional experiment docs may be missing in synthetic doc_root tests.
            if rel.startswith("experiments/") and doc_root != repo_root:
                continue
            if rel in {"README.md", "ARCHITECTURE.md"}:
                continue  # already reported
            continue
        text = path.read_text(encoding="utf-8")
        for tag, pattern in STALE_DOC_PATTERNS:
            if pattern.search(text):
                findings.append(
                    _error(
                        f"roster.stale.{tag}",
                        f"active documentation contains stale claim matching /{pattern.pattern}/",
                        rel,
                    )
                )

    # Test surface: a four-seat dispute test should exist (name-level check).
    orch_tests = repo_root / "tests" / "orchestrator" / "test_orchestrator.py"
    if orch_tests.is_file():
        body = orch_tests.read_text(encoding="utf-8")
        if "four_seat" not in body and "DEFAULT_INVITED_MODELS" not in body:
            findings.append(
                _error(
                    "roster.tests",
                    "orchestrator tests must exercise DEFAULT_INVITED_MODELS / four-seat roster",
                    str(orch_tests.relative_to(repo_root)),
                )
            )
    elif doc_root == repo_root:
        findings.append(
            _error("roster.tests_missing", "tests/orchestrator/test_orchestrator.py missing")
        )

    if not findings:
        findings.append(_info("roster.ok", "four-seat roster consistent across code and docs"))
    return _report(check, findings)


# ---------------------------------------------------------------------------
# (5) Constitutional invariants
# ---------------------------------------------------------------------------


def validate_constitutional_invariants(
    *, repo_root: Optional[Path] = None
) -> ValidationReport:
    repo_root = Path(repo_root) if repo_root is not None else _default_repo_root()
    findings: list[Finding] = []
    check = "constitutional"
    src = repo_root / "src" / "golden_lattice"

    engine_path = src / "synthesis" / "engine.py"
    orch_path = src / "orchestrator" / "orchestrator.py"
    schema_path = src / "memory_graph" / "schema.py"
    claim_trace_path = src / "synthesis" / "claim_trace.py"
    attr_path = src / "synthesis" / "attribution.py"

    for path in (engine_path, orch_path, schema_path, claim_trace_path, attr_path):
        if not path.is_file():
            findings.append(
                _error(
                    "constitutional.missing_source",
                    f"required source missing: {path.relative_to(repo_root)}",
                    str(path.relative_to(repo_root)),
                )
            )

    if findings:
        return _report(check, findings)

    engine = engine_path.read_text(encoding="utf-8")
    orch = orch_path.read_text(encoding="utf-8")
    schema = schema_path.read_text(encoding="utf-8")
    claim_trace = claim_trace_path.read_text(encoding="utf-8")
    attribution = attr_path.read_text(encoding="utf-8")

    # --- No hidden judge in canonical orchestration ---
    banned_engine = (
        "import anthropic",
        "from anthropic",
        "AsyncAnthropic",
        "OpenAI(",
        "submit_phase_4",
        "judge_model",
    )
    for token in banned_engine:
        if token in engine:
            findings.append(
                _error(
                    "constitutional.hidden_judge_engine",
                    f"synthesis engine must not contain {token!r} (no model judge)",
                    "src/golden_lattice/synthesis/engine.py",
                )
            )

    if "def synthesize(" not in engine:
        findings.append(
            _error(
                "constitutional.synthesize_missing",
                "engine.py must define synthesize()",
                "src/golden_lattice/synthesis/engine.py",
            )
        )
    if "no LLM" not in engine and "deterministic" not in engine.lower():
        findings.append(
            _error(
                "constitutional.synthesize_contract",
                "engine.py must document deterministic / no-LLM Phase 4 contract",
                "src/golden_lattice/synthesis/engine.py",
            )
        )

    if "artifact = synthesize(" not in orch:
        findings.append(
            _error(
                "constitutional.orch_phase4",
                "orchestrator Phase 4 must call synthesize(...)",
                "src/golden_lattice/orchestrator/orchestrator.py",
            )
        )
    if "submit_phase_4" in orch or re.search(r"judge_model\s*=", orch):
        findings.append(
            _error(
                "constitutional.orch_judge",
                "orchestrator must not dispatch a Phase 4 judge model",
                "src/golden_lattice/orchestrator/orchestrator.py",
            )
        )

    # Judge baseline must remain outside canonical path (file-level marker).
    judge_path = repo_root / "experiments" / "baselines" / "conventional_judge_summarizer.py"
    if judge_path.is_file():
        judge_src = judge_path.read_text(encoding="utf-8")
        if "NON-CANONICAL" not in judge_src and "non-canonical" not in judge_src.lower():
            findings.append(
                _error(
                    "constitutional.judge_baseline_label",
                    "conventional_judge_summarizer must be explicitly labeled non-canonical",
                    "experiments/baselines/conventional_judge_summarizer.py",
                )
            )
        if "canonical = True" in judge_src:
            findings.append(
                _error(
                    "constitutional.judge_canonical_flag",
                    "conventional_judge_summarizer must not set canonical = True",
                    "experiments/baselines/conventional_judge_summarizer.py",
                )
            )

    # --- Complete attribution trace ---
    if "def build_claim_trace" not in claim_trace:
        findings.append(
            _error(
                "constitutional.claim_trace",
                "claim_trace.py must define build_claim_trace",
                "src/golden_lattice/synthesis/claim_trace.py",
            )
        )
    if "build_claim_trace" not in engine or "claim_trace=claim_trace" not in engine:
        findings.append(
            _error(
                "constitutional.trace_wired",
                "synthesize() must wire build_claim_trace into SynthesisArtifact",
                "src/golden_lattice/synthesis/engine.py",
            )
        )
    if "_MARKER_BY_MODEL" not in attribution and "MARKER" not in attribution:
        findings.append(
            _error(
                "constitutional.attribution_markers",
                "attribution.py must define closed marker mapping",
                "src/golden_lattice/synthesis/attribution.py",
            )
        )

    # --- Explicit transition-only commitment records ---
    if "class CommitmentTransition" not in schema:
        findings.append(
            _error(
                "constitutional.commitment_type",
                "schema must define CommitmentTransition",
                "src/golden_lattice/memory_graph/schema.py",
            )
        )
    else:
        # Evidence requirements in validator docstring/messages.
        if "never inferred" not in schema.lower() and "Never inferred" not in schema:
            findings.append(
                _error(
                    "constitutional.commitment_explicit",
                    "CommitmentTransition must document that transitions are never inferred from prose",
                    "src/golden_lattice/memory_graph/schema.py",
                )
            )
        if "supporting_artifact_ref" not in schema or "source_event" not in schema:
            findings.append(
                _error(
                    "constitutional.commitment_fields",
                    "CommitmentTransition must require source_event and evidence fields",
                    "src/golden_lattice/memory_graph/schema.py",
                )
            )

    # Orchestrator accepts transitions as explicit input; must not invent from prose.
    if "commitment_transitions" not in orch:
        findings.append(
            _error(
                "constitutional.orch_transitions",
                "orchestrator must accept commitment_transitions as explicit input",
                "src/golden_lattice/orchestrator/orchestrator.py",
            )
        )
    # Guard against a prose-inference helper name if introduced later.
    if re.search(r"infer\w*commitment", orch, re.IGNORECASE):
        findings.append(
            _error(
                "constitutional.no_infer_commitment",
                "orchestrator must not infer commitment transitions from prose",
                "src/golden_lattice/orchestrator/orchestrator.py",
            )
        )

    if not findings:
        findings.append(
            _info(
                "constitutional.ok",
                "canonical path is judge-free; attribution trace and explicit transitions present",
            )
        )
    return _report(check, findings)


# ---------------------------------------------------------------------------
# Aggregate + checked-in run samples
# ---------------------------------------------------------------------------


def validate_checked_in_runs(*, repo_root: Optional[Path] = None) -> ValidationReport:
    """Validate any JSON run artifacts committed under experiments/baselines/runs/."""
    repo_root = Path(repo_root) if repo_root is not None else _default_repo_root()
    runs_dir = repo_root / "experiments" / "baselines" / "runs"
    findings: list[Finding] = []
    check = "run_json.checked_in"

    if not runs_dir.is_dir():
        return _report(
            check,
            [_info("run_json.no_checked_in", f"no checked-in runs dir at {runs_dir}")],
        )

    paths = sorted(runs_dir.glob("*.json"))
    if not paths:
        return _report(
            check,
            [_info("run_json.no_checked_in", "runs dir exists but has no JSON samples")],
        )

    reports = [validate_run_json(p) for p in paths]
    merged = ValidationReport(findings=(), checks_run=(check,))
    for r in reports:
        merged = merged.merge(r)
    # Re-tag checks_run to single aggregate entry + keep findings.
    return ValidationReport(findings=merged.findings, checks_run=(check,))


def validate_all(
    *,
    repo_root: Optional[Path] = None,
    run_json: Optional[Sequence[RunJsonInput]] = None,
) -> ValidationReport:
    repo_root = Path(repo_root) if repo_root is not None else _default_repo_root()
    reports = [
        validate_corpus(repo_root=repo_root),
        validate_baseline_registry(repo_root=repo_root),
        validate_roster_consistency(repo_root=repo_root),
        validate_constitutional_invariants(repo_root=repo_root),
        validate_checked_in_runs(repo_root=repo_root),
    ]
    if run_json:
        for item in run_json:
            reports.append(validate_run_json(item))
    elif not any(r.checks_run and r.checks_run[0] == "run_json.checked_in" and r.findings for r in reports[-1:]):
        pass

    merged = ValidationReport()
    for r in reports:
        merged = merged.merge(r)
    return merged


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m experiments.validation",
        description=(
            "Validate Golden Lattice corpus, baseline registry, run JSON, "
            "four-seat roster docs, and constitutional invariants."
        ),
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=None,
        help="Repository root (default: auto-detect from package location)",
    )
    parser.add_argument(
        "--run-json",
        action="append",
        default=[],
        type=Path,
        help="Optional run JSON path to validate (repeatable). "
        "Checked-in experiments/baselines/runs/*.json are always validated.",
    )
    parser.add_argument(
        "--corpus-only",
        action="store_true",
        help="Run only corpus checks",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Print only the final status line",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    repo = args.repo if args.repo is not None else _default_repo_root()

    if args.corpus_only:
        report = validate_corpus(repo_root=repo)
    else:
        extra = list(args.run_json) if args.run_json else None
        report = validate_all(repo_root=repo, run_json=extra)

    if not args.quiet:
        print(report.summary())
    if report.ok:
        print("OK — validation passed")
        return 0
    print(f"FAIL — {len(report.errors())} error(s)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
