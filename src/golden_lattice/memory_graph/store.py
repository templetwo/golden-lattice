"""Session persistence for the Memory Graph.

Plain-text legibility is the load-bearing feature, not a compromise. JSON files on
disk preserve continuity with how the Sovereign Stack persists work — sessions
remain inspectable with cat, grep, and a text editor. The SessionStore Protocol
abstracts queries the upstream code needs so a SQLite or remote backend can layer
on later without rewriting callers; the JSON export remains as a parallel artifact
even after that, so legibility never erodes.

Cross-session queries (parity_history, alignment_pair_history) are how the Memory
Graph functions as the protocol-drift detector across many sessions, not just within
one. Single-session skew is noisy; alignment drift is a pattern.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Iterator, Optional, Protocol, runtime_checkable

from golden_lattice.memory_graph.base import ModelId
from golden_lattice.memory_graph.metrics import (
    compute_consensus_pair_distribution,
    compute_parity_shares,
)
from golden_lattice.memory_graph.schema import Session, SessionMetrics


@runtime_checkable
class SessionStore(Protocol):
    """The query surface the Memory Graph needs across sessions.

    Implementations: JsonFileSessionStore (this module), SQLiteSessionStore (later),
    SovereignStackSessionStore (later if/when integration is desired). Upstream code
    depends on this Protocol, not any concrete class.
    """

    def save(self, session: Session) -> None: ...

    def load(self, session_id: str) -> Session: ...

    def exists(self, session_id: str) -> bool: ...

    def list_sessions(self) -> tuple[str, ...]: ...

    def iter_sessions(self) -> Iterator[Session]: ...

    def parity_history(
        self, threshold: Optional[float] = None
    ) -> tuple[tuple[str, Optional[SessionMetrics]], ...]: ...

    def alignment_pair_history(
        self,
    ) -> tuple[tuple[str, dict[frozenset[ModelId], int]], ...]: ...


class SessionNotFoundError(KeyError):
    pass


class SessionAlreadyExistsError(ValueError):
    """save refuses to overwrite — sessions are write-once. The chronicle remembers."""


class JsonFileSessionStore:
    """One JSON file per Session, named by session_id, in a single directory.

    Write-once: save raises if the file already exists. Rewriting a session is an
    explicit delete-then-save sequence, never a silent overwrite. The chronicle
    remembers; the store does not silently forget.
    """

    SUFFIX = ".session.json"

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, session_id: str) -> Path:
        if "/" in session_id or ".." in session_id or session_id.startswith("."):
            raise ValueError(
                f"session_id {session_id!r} contains path-traversal characters."
            )
        return self.root / f"{session_id}{self.SUFFIX}"

    def save(self, session: Session) -> None:
        path = self._path_for(session.session_id)
        if path.exists():
            raise SessionAlreadyExistsError(
                f"Session {session.session_id!r} already exists at {path}. "
                "Sessions are write-once — delete explicitly to rewrite."
            )
        path.write_text(session.model_dump_json(indent=2))

    def load(self, session_id: str) -> Session:
        path = self._path_for(session_id)
        if not path.exists():
            raise SessionNotFoundError(session_id)
        return Session.model_validate_json(path.read_text())

    def exists(self, session_id: str) -> bool:
        return self._path_for(session_id).exists()

    def delete(self, session_id: str) -> None:
        path = self._path_for(session_id)
        if not path.exists():
            raise SessionNotFoundError(session_id)
        path.unlink()

    def list_sessions(self) -> tuple[str, ...]:
        ids = sorted(
            p.name.removesuffix(self.SUFFIX)
            for p in self.root.glob(f"*{self.SUFFIX}")
        )
        return tuple(ids)

    def iter_sessions(self) -> Iterator[Session]:
        for session_id in self.list_sessions():
            yield self.load(session_id)

    def parity_history(
        self, threshold: Optional[float] = None
    ) -> tuple[tuple[str, Optional[SessionMetrics]], ...]:
        out: list[tuple[str, Optional[SessionMetrics]]] = []
        for session in self.iter_sessions():
            if threshold is None:
                metrics = compute_parity_shares(session)
            else:
                metrics = compute_parity_shares(session, threshold=threshold)
            out.append((session.session_id, metrics))
        return tuple(out)

    def alignment_pair_history(
        self,
    ) -> tuple[tuple[str, dict[frozenset[ModelId], int]], ...]:
        out: list[tuple[str, dict[frozenset[ModelId], int]]] = []
        for session in self.iter_sessions():
            out.append((session.session_id, compute_consensus_pair_distribution(session)))
        return tuple(out)


def aggregate_alignment_pair_history(
    history: tuple[tuple[str, dict[frozenset[ModelId], int]], ...],
) -> dict[frozenset[ModelId], int]:
    """Roll up a per-session alignment pair history into a cross-session distribution.

    Single-session skew is noisy. The cross-session aggregate is where alignment drift
    shows up. Suggested flag: skew > 2.0 on the aggregate over a meaningful window.
    """
    counter: Counter[frozenset[ModelId]] = Counter()
    for _, distribution in history:
        for voter_group, count in distribution.items():
            counter[voter_group] += count
    return dict(counter)
