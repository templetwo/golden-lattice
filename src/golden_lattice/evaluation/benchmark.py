"""Benchmark task sets with manifest verification (eval harness, step 1).

The benchmark is split into one file per split: ``dev.json`` and
``heldout.json``. A dev run never opens the heldout file, so held-out
prompts cannot leak into tuning by accident. Each file is listed in
``MANIFEST.sha256`` (sha256sum format); the loader refuses any file whose
bytes do not match its manifest entry.

A set carries ``status``. ``placeholder`` marks a set that is scaffolding
and must not be used as evidence. Only ``frozen`` sets back a report.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

MIN_WORDS = 150
MAX_WORDS = 400
MANIFEST_NAME = "MANIFEST.sha256"
SPLIT_FILES = {"dev": "dev.json", "heldout": "heldout.json"}

TaskKind = Literal["system_design", "code_review", "decision_analysis"]


class BenchmarkError(ValueError):
    """Base class for benchmark loading failures."""


class ManifestMismatch(BenchmarkError):
    """A benchmark file does not match its manifest entry."""


class Task(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    kind: TaskKind
    prompt: str

    @field_validator("prompt")
    @classmethod
    def _word_bounds(cls, value: str) -> str:
        words = len(value.split())
        if not MIN_WORDS <= words <= MAX_WORDS:
            raise ValueError(
                f"prompt has {words} words; benchmark tasks must be "
                f"{MIN_WORDS} to {MAX_WORDS}"
            )
        return value


class TaskSet(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = Field(ge=1)
    split: Literal["dev", "heldout"]
    status: Literal["placeholder", "frozen"]
    tasks: tuple[Task, ...]

    @field_validator("tasks")
    @classmethod
    def _unique_ids(cls, tasks: tuple[Task, ...]) -> tuple[Task, ...]:
        ids = [task.id for task in tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("task ids must be unique within a split")
        if not ids:
            raise ValueError("a task set must contain at least one task")
        return tasks


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_manifest(directory: Path) -> dict[str, str]:
    """Return {filename: sha256} from MANIFEST.sha256 in sha256sum format."""
    manifest_path = directory / MANIFEST_NAME
    if not manifest_path.is_file():
        raise BenchmarkError(f"missing manifest: {manifest_path}")
    entries: dict[str, str] = {}
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, _, name = line.partition("  ")
        if len(digest) != 64 or not name:
            raise BenchmarkError(f"malformed manifest line: {line!r}")
        entries[name] = digest
    return entries


def load_split(directory: Path, split: Literal["dev", "heldout"]) -> TaskSet:
    """Load one split, verifying its bytes against the manifest first."""
    filename = SPLIT_FILES[split]
    path = directory / filename
    if not path.is_file():
        raise BenchmarkError(f"split file not present: {path}")
    entries = read_manifest(directory)
    if filename not in entries:
        raise BenchmarkError(f"{filename} has no manifest entry; refusing to load")
    raw = path.read_bytes()
    if sha256_bytes(raw) != entries[filename]:
        raise ManifestMismatch(f"{filename} does not match {MANIFEST_NAME}")
    try:
        task_set = TaskSet.model_validate(json.loads(raw))
    except (ValidationError, json.JSONDecodeError) as exc:
        raise BenchmarkError(f"{filename} is invalid: {exc}") from exc
    if task_set.split != split:
        raise BenchmarkError(f"{filename} declares split {task_set.split!r}")
    return task_set
