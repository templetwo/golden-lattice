"""Phase 2 Task 10 — executable lattice / experiment validation suite.

Covers:
1. Corpus task IDs, fields, step ordering, perturbation IDs, reversal/removal
2. Baseline SUT registry completeness + canonical/non-canonical metadata
3. Report/run JSON structural integrity
4. Four-seat roster consistency across code/docs/tests (no stale triadic-default claims)
5. Constitutional invariants (no hidden judge, attribution trace, explicit transitions)

No network. Does not overfit to /tmp-generated artifacts — checks in-repo
corpus, checked-in run JSON samples, and source/doc surfaces.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS_DIR = REPO_ROOT / "experiments" / "tasks"
RUNS_DIR = REPO_ROOT / "experiments" / "baselines" / "runs"
DRY_RUN_JSON = RUNS_DIR / "task8_dryrun_all.json"

REQUIRED_SUTS = (
    "strongest_single_peer",
    "simple_parallel_responses",
    "conventional_judge_summarizer",
    "golden_lattice",
)

DEFAULT_SEQUENCE = (
    "present_initial_claim",
    "apply_controlled_challenge",
    "apply_evidence_update",
    "apply_reversal_or_removal",
)

ACTIVE_SEAT_NAMES = ("Fable", "Opus", "Sonnet", "Haiku")


def _import_validation():
    from experiments.validation import (  # noqa: WPS433
        Finding,
        ValidationReport,
        main,
        validate_all,
        validate_baseline_registry,
        validate_constitutional_invariants,
        validate_corpus,
        validate_roster_consistency,
        validate_run_json,
    )

    return {
        "Finding": Finding,
        "ValidationReport": ValidationReport,
        "main": main,
        "validate_all": validate_all,
        "validate_baseline_registry": validate_baseline_registry,
        "validate_constitutional_invariants": validate_constitutional_invariants,
        "validate_corpus": validate_corpus,
        "validate_roster_consistency": validate_roster_consistency,
        "validate_run_json": validate_run_json,
    }


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def test_validation_module_exports_expected_api() -> None:
    api = _import_validation()
    assert callable(api["validate_all"])
    assert callable(api["validate_corpus"])
    assert callable(api["validate_baseline_registry"])
    assert callable(api["validate_run_json"])
    assert callable(api["validate_roster_consistency"])
    assert callable(api["validate_constitutional_invariants"])
    assert callable(api["main"])
    report = api["ValidationReport"](findings=())
    assert report.ok is True


# ---------------------------------------------------------------------------
# (1) Corpus
# ---------------------------------------------------------------------------


def test_validate_corpus_passes_on_repo_tasks() -> None:
    api = _import_validation()
    report = api["validate_corpus"](repo_root=REPO_ROOT)
    assert isinstance(report, api["ValidationReport"])
    assert report.ok, report.summary()
    # At least one finding of severity info is fine; errors are not.
    assert not report.errors()


def test_validate_corpus_requires_four_categories_and_sequence(tmp_path: Path) -> None:
    api = _import_validation()
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    # Incomplete corpus: one task, truncated sequence, missing reversal table.
    (tasks / "only.toml").write_text(
        "\n".join(
            [
                'id = "gl.longitudinal.broken.v1"',
                "version = 1",
                'category = "design_critique"',
                'title = "broken"',
                'summary = "' + ("x" * 50) + '"',
                'cognition_disclaimer = "These are not cognition labels; protocol conditions only."',
                "expected_perturbation_sequence = [",
                '  "present_initial_claim",',
                '  "apply_controlled_challenge",',
                '  "apply_evidence_update",',
                "]",
                "[initial_claim]",
                'id = "b.initial"',
                'text = "claim"',
                'prompt = "P {claim_text}"',
                "[controlled_challenge]",
                'id = "b.challenge"',
                'kind = "challenge"',
                'text = "c"',
                'prompt = "P {challenge_text}"',
                "[evidence_update]",
                'id = "b.evidence"',
                'kind = "evidence_update"',
                'text = "e"',
                'prompt = "P {evidence_text}"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    report = api["validate_corpus"](repo_root=tmp_path, tasks_dir=tasks)
    assert not report.ok
    messages = " ".join(f.message for f in report.errors()).lower()
    assert "sequence" in messages or "reversal" in messages or "category" in messages or "missing" in messages


def test_validate_corpus_rejects_duplicate_perturbation_ids(tmp_path: Path) -> None:
    api = _import_validation()
    tasks = tmp_path / "tasks"
    tasks.mkdir()

    def _write_task(
        filename: str,
        *,
        task_id: str,
        category: str,
        claim_id: str,
        challenge_id: str,
        evidence_id: str,
        reversal_id: str,
    ) -> None:
        (tasks / filename).write_text(
            "\n".join(
                [
                    f'id = "{task_id}"',
                    "version = 1",
                    f'category = "{category}"',
                    'title = "t"',
                    'summary = "' + ("x" * 50) + '"',
                    'cognition_disclaimer = "not cognition labels; protocol conditions only."',
                    "expected_perturbation_sequence = [",
                    '  "present_initial_claim",',
                    '  "apply_controlled_challenge",',
                    '  "apply_evidence_update",',
                    '  "apply_reversal_or_removal",',
                    "]",
                    "[initial_claim]",
                    f'id = "{claim_id}"',
                    'text = "claim"',
                    'prompt = "P {claim_text}"',
                    "[controlled_challenge]",
                    f'id = "{challenge_id}"',
                    'kind = "challenge"',
                    'text = "c"',
                    'prompt = "P {challenge_text}"',
                    "[evidence_update]",
                    f'id = "{evidence_id}"',
                    'kind = "evidence_update"',
                    'text = "e"',
                    'prompt = "P {evidence_text}"',
                    "[reversal_or_removal]",
                    f'id = "{reversal_id}"',
                    'kind = "removal"',
                    'text = "r"',
                    'prompt = "P {reversal_text}"',
                    "",
                ]
            ),
            encoding="utf-8",
        )

    # One task with deliberately duplicated nested ids.
    _write_task(
        "dup.toml",
        task_id="gl.longitudinal.dup.v1",
        category="design_critique",
        claim_id="same.id",
        challenge_id="same.id",
        evidence_id="e.id",
        reversal_id="r.id",
    )
    # Other three categories so category completeness is not the only fail.
    extras = (
        ("ok0.toml", "gl.longitudinal.ok0.v1", "competing_scientific_explanations"),
        ("ok1.toml", "gl.longitudinal.ok1.v1", "ambiguous_evidence_synthesis"),
        ("ok2.toml", "gl.longitudinal.ok2.v1", "decision_under_changing_evidence"),
    )
    for filename, tid, cat in extras:
        prefix = tid.split(".")[-2]
        _write_task(
            filename,
            task_id=tid,
            category=cat,
            claim_id=f"{prefix}.a",
            challenge_id=f"{prefix}.c",
            evidence_id=f"{prefix}.e",
            reversal_id=f"{prefix}.r",
        )

    report = api["validate_corpus"](repo_root=tmp_path, tasks_dir=tasks)
    assert not report.ok
    assert any(
        "unique" in f.message.lower() or "duplicate" in f.message.lower()
        for f in report.errors()
    )


def test_validate_corpus_requires_reversal_or_removal_kind_coverage() -> None:
    """Repo corpus must include at least one reversal and one removal across tasks."""
    api = _import_validation()
    report = api["validate_corpus"](repo_root=REPO_ROOT)
    assert report.ok, report.summary()
    # Corpus-level coverage finding should not error; kinds must span both.
    kinds = set()
    import tomllib

    for path in TASKS_DIR.glob("*.toml"):
        with path.open("rb") as fh:
            task = tomllib.load(fh)
        kinds.add(task["reversal_or_removal"]["kind"])
    assert "reversal" in kinds
    assert "removal" in kinds


# ---------------------------------------------------------------------------
# (2) Baseline SUT registry
# ---------------------------------------------------------------------------


def test_validate_baseline_registry_passes_on_repo() -> None:
    api = _import_validation()
    report = api["validate_baseline_registry"](repo_root=REPO_ROOT)
    assert report.ok, report.summary()


def test_validate_baseline_registry_requires_four_suts_and_flags() -> None:
    api = _import_validation()
    report = api["validate_baseline_registry"](repo_root=REPO_ROOT)
    assert report.ok
    # Spot-check via registry import that validator would have required.
    from experiments.baselines import REQUIRED_SUT_IDS, get_sut

    assert tuple(REQUIRED_SUT_IDS) == REQUIRED_SUTS
    assert get_sut("golden_lattice").canonical is True
    assert get_sut("golden_lattice").optional is False
    assert get_sut("conventional_judge_summarizer").canonical is False
    assert get_sut("conventional_judge_summarizer").optional is True
    # Required non-judge comparators declare explicit metadata (canonical flag
    # means "primary comparison foil", not "Phase 4 lattice path").
    for sid in ("strongest_single_peer", "simple_parallel_responses"):
        sut = get_sut(sid)
        assert isinstance(sut.canonical, bool)
        assert sut.optional is False
        assert hasattr(sut, "canonical")


# ---------------------------------------------------------------------------
# (3) Run / report JSON structure
# ---------------------------------------------------------------------------


def test_validate_run_json_accepts_checked_in_dry_run() -> None:
    api = _import_validation()
    assert DRY_RUN_JSON.is_file()
    report = api["validate_run_json"](DRY_RUN_JSON)
    assert report.ok, report.summary()


def test_validate_run_json_accepts_payload_dict() -> None:
    api = _import_validation()
    payload = json.loads(DRY_RUN_JSON.read_text(encoding="utf-8"))
    report = api["validate_run_json"](payload)
    assert report.ok, report.summary()


def test_validate_run_json_rejects_missing_manifest_keys() -> None:
    api = _import_validation()
    payload = json.loads(DRY_RUN_JSON.read_text(encoding="utf-8"))
    bad = deepcopy(payload)
    del bad["manifest"]["run_id"]
    del bad["manifest"]["sut_ids"]
    report = api["validate_run_json"](bad)
    assert not report.ok
    joined = " ".join(f.message for f in report.errors()).lower()
    assert "run_id" in joined or "sut_ids" in joined or "manifest" in joined


def test_validate_run_json_rejects_session_without_steps() -> None:
    api = _import_validation()
    payload = json.loads(DRY_RUN_JSON.read_text(encoding="utf-8"))
    bad = deepcopy(payload)
    bad["sessions"][0]["steps"] = []
    report = api["validate_run_json"](bad)
    assert not report.ok


def test_validate_run_json_rejects_fabricated_commitment_without_fields() -> None:
    api = _import_validation()
    payload = json.loads(DRY_RUN_JSON.read_text(encoding="utf-8"))
    bad = deepcopy(payload)
    # Illegal: transition object missing required structural fields.
    bad["sessions"][0]["steps"][0]["commitment_transitions"] = [{"note": "inferred from prose"}]
    report = api["validate_run_json"](bad)
    assert not report.ok
    assert any("commitment" in f.message.lower() for f in report.errors())


def test_validate_run_json_does_not_require_tmp_artifacts(tmp_path: Path) -> None:
    """Validator must not hard-depend on /tmp paths."""
    api = _import_validation()
    # Empty runs dir still allows validating an explicit payload.
    payload = {
        "manifest": {
            "run_id": "synthetic",
            "mode": "dry_run",
            "created_at": "2026-08-06T00:00:00+00:00",
            "sut_ids": list(REQUIRED_SUTS),
            "task_ids": ["gl.longitudinal.design_critique.v1"],
            "session_count": 0,
            "status_vocabulary": [
                "planned",
                "completed",
                "unavailable",
                "skipped",
                "aborted",
                "error",
            ],
        },
        "sessions": [],
    }
    report = api["validate_run_json"](payload)
    # Zero sessions is a structural warning/error depending on policy —
    # must not raise FileNotFoundError looking under /tmp.
    assert isinstance(report, api["ValidationReport"])


# ---------------------------------------------------------------------------
# (4) Four-seat roster consistency
# ---------------------------------------------------------------------------


def test_validate_roster_consistency_passes_on_current_repo() -> None:
    api = _import_validation()
    report = api["validate_roster_consistency"](repo_root=REPO_ROOT)
    assert report.ok, report.summary()


def test_default_invited_models_are_four_active_seats() -> None:
    from golden_lattice.memory_graph.base import ModelId
    from golden_lattice.orchestrator import DEFAULT_INVITED_MODELS

    assert DEFAULT_INVITED_MODELS == (
        ModelId.FABLE,
        ModelId.OPUS,
        ModelId.SONNET,
        ModelId.HAIKU,
    )
    assert len(DEFAULT_INVITED_MODELS) == 4


def test_validate_roster_flags_stale_triadic_default_claim(tmp_path: Path) -> None:
    api = _import_validation()
    # Minimal fake repo surfaces for the roster checker.
    (tmp_path / "README.md").write_text(
        "Default roster is triadic-only: Opus, Sonnet, and Haiku as the three peers.\n",
        encoding="utf-8",
    )
    (tmp_path / "ARCHITECTURE.md").write_text(
        "A CLI that runs three models.\n",
        encoding="utf-8",
    )
    src = tmp_path / "src" / "golden_lattice" / "orchestrator"
    src.mkdir(parents=True)
    # Pointing at real code still needed for DEFAULT_INVITED — checker uses
    # import of live package for code side; doc side uses tmp_path.
    report = api["validate_roster_consistency"](
        repo_root=REPO_ROOT,
        doc_root=tmp_path,
    )
    assert not report.ok
    joined = " ".join(f.message.lower() for f in report.errors())
    assert "triadic" in joined or "three" in joined or "fable" in joined


def test_attribution_markers_cover_active_roster() -> None:
    from golden_lattice.orchestrator import DEFAULT_INVITED_MODELS
    from golden_lattice.synthesis import attribution as attr_mod

    markers = getattr(attr_mod, "_MARKER_BY_MODEL", None)
    assert isinstance(markers, dict) and markers, "attribution marker map missing"
    for seat in DEFAULT_INVITED_MODELS:
        assert seat in markers, f"missing attribution marker for {seat}"


# ---------------------------------------------------------------------------
# (5) Constitutional invariants
# ---------------------------------------------------------------------------


def test_validate_constitutional_invariants_passes_on_repo() -> None:
    api = _import_validation()
    report = api["validate_constitutional_invariants"](repo_root=REPO_ROOT)
    assert report.ok, report.summary()


def test_synthesize_has_no_model_client_imports() -> None:
    """Static guard: Phase 4 engine must not pull provider clients."""
    engine = (
        REPO_ROOT / "src" / "golden_lattice" / "synthesis" / "engine.py"
    ).read_text(encoding="utf-8")
    banned = ("import anthropic", "from anthropic", "AsyncAnthropic", "OpenAI(")
    for token in banned:
        assert token not in engine
    assert "no LLM" in engine or "deterministic" in engine.lower()


def test_orchestrator_phase4_calls_synthesize_not_judge() -> None:
    orch = (
        REPO_ROOT / "src" / "golden_lattice" / "orchestrator" / "orchestrator.py"
    ).read_text(encoding="utf-8")
    assert "artifact = synthesize(" in orch
    # Must not dispatch a seat/model call for Phase 4 judging.
    assert "submit_phase_4" not in orch
    assert "judge_model" not in orch


def test_commitment_transition_refuses_empty_evidence() -> None:
    from datetime import datetime, timezone

    import pytest
    from pydantic import ValidationError

    from golden_lattice.memory_graph.base import CommitmentState, ModelId
    from golden_lattice.memory_graph.schema import CommitmentTransition

    with pytest.raises(ValidationError):
        CommitmentTransition(
            claim_id="abcd1234abcd1234",
            source_model=ModelId.OPUS,
            prior_state=CommitmentState.PROPOSED,
            next_state=CommitmentState.CHALLENGED,
            source_event="evt",
            reason=None,
            supporting_artifact_ref=None,
            sequence_index=0,
            occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )


def test_claim_trace_is_required_on_synthesis_artifact_path() -> None:
    """build_claim_trace is the attribution/irreducibility surface."""
    from golden_lattice.synthesis import build_claim_trace, synthesize

    assert callable(build_claim_trace)
    assert callable(synthesize)
    engine_src = (
        REPO_ROOT / "src" / "golden_lattice" / "synthesis" / "engine.py"
    ).read_text(encoding="utf-8")
    assert "build_claim_trace" in engine_src
    assert "claim_trace=claim_trace" in engine_src


# ---------------------------------------------------------------------------
# Aggregate + CLI
# ---------------------------------------------------------------------------


def test_validate_all_passes_on_repo() -> None:
    api = _import_validation()
    report = api["validate_all"](repo_root=REPO_ROOT)
    assert report.ok, report.summary()
    # Covers all five check groups.
    check_ids = {f.check_id.split(".")[0] for f in report.findings} | {
        c for c in report.checks_run
    }
    for prefix in ("corpus", "baseline", "run_json", "roster", "constitutional"):
        assert any(prefix in c for c in check_ids) or prefix in " ".join(check_ids), (
            f"missing check group {prefix}: {check_ids}"
        )


def test_cli_main_exits_zero_on_clean_repo(capsys: pytest.CaptureFixture[str]) -> None:
    api = _import_validation()
    code = api["main"](["--repo", str(REPO_ROOT)])
    captured = capsys.readouterr()
    assert code == 0, captured.out + captured.err
    assert "OK" in captured.out or "passed" in captured.out.lower()


def test_cli_main_exits_nonzero_on_bad_run_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = _import_validation()
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"nope": True}), encoding="utf-8")
    code = api["main"](["--repo", str(REPO_ROOT), "--run-json", str(bad)])
    captured = capsys.readouterr()
    assert code != 0
    assert "error" in (captured.out + captured.err).lower() or "FAIL" in (
        captured.out + captured.err
    )
