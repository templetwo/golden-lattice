"""Lightweight validation for the longitudinal experiment task corpus.

Task 7 corpus only — no baseline runners, no model calls, no synthesis changes.
See experiments/README.md for the schema contract.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS_DIR = REPO_ROOT / "experiments" / "tasks"

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

REQUIRED_CLAIM_FIELDS = frozenset({"id", "text", "prompt"})
REQUIRED_CONDITION_FIELDS = frozenset({"id", "text", "prompt", "kind"})
ALLOWED_CONDITION_KINDS = frozenset(
    {"challenge", "evidence_update", "reversal", "removal"}
)

DEFAULT_SEQUENCE = (
    "present_initial_claim",
    "apply_controlled_challenge",
    "apply_evidence_update",
    "apply_reversal_or_removal",
)

STABLE_ID_RE = re.compile(r"^gl\.longitudinal\.[a-z0-9_]+\.v\d+$")
NESTED_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.]*$", re.IGNORECASE)

# Phrases that should appear in the cognition disclaimer (case-insensitive).
DISCLAIMER_MARKERS = (
    "not",
    "cognition",
)


def _task_paths() -> list[Path]:
    if not TASKS_DIR.is_dir():
        return []
    return sorted(TASKS_DIR.glob("*.toml"))


def _load(path: Path) -> dict:
    with path.open("rb") as f:
        data = tomllib.load(f)
    if not isinstance(data, dict):
        raise AssertionError(f"{path.name}: root value must be a table")
    return data


@pytest.fixture(scope="module")
def task_files() -> list[Path]:
    paths = _task_paths()
    assert paths, f"no task TOML files found under {TASKS_DIR}"
    return paths


@pytest.fixture(scope="module")
def tasks_by_path(task_files: list[Path]) -> dict[Path, dict]:
    return {path: _load(path) for path in task_files}


def test_experiments_docs_exist() -> None:
    experiments = REPO_ROOT / "experiments"
    assert (experiments / "README.md").is_file()
    assert (experiments / "protocol.md").is_file()
    assert TASKS_DIR.is_dir()


def test_exactly_four_task_files(task_files: list[Path]) -> None:
    assert len(task_files) == 4, (
        f"expected 4 task files, found {len(task_files)}: "
        f"{[p.name for p in task_files]}"
    )


def test_all_four_categories_present(tasks_by_path: dict[Path, dict]) -> None:
    categories = {task["category"] for task in tasks_by_path.values()}
    assert categories == REQUIRED_CATEGORIES, (
        f"category set mismatch: got {sorted(categories)}, "
        f"expected {sorted(REQUIRED_CATEGORIES)}"
    )


def test_category_unique_per_corpus(tasks_by_path: dict[Path, dict]) -> None:
    seen: dict[str, Path] = {}
    for path, task in tasks_by_path.items():
        cat = task["category"]
        assert cat not in seen, f"duplicate category {cat!r}: {seen[cat].name} and {path.name}"
        seen[cat] = path


@pytest.mark.parametrize("path", _task_paths(), ids=lambda p: p.name)
def test_required_top_level_fields(path: Path) -> None:
    task = _load(path)
    missing = REQUIRED_TOP_LEVEL - task.keys()
    assert not missing, f"{path.name}: missing top-level fields {sorted(missing)}"


@pytest.mark.parametrize("path", _task_paths(), ids=lambda p: p.name)
def test_stable_ids_and_version(path: Path) -> None:
    task = _load(path)
    assert isinstance(task["id"], str) and STABLE_ID_RE.match(task["id"]), (
        f"{path.name}: id {task['id']!r} must match {STABLE_ID_RE.pattern}"
    )
    assert isinstance(task["version"], int) and task["version"] >= 1
    assert task["category"] in REQUIRED_CATEGORIES
    assert isinstance(task["title"], str) and task["title"].strip()
    assert isinstance(task["summary"], str) and len(task["summary"].strip()) >= 40


@pytest.mark.parametrize("path", _task_paths(), ids=lambda p: p.name)
def test_cognition_disclaimer_is_explicit(path: Path) -> None:
    task = _load(path)
    text = task["cognition_disclaimer"]
    assert isinstance(text, str) and len(text.strip()) >= 40
    lowered = text.lower()
    for marker in DISCLAIMER_MARKERS:
        assert marker in lowered, (
            f"{path.name}: cognition_disclaimer must note these are not cognition labels "
            f"(missing {marker!r})"
        )
    # Stronger check: disclaimer should deny that fields are cognition labels.
    assert "prompt" in lowered or "condition" in lowered or "protocol" in lowered


@pytest.mark.parametrize("path", _task_paths(), ids=lambda p: p.name)
def test_expected_perturbation_sequence(path: Path) -> None:
    task = _load(path)
    seq = task["expected_perturbation_sequence"]
    assert isinstance(seq, list) and seq, f"{path.name}: sequence must be non-empty list"
    assert all(isinstance(step, str) and step.strip() for step in seq)
    assert tuple(seq) == DEFAULT_SEQUENCE, (
        f"{path.name}: v1 corpus expects {DEFAULT_SEQUENCE}, got {tuple(seq)}"
    )


@pytest.mark.parametrize("path", _task_paths(), ids=lambda p: p.name)
def test_initial_claim_fields(path: Path) -> None:
    task = _load(path)
    claim = task["initial_claim"]
    assert isinstance(claim, dict)
    missing = REQUIRED_CLAIM_FIELDS - claim.keys()
    assert not missing, f"{path.name}: initial_claim missing {sorted(missing)}"
    assert NESTED_ID_RE.match(claim["id"]), f"{path.name}: bad initial_claim.id"
    assert claim["text"].strip() and claim["prompt"].strip()
    assert "{claim_text}" in claim["prompt"], (
        f"{path.name}: initial_claim.prompt should include {{claim_text}} placeholder"
    )


@pytest.mark.parametrize(
    ("path", "field", "expected_kinds"),
    [
        (p, "controlled_challenge", frozenset({"challenge"}))
        for p in _task_paths()
    ]
    + [
        (p, "evidence_update", frozenset({"evidence_update"}))
        for p in _task_paths()
    ]
    + [
        (p, "reversal_or_removal", frozenset({"reversal", "removal"}))
        for p in _task_paths()
    ],
    ids=lambda x: x if isinstance(x, str) else getattr(x, "name", str(x)),
)
def test_condition_tables(
    path: Path, field: str, expected_kinds: frozenset[str]
) -> None:
    task = _load(path)
    cond = task[field]
    assert isinstance(cond, dict)
    missing = REQUIRED_CONDITION_FIELDS - cond.keys()
    assert not missing, f"{path.name}: {field} missing {sorted(missing)}"
    assert NESTED_ID_RE.match(cond["id"]), f"{path.name}: bad {field}.id"
    assert cond["kind"] in ALLOWED_CONDITION_KINDS
    assert cond["kind"] in expected_kinds, (
        f"{path.name}: {field}.kind={cond['kind']!r} not in {sorted(expected_kinds)}"
    )
    assert cond["text"].strip() and cond["prompt"].strip()


@pytest.mark.parametrize("path", _task_paths(), ids=lambda p: p.name)
def test_condition_prompt_placeholders(path: Path) -> None:
    task = _load(path)
    assert "{challenge_text}" in task["controlled_challenge"]["prompt"]
    assert "{evidence_text}" in task["evidence_update"]["prompt"]
    assert "{reversal_text}" in task["reversal_or_removal"]["prompt"]


@pytest.mark.parametrize("path", _task_paths(), ids=lambda p: p.name)
def test_nested_ids_unique_within_task(path: Path) -> None:
    task = _load(path)
    ids = [
        task["initial_claim"]["id"],
        task["controlled_challenge"]["id"],
        task["evidence_update"]["id"],
        task["reversal_or_removal"]["id"],
    ]
    assert len(ids) == len(set(ids)), f"{path.name}: nested ids must be unique, got {ids}"


def test_task_ids_unique_across_corpus(tasks_by_path: dict[Path, dict]) -> None:
    ids = [task["id"] for task in tasks_by_path.values()]
    assert len(ids) == len(set(ids))


def test_no_provider_lock_in_task_bodies(tasks_by_path: dict[Path, dict]) -> None:
    """Corpus must stay provider-neutral for later baseline runners."""
    banned = (
        "anthropic",
        "openai",
        "api.x.ai",
        "claude-opus",
        "gpt-4",
        "gemini-",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
    )
    for path, task in tasks_by_path.items():
        blob = str(task).lower()
        for token in banned:
            assert token.lower() not in blob, (
                f"{path.name}: provider-specific token {token!r} found in task body"
            )
