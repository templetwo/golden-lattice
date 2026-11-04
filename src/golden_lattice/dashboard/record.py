"""The Record: cross-session sums over normalized sessions.

Reads only what normalize.py produced (which in turn reads the persisted
Session and the canonical metrics functions), so the renderer stays a
renderer. A seat is its family here (spec §5.2): Opus 4.7 and Opus 5 are one
line on the share chart, with `id_changed` marking the run where the
underlying model moved.
"""

from __future__ import annotations

from typing import Any, Optional

from golden_lattice.memory_graph.base import PARITY_THRESHOLD, SEAT_FAMILY_ORDER, seat_family

HIGHLIGHT_RATIO = 8
PERSISTENT_WINDOW = 5
PERSISTENT_MIN = 3
PERSISTENT_RULE = (
    f"same family flagged in {PERSISTENT_MIN} or more of the last {PERSISTENT_WINDOW} runs"
)


def _parity(session: dict[str, Any]) -> tuple[str, Optional[str]]:
    if session.get("metrics") is None:
        return "undefined", None
    readings = session.get("interpretations") or []
    if readings:
        return "flag", readings[0]["reading"]
    return "holds", None


def _runs(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for s in sorted(sessions, key=lambda x: x["id"], reverse=True):
        parity, reading = _parity(s)
        st = s["stats"]
        out.append({
            "id": s["id"],
            "created_at": s.get("created_at"),
            "title": s.get("title"),
            "prompt": s.get("prompt"),
            "claims": st["claims"],
            "disputed": st["disputed"],
            "both": st["both"],
            "elevated": st["elevated"],
            "parity": parity,
            "flag_reading": reading,
            "recomputed": bool(s.get("metrics_recomputed")),
            "seats": [seat["family"] for seat in s.get("seats", [])],
        })
    return out


def _series(sessions: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    chronological = sorted(sessions, key=lambda x: x["id"])
    families = [f for f in SEAT_FAMILY_ORDER
                if any(seat["family"] == f for s in sessions for seat in s.get("seats", []))]
    series: dict[str, list[dict[str, Any]]] = {}
    for family in families:
        points = []
        previous_id: Optional[str] = None
        for s in chronological:
            seat = next((x for x in s.get("seats", []) if x["family"] == family), None)
            model_id = seat["id"] if seat else None
            metrics = s.get("metrics")
            share = None
            if seat and metrics is not None:
                share = metrics["distinct_claim_share"].get(model_id)
            changed = bool(model_id and previous_id and model_id != previous_id)
            if model_id:
                previous_id = model_id
            points.append({"run_id": s["id"], "share": share, "model_id": model_id, "id_changed": changed})
        series[family] = points
    return series


def _pairs(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sums: dict[tuple[str, str], list[int]] = {}
    for s in sessions:
        for p in s.get("pairs", []):
            key = (seat_family(p["reader"]), seat_family(p["target"]))
            acc = sums.setdefault(key, [0, 0])
            acc[0] += p["disputed"]
            acc[1] += p["endorsed"]
    order = {f: i for i, f in enumerate(SEAT_FAMILY_ORDER)}
    rows = [
        {
            "reader": r, "target": t, "disputed": d, "endorsed": e,
            "highlight": (d + e) > 0 and e >= HIGHLIGHT_RATIO * d,
        }
        for (r, t), (d, e) in sums.items()
    ]
    rows.sort(key=lambda x: (-x["disputed"], order[x["reader"]], order[x["target"]]))
    return rows


def _flags(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for s in sorted(sessions, key=lambda x: x["id"], reverse=True):
        for i in s.get("interpretations") or []:
            out.append({
                "run_id": s["id"],
                "created_at": s.get("created_at"),
                "family": i["family"],
                "dimension": i["dimension"],
                "reading": i["reading"],
                "gloss": i["gloss"],
                "share": i["share"],
                "recomputed": bool(s.get("metrics_recomputed")),
            })
    return out


def _persistent(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    window = sorted(sessions, key=lambda x: x["id"], reverse=True)[:PERSISTENT_WINDOW]
    for family in SEAT_FAMILY_ORDER:
        flagged = sum(
            1 for s in window
            if any(i["family"] == family for i in s.get("interpretations") or [])
        )
        if flagged >= PERSISTENT_MIN:
            return {"present": True, "family": family, "rule": PERSISTENT_RULE}
    return {"present": False, "family": None, "rule": PERSISTENT_RULE}


def build_record(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    thresholds = [s["metrics"]["parity_threshold"] for s in sessions if s.get("metrics")]
    return {
        "runs": _runs(sessions),
        "series": _series(sessions),
        "threshold": max(thresholds) if thresholds else PARITY_THRESHOLD,
        "pairs": _pairs(sessions),
        "flags": _flags(sessions),
        "persistent": _persistent(sessions),
    }
