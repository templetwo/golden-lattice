"""Benchmark loader refusals (eval harness step 1).

Each test pins one refusal: a tampered file, a missing manifest, out-of-bounds
prompts, duplicate ids, and a split file that has no manifest entry.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from golden_lattice.evaluation.benchmark import (
    BenchmarkError,
    ManifestMismatch,
    Task,
    TaskSet,
    load_split,
    sha256_bytes,
)

REPO_BENCHMARKS = Path(__file__).resolve().parents[2] / "benchmarks"


def _words(n: int) -> str:
    return " ".join(["word"] * n)


def _write_set(directory: Path, split: str, payload: dict, *, manifest: bool = True) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload).encode("utf-8")
    (directory / f"{split}.json").write_bytes(raw)
    if manifest:
        (directory / "MANIFEST.sha256").write_text(f"{sha256_bytes(raw)}  {split}.json\n")


def _payload(split: str = "dev", n_words: int = 160, ids=("t-1",)) -> dict:
    return {
        "version": 1,
        "split": split,
        "status": "frozen",
        "tasks": [
            {"id": tid, "kind": "system_design", "prompt": _words(n_words)} for tid in ids
        ],
    }


def test_committed_dev_split_verifies_against_manifest():
    task_set = load_split(REPO_BENCHMARKS, "dev")
    assert task_set.split == "dev"
    assert len(task_set.tasks) == 5
    assert task_set.status == "placeholder"


def test_tampered_dev_file_refused(tmp_path):
    shutil.copytree(REPO_BENCHMARKS, tmp_path / "bench")
    target = tmp_path / "bench" / "dev.json"
    target.write_bytes(target.read_bytes() + b" ")
    with pytest.raises(ManifestMismatch):
        load_split(tmp_path / "bench", "dev")


def test_missing_manifest_refused(tmp_path):
    _write_set(tmp_path, "dev", _payload(), manifest=False)
    with pytest.raises(BenchmarkError, match="missing manifest"):
        load_split(tmp_path, "dev")


def test_split_without_manifest_entry_refused(tmp_path):
    _write_set(tmp_path, "dev", _payload())
    _write_set(tmp_path, "heldout", _payload("heldout"), manifest=False)
    with pytest.raises(BenchmarkError, match="no manifest entry"):
        load_split(tmp_path, "heldout")


def test_missing_split_file_refused(tmp_path):
    _write_set(tmp_path, "dev", _payload())
    with pytest.raises(BenchmarkError, match="not present"):
        load_split(tmp_path, "heldout")


def test_prompt_below_word_floor_refused():
    with pytest.raises(ValidationError, match="words"):
        Task(id="short", kind="system_design", prompt=_words(149))


def test_prompt_above_word_ceiling_refused():
    with pytest.raises(ValidationError, match="words"):
        Task(id="long", kind="system_design", prompt=_words(401))


def test_prompt_at_bounds_accepted():
    assert Task(id="lo", kind="system_design", prompt=_words(150)).id == "lo"
    assert Task(id="hi", kind="system_design", prompt=_words(400)).id == "hi"


def test_duplicate_ids_refused():
    with pytest.raises(ValidationError, match="unique"):
        TaskSet(
            version=1,
            split="dev",
            status="frozen",
            tasks=(
                Task(id="same", kind="system_design", prompt=_words(160)),
                Task(id="same", kind="code_review", prompt=_words(160)),
            ),
        )


def test_unknown_fields_refused():
    with pytest.raises(ValidationError):
        Task(id="x", kind="system_design", prompt=_words(160), weight=2)


def test_split_declared_mismatch_refused(tmp_path):
    _write_set(tmp_path, "dev", _payload(split="heldout"))
    with pytest.raises(BenchmarkError, match="declares split"):
        load_split(tmp_path, "dev")
