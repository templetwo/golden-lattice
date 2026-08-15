"""Normalize a persisted Session into the dashboard's single render shape.

The redesigned dashboard (static/index.html) reads exactly one shape for both
replay and live. Replay is sourced here: the `/sessions` endpoint returns an
array of these objects, built from `sessions/*.session.json`. Live mode builds
the identical shape in the browser from the `/ws` event stream (the same
`applyEvent` mapping). One render layer, two sources.

The shape (mirrors the State Management block of the design handoff):

    {
      id, title, prompt,
      models: [opusId, sonnetId, haikuId],
      phase0: null | {grounding, proposals:[{model,count,queries}],
                      unique, frozen, results:[{query,preview}],
                      failed:[{query,reason}]},
      phase1: {modelId: {focus, conf, claims:[{id,text}]}},
      reflections: {modelId: {strong, weak, just}},
      crossreadings: [{reader, target, agreements:[claimId],
                       disagreements:[{id,reason}]}],
      turns: [{id, speaker, channel, target, content}],
      synthesis: {output, trace:[{id, disp}]},
      metrics: null | {distinct_claim_share, edge_case_coverage_share,
                       structural_pattern_share, parity_threshold,
                       irreducibility_violations},
      elevations: [{claim_ids:[...], converge_turn_ids:[...]}]
    }

Cross-reading agreement claim-ids are read straight from `session.phase_2`
(the persisted detail). The live `phase_2_cross_reading` event only carries
counts, so live-mode weave arcs are necessarily sparse — that is a protocol
limit, not a normalization choice.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from golden_lattice.memory_graph.metrics import compute_parity_shares
from golden_lattice.memory_graph.phase_0 import (
    DateTimeGrounding,
    FailedSearch,
    SearchResult,
)
from golden_lattice.memory_graph.schema import Session
from golden_lattice.memory_graph.store import JsonFileSessionStore

_PREVIEW_CHARS = 200
_TITLE_CHARS = 44


def _title_from_prompt(prompt: str) -> str:
    """A short, deterministic session label for the picker pills.

    First sentence (or first line), trimmed to a word boundary near
    _TITLE_CHARS. The sample bundle used hand-curated titles; absent an LLM
    this is the faithful deterministic stand-in.
    """
    text = " ".join((prompt or "").split())
    if not text:
        return "untitled"
    # Prefer the first sentence if it ends early.
    for sep in (". ", "? ", "! "):
        idx = text.find(sep)
        if 0 < idx <= _TITLE_CHARS:
            return text[: idx + 1].strip()
    if len(text) <= _TITLE_CHARS:
        return text
    clipped = text[:_TITLE_CHARS]
    space = clipped.rfind(" ")
    if space > _TITLE_CHARS // 2:
        clipped = clipped[:space]
    return clipped.rstrip(" ,;:") + "…"


def _normalize_phase0(session: Session) -> Optional[dict[str, Any]]:
    p0 = session.phase_0
    if p0 is None:
        return None

    grounding = ""
    results: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for entry in p0.feed:
        if isinstance(entry, DateTimeGrounding):
            grounding = entry.formatted_text
        elif isinstance(entry, SearchResult):
            results.append(
                {"query": entry.query, "preview": entry.result_text[:_PREVIEW_CHARS]}
            )
        elif isinstance(entry, FailedSearch):
            failed.append({"query": entry.query, "reason": entry.reason})

    proposals = [
        {
            "model": prop.model_id,
            "count": len(prop.queries),
            "queries": list(prop.queries),
        }
        for prop in p0.proposals
    ]
    unique: set[str] = set()
    for prop in p0.proposals:
        unique.update(prop.queries)

    return {
        "grounding": grounding,
        "proposals": proposals,
        "unique": len(unique),
        "frozen": len(p0.feed),
        "results": results,
        "failed": failed,
    }


def normalize_session(session: Session) -> dict[str, Any]:
    """Build the single dashboard render shape from a persisted Session."""
    phase1: dict[str, Any] = {}
    reflections: dict[str, Any] = {}
    for model_id, resp in session.phase_1.items():
        phase1[model_id] = {
            "focus": resp.focus_tag,
            "conf": resp.confidence,
            "claims": [{"id": c.claim_id, "text": c.text} for c in resp.claims],
        }
        if resp.self_reflection_artifacts:
            art = resp.self_reflection_artifacts[0]
            reflections[model_id] = {
                "strong": art.strongest_claim_id,
                "weak": art.weakest_claim_id,
                "just": art.tag_justification,
            }
        else:
            reflections[model_id] = {"strong": None, "weak": None, "just": ""}

    crossreadings = [
        {
            "reader": cr.reader_model,
            "target": cr.target_model,
            "agreements": [a.claim_id for a in cr.agreements],
            "disagreements": [
                {"id": d.target_claim_id, "reason": d.reason} for d in cr.disagreements
            ],
        }
        for cr in session.phase_2
    ]

    turns = [
        {
            "id": t.turn_id,
            "speaker": t.speaker_model,
            "channel": t.channel,
            "target": t.target_model,
            "content": t.content,
        }
        for t in session.phase_3
    ]

    synthesis: dict[str, Any] = {"output": "", "trace": []}
    elevations: list[dict[str, Any]] = []
    artifact = session.phase_4
    if artifact is not None:
        synthesis = {
            "output": artifact.output,
            "trace": [
                {"id": e.claim_id, "disp": e.disposition} for e in artifact.claim_trace
            ],
        }
        elevations = [
            {
                "claim_ids": list(e.claim_ids),
                "converge_turn_ids": list(e.converge_turn_ids),
            }
            for e in artifact.elevations
        ]

    # Use persisted metrics when present; otherwise recompute (older sessions
    # predate the parity wiring), mirroring replay's idempotent fallback.
    metrics_model = (
        session.metrics if session.metrics is not None else compute_parity_shares(session)
    )
    metrics = metrics_model.model_dump(mode="json") if metrics_model is not None else None

    return {
        "id": session.session_id,
        "title": _title_from_prompt(session.prompt),
        "prompt": session.prompt,
        "models": list(session.models_invited),
        "phase0": _normalize_phase0(session),
        "phase1": phase1,
        "reflections": reflections,
        "crossreadings": crossreadings,
        "turns": turns,
        "synthesis": synthesis,
        "metrics": metrics,
        "elevations": elevations,
    }


def _is_complete(session: Session) -> bool:
    """Complete through Phase 4 with at least one Phase 1 response — the
    selection criterion for the replay bundle (handoff §Replay data source)."""
    return bool(session.phase_1) and session.phase_4 is not None


def normalized_sessions(
    sessions_dir: Path, *, limit: Optional[int] = None
) -> list[dict[str, Any]]:
    """Load every complete session under `sessions_dir`, normalized, newest first.

    Newest-first by session id (the ids are timestamp-prefixed, so this is a
    chronological sort). `limit` caps how many are returned.
    """
    store = JsonFileSessionStore(sessions_dir)
    out: list[dict[str, Any]] = []
    for session_id in sorted(store.list_sessions(), reverse=True):
        try:
            session = store.load(session_id)
        except Exception:
            continue
        if not _is_complete(session):
            continue
        try:
            out.append(normalize_session(session))
        except Exception:
            # A single malformed session must not sink the whole bundle.
            continue
        if limit is not None and len(out) >= limit:
            break
    return out
