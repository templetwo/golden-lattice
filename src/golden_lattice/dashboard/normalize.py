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

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from golden_lattice.memory_graph.base import LEGACY_MODEL_IDS, seat_family, seat_label
from golden_lattice.memory_graph.metrics import (
    READING_GLOSS,
    compute_parity_shares,
    interpret_parity_flags,
)
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


_TAG_RE = re.compile(r"^\[([A-Z])\]\s*")


def _split_hedge(text: str) -> tuple[str, Optional[str]]:
    """Split a trailing '[DISPUTED ...]' hedge off a synthesis line or text."""
    idx = text.find("[DISPUTED")
    if idx < 0:
        return text.strip(), None
    return text[:idx].strip(), text[idx:].strip()


def _synthesis_lines(output: str) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for raw in (output or "").split("\n"):
        if not raw.strip():
            continue
        m = _TAG_RE.match(raw)
        tag = m.group(1) if m else ""
        body = raw[m.end():] if m else raw
        text, hedge = _split_hedge(body)
        lines.append({"index": len(lines), "tag": tag, "text": text, "hedge": hedge, "claim_id": None})
    return lines


def _ordered(models: list[str], invited: list[str]) -> list[str]:
    seen = set(models)
    return [m for m in invited if m in seen]


def _claims(session: Session, lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    invited = [m.value for m in session.models_invited]
    agreed: dict[str, list[str]] = {}
    disagreements: dict[str, list[dict[str, str]]] = {}
    for cr in session.phase_2:
        for a in cr.agreements:
            agreed.setdefault(a.claim_id, []).append(cr.reader_model.value)
        for d in cr.disagreements:
            disagreements.setdefault(d.target_claim_id, []).append(
                {"reader": cr.reader_model.value, "reason": d.reason}
            )
    critiques: dict[str, list[dict[str, str]]] = {}
    for t in session.phase_3:
        if t.channel != "critique":
            continue
        for cid in t.target_claim_ids:
            critiques.setdefault(cid, []).append(
                {"speaker": t.speaker_model.value, "turn_id": t.turn_id, "content": t.content}
            )
    artifact = session.phase_4
    trace = {e.claim_id: e for e in artifact.claim_trace} if artifact else {}
    elevated = {cid for e in artifact.elevations for cid in e.claim_ids} if artifact else set()
    notes: dict[str, list[str]] = {}
    if artifact:
        for sd in artifact.surfaced_disagreements:
            for cid in sd.claim_ids:
                notes.setdefault(cid, []).append(sd.note)

    out: list[dict[str, Any]] = []
    index = 0
    for model in session.models_invited:
        resp = session.phase_1.get(model)
        if resp is None:
            continue
        for c in resp.claims:
            entry = trace.get(c.claim_id)
            dis = disagreements.get(c.claim_id, [])
            crit = critiques.get(c.claim_id, [])
            out.append({
                "id": c.claim_id,
                "author": model.value,
                "author_family": seat_family(model),
                "text": c.text,
                "index": index,
                "agreed_by": _ordered(agreed.get(c.claim_id, []), invited),
                "disagreements": dis,
                "critiques": crit,
                "disputed_by": _ordered([d["reader"] for d in dis] + [k["speaker"] for k in crit], invited),
                "disposition": entry.disposition if entry else None,
                "modified_text": entry.modified_text if entry else None,
                "omission_reason": entry.omission_reason if entry else None,
                "synthesis_line": None,
                "disputed_hedge": None,
                "surfaced_note": " · ".join(notes[c.claim_id]) if c.claim_id in notes else None,
                "elevated": c.claim_id in elevated,
            })
            index += 1

    # Link synthesis lines to claims: exact claim text first, then the
    # modified text with its hedge stripped. One claim per line, one line
    # per claim; unmatched lines keep claim_id None.
    by_text: dict[str, list[dict[str, Any]]] = {}
    by_modified: dict[str, list[dict[str, Any]]] = {}
    for c in out:
        by_text.setdefault(c["text"], []).append(c)
        if c["modified_text"]:
            by_modified.setdefault(_split_hedge(c["modified_text"])[0], []).append(c)
    tag_family = {"F": "fable", "O": "opus", "S": "sonnet", "H": "haiku"}

    def _pick(candidates: list[dict[str, Any]], tag: str) -> Optional[dict[str, Any]]:
        free = [c for c in candidates if c["synthesis_line"] is None and c["disposition"] != "omitted"]
        same = [c for c in free if c["author_family"] == tag_family.get(tag)]
        return (same or free or [None])[0]

    for line in lines:
        claim = _pick(by_text.get(line["text"], []), line["tag"]) or _pick(
            by_modified.get(line["text"], []), line["tag"]
        )
        if claim is None:
            continue
        claim["synthesis_line"] = line["index"]
        line["claim_id"] = claim["id"]
    for c in out:
        hedge = None
        if c["synthesis_line"] is not None:
            hedge = lines[c["synthesis_line"]]["hedge"]
        if hedge is None and c["modified_text"]:
            hedge = _split_hedge(c["modified_text"])[1]
        c["disputed_hedge"] = hedge
    return out


def _phase_1_ids(session: Session) -> set[str]:
    return {c.claim_id for r in session.phase_1.values() for c in r.claims}


def _unattached_critiques(session: Session) -> list[dict[str, Any]]:
    """Critiques whose targets are all outside Phase 1 (e.g. a Phase 2
    missing claim). They have no row in 02, so 03 shows them."""
    ids = _phase_1_ids(session)
    return [
        {"speaker": t.speaker_model.value, "turn_id": t.turn_id, "content": t.content,
         "target": t.target_model.value if t.target_model else None,
         "target_claim_ids": list(t.target_claim_ids)}
        for t in session.phase_3
        if t.channel == "critique" and not (set(t.target_claim_ids) & ids)
    ]


def _stats(claims: list[dict[str, Any]], session: Session) -> dict[str, int]:
    channels = [t.channel for t in session.phase_3]
    phase_1_ids = _phase_1_ids(session)
    return {
        "claims": len(claims),
        "disputed": sum(1 for c in claims if c["disputed_by"]),
        "both": sum(1 for c in claims if len(c["disputed_by"]) >= 2),
        "elevated": sum(1 for c in claims if c["elevated"]),
        "omitted": sum(1 for c in claims if c["disposition"] == "omitted"),
        "modified": sum(1 for c in claims if c["disposition"] == "modified"),
        "critiques": channels.count("critique"),
        "critiques_attached": sum(
            1 for t in session.phase_3
            if t.channel == "critique" and set(t.target_claim_ids) & phase_1_ids
        ),
        "augments": channels.count("augment"),
        "converges": channels.count("converge"),
        "missing": sum(len(cr.missing) for cr in session.phase_2),
    }


def _pairs(session: Session) -> list[dict[str, Any]]:
    return [
        {
            "reader": cr.reader_model.value,
            "target": cr.target_model.value,
            "disputed": len(cr.disagreements),
            "endorsed": len(cr.agreements),
            "missing": len(cr.missing),
        }
        for cr in session.phase_2
    ]


def _seats(session: Session) -> list[dict[str, Any]]:
    seats = []
    for model in session.models_invited:
        resp = session.phase_1.get(model)
        seats.append({
            "id": model.value,
            "family": seat_family(model),
            "label": seat_label(model),
            "legacy": model in LEGACY_MODEL_IDS,
            "focus": resp.focus_tag.value if resp else "",
            "conf": resp.confidence if resp else 0.0,
            "claim_count": len(resp.claims) if resp else 0,
        })
    return seats


def _interpretations(session: Session) -> list[dict[str, Any]]:
    out = []
    for f in interpret_parity_flags(session):
        out.append({
            "model": f.source_model.value,
            "family": seat_family(f.source_model),
            "dimension": f.dimension_label,
            "reading": f.reading,
            "gloss": READING_GLOSS[f.reading],
            "share": f.share,
            "histogram_n_zero": f.histogram_n_zero,
            "histogram_n_one": f.histogram_n_one,
            "histogram_n_two": f.histogram_n_two,
            "other_only_entries": f.other_only_entries,
            "total_claims": f.total_claims,
        })
    return out


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
    recomputed = compute_parity_shares(session) if session.metrics is None else None
    metrics_model = session.metrics if session.metrics is not None else recomputed
    metrics = metrics_model.model_dump(mode="json") if metrics_model is not None else None
    interpreted = session if session.metrics is not None else (
        session.model_copy(update={"metrics": recomputed}) if recomputed is not None else session
    )

    lines = _synthesis_lines(artifact.output if artifact is not None else "")
    claims = _claims(session, lines)

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
        "seats": _seats(session),
        "claims": claims,
        "synthesis_lines": lines,
        "stats": _stats(claims, session),
        "pairs": _pairs(session),
        "unattached_critiques": _unattached_critiques(session),
        "interpretations": _interpretations(interpreted),
        "metrics_recomputed": session.metrics is None and recomputed is not None,
        "created_at": session.created_at.isoformat(),
    }


