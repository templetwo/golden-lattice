"""Offline audit of completed sessions (hardening, no model calls, no spend).

Works on the session JSON as plain data, so it can run over any saved session,
including sessions from before this module existed. It reports; it does not
judge. Parity is reported under both readings (ARCHITECTURE §3 "all three
metrics" and the code's "any one share") so Anthony can rule between them.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from golden_lattice.evaluation.insights import cluster_count

CHANNEL_CAP = 3
CHANNELS = ("critique", "augment", "converge")
PARITY_METRICS = ("distinct_claim_share", "edge_case_coverage_share", "structural_pattern_share")
PARITY_THRESHOLD_DEFAULT = 0.15
BANDS = (0.5, 0.6, 0.7)


def same_claim_agree_and_disagree(phase_2: list[dict]) -> list[dict]:
    """A reader agreeing and disagreeing with the same target claim."""
    hits = []
    for reading in phase_2:
        agreed = {a["claim_id"] for a in reading.get("agreements", [])}
        for dis in reading.get("disagreements", []):
            if dis["target_claim_id"] in agreed:
                hits.append({
                    "reader": reading["reader_model"],
                    "target": reading["target_model"],
                    "claim_id": dis["target_claim_id"],
                })
    return hits


def channel_pressure(phase_3: list[dict]) -> dict[str, Any]:
    """Cap pressure as the parser counts it.

    Critique is capped per (speaker, target model): 3 per peer pair.
    Augment and converge are capped per speaker, in aggregate: 3 total.
    The parser (phase_3_dialogue._validate_caps) uses the same rules.
    """
    aggregate: dict[str, Counter] = defaultdict(Counter)
    critique: Counter = Counter()
    for turn in phase_3:
        if turn["channel"] == "critique":
            critique[(turn["speaker_model"], turn.get("target_model"))] += 1
        else:
            aggregate[turn["speaker_model"]][turn["channel"]] += 1
    counts = {
        speaker: {ch: chans.get(ch, 0) for ch in ("augment", "converge")}
        for speaker, chans in aggregate.items()
    }
    critique_per_target = {f"{s}->{t}": n for (s, t), n in critique.items()}
    at_cap = [(s, ch) for s, c in counts.items() for ch, n in c.items() if n == CHANNEL_CAP]
    over_cap = [(s, ch) for s, c in counts.items() for ch, n in c.items() if n > CHANNEL_CAP]
    at_cap += [(s, f"critique->{t}") for (s, t), n in critique.items() if n == CHANNEL_CAP]
    over_cap += [(s, f"critique->{t}") for (s, t), n in critique.items() if n > CHANNEL_CAP]
    return {"counts": counts, "critique_per_target": critique_per_target,
            "at_cap": at_cap, "over_cap": over_cap}


def null_targets_by_channel(phase_3: list[dict]) -> dict[str, dict[str, int]]:
    """How often each channel carries no target model, and how often it is critique without a target."""
    out: dict[str, dict[str, int]] = {ch: {"turns": 0, "null_target": 0} for ch in CHANNELS}
    for turn in phase_3:
        bucket = out.setdefault(turn["channel"], {"turns": 0, "null_target": 0})
        bucket["turns"] += 1
        if turn.get("target_model") is None:
            bucket["null_target"] += 1
    return out


def claim_concentration(phase_2: list[dict], phase_3: list[dict]) -> dict[str, Any]:
    """How many times each claim id is cited across cross-readings and dialogue."""
    cites: Counter = Counter()
    for reading in phase_2:
        cites.update(a["claim_id"] for a in reading.get("agreements", []))
        cites.update(d["target_claim_id"] for d in reading.get("disagreements", []))
    for turn in phase_3:
        cites.update(turn.get("target_claim_ids") or [])
    if not cites:
        return {"max_cites": 0, "top_claim_id": None, "distinct_cited": 0}
    top, n = cites.most_common(1)[0]
    return {"max_cites": n, "top_claim_id": top, "distinct_cited": len(cites)}


def distinct_insights(phase_1: dict[str, Any]) -> dict[str, Any]:
    """Raw claim count against near-duplicate clusters at each Jaccard band.

    Clusters across all models together, so a claim phrased the same way by two
    models is one insight. A band that merges nothing shows the criterion cannot fail.
    """
    texts = [c["text"] for resp in phase_1.values() for c in resp.get("claims", [])]
    raw = len(texts)
    bands = {f"{t:.1f}": cluster_count(texts, threshold=t) for t in BANDS} if texts else {}
    return {"raw": raw, "clusters": bands, "merged_at_0_5": raw - bands.get("0.5", raw)}


def parity_readings(metrics: dict[str, Any]) -> dict[str, Any]:
    """Both parity readings, recorded so the choice between them stays Anthony's."""
    threshold = metrics.get("parity_threshold", PARITY_THRESHOLD_DEFAULT)
    below: dict[str, list[str]] = {}
    models: set[str] = set()
    for name in PARITY_METRICS:
        shares = metrics.get(name) or {}
        models.update(shares)
        below[name] = sorted(m for m, v in shares.items() if v is not None and v < threshold)
    all_three = sorted(
        m for m in models
        if all(m in below[name] for name in PARITY_METRICS)
    )
    return {
        "threshold": threshold,
        "below_by_metric": below,
        "flag_any_share": any(below[name] for name in PARITY_METRICS),
        "flag_all_three": bool(all_three),
        "models_below_all_three": all_three,
    }


def audit_session(session: dict[str, Any]) -> dict[str, Any]:
    phase_1 = session.get("phase_1") or {}
    phase_2 = session.get("phase_2") or []
    phase_3 = session.get("phase_3") or []
    same = same_claim_agree_and_disagree(phase_2)
    return {
        "session_id": session.get("session_id"),
        "same_claim_agree_and_disagree": same,
        "channel_pressure": channel_pressure(phase_3),
        "null_targets_by_channel": null_targets_by_channel(phase_3),
        "claim_concentration": claim_concentration(phase_2, phase_3),
        "distinct_insights": distinct_insights(phase_1),
        "parity": parity_readings(session.get("metrics") or {}),
    }


def audit_run(run_dir: Path) -> dict[str, Any]:
    """Audit every session in a run directory. Writes <session>.audit.json per session."""
    per_session = []
    for path in sorted(run_dir.glob("*.session.json")):
        session = json.loads(path.read_text(encoding="utf-8"))
        result = audit_session(session)
        (run_dir / f"{path.name[:-len('.session.json')]}.audit.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        per_session.append(result)
    summary = {
        "sessions": len(per_session),
        "same_claim_agree_and_disagree": sum(len(s["same_claim_agree_and_disagree"]) for s in per_session),
        "sessions_at_cap": sum(1 for s in per_session if s["channel_pressure"]["at_cap"]),
        "sessions_over_cap": sum(1 for s in per_session if s["channel_pressure"]["over_cap"]),
        "max_claim_cites": max((s["claim_concentration"]["max_cites"] for s in per_session), default=0),
        "merged_at_0_5": sum(s["distinct_insights"]["merged_at_0_5"] for s in per_session),
        "sessions_flagged_any_share": sum(1 for s in per_session if s["parity"]["flag_any_share"]),
        "sessions_flagged_all_three": sum(1 for s in per_session if s["parity"]["flag_all_three"]),
    }
    (run_dir / "run_audit.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    import sys

    print(json.dumps(audit_run(Path(sys.argv[1])), indent=2))