def _is_complete(session: Session) -> bool:
    """Complete through Phase 4 with at least one Phase 1 response — the
    selection criterion for the replay bundle (handoff §Replay data source)."""
    return bool(session.phase_1) and session.phase_4 is not None


def _why(exc: Exception) -> str:
    """One line that says why a file failed: the headline plus, for a
    pydantic error, the first field and its message (e.g. an unknown seat id)."""
    lines = [ln.strip() for ln in str(exc).splitlines() if ln.strip()]
    if not lines:
        return type(exc).__name__
    head = lines[0]
    if len(lines) >= 3 and "validation error" in head:
        detail = lines[2].split(" [type=")[0]
        return f"{head}: {lines[1]}: {detail}"
    return head


@dataclass
class NormalizedBundle:
    """Every loadable complete session, normalized, plus every file that failed."""

    sessions: list[dict[str, Any]] = field(default_factory=list)
    unloadable: list[dict[str, str]] = field(default_factory=list)


def load_normalized(sessions_dir: Path, *, limit: Optional[int] = None) -> NormalizedBundle:
    """Newest first. A file that fails to load or normalize is reported, never hidden."""
    bundle = NormalizedBundle()
    if not Path(sessions_dir).exists():
        return bundle
    store = JsonFileSessionStore(sessions_dir)
    for session_id in sorted(store.list_sessions(), reverse=True):
        try:
            session = store.load(session_id)
            if not _is_complete(session):
                bundle.unloadable.append(
                    {"id": session_id, "error": "incomplete: no Phase 4 synthesis in this file"}
                )
                continue
            bundle.sessions.append(normalize_session(session))
        except Exception as exc:  # report, do not sink the bundle
            bundle.unloadable.append({"id": session_id, "error": _why(exc)})
            continue
        if limit is not None and len(bundle.sessions) >= limit:
            break
    return bundle


def normalized_sessions(
    sessions_dir: Path, *, limit: Optional[int] = None
) -> list[dict[str, Any]]:
    """Load every complete session under `sessions_dir`, normalized, newest first."""
    return load_normalized(sessions_dir, limit=limit).sessions
