"""The Record: sums across every loaded run, keyed by seat family."""

from golden_lattice.dashboard.normalize import normalize_session
from golden_lattice.dashboard.record import HIGHLIGHT_RATIO, build_record

RULE = "same family flagged in 3 or more of the last 5 runs"


def _runs(make_full_session, n, **kw):
    return [
        normalize_session(
            make_full_session(
                session_id=f"session_202605{10 + i:02d}_000000_r{i:07d}",
                created_offset_days=i,
                **kw,
            )
        )
        for i in range(n)
    ]


def test_runs_newest_first_with_claim_counts(make_full_session):
    r = build_record(_runs(make_full_session, 2))["runs"]
    assert [x["id"][:16] for x in r] == ["session_20260511", "session_20260510"]
    assert (r[0]["claims"], r[0]["disputed"], r[0]["both"], r[0]["elevated"]) == (6, 3, 1, 1)
    assert r[0]["parity"] == "flag" and r[0]["flag_reading"] == "not_recognized"
    assert r[0]["recomputed"] is False


def test_series_by_family_oldest_first_with_id_change_marker(make_full_session):
    legacy = normalize_session(make_full_session(
        legacy=True, session_id="session_20260501_000000_legacy00", created_offset_days=0))
    current = normalize_session(make_full_session(
        session_id="session_20260502_000000_curr0000", created_offset_days=1))
    series = build_record([current, legacy])["series"]
    assert [p["run_id"] for p in series["opus"]] == [
        "session_20260501_000000_legacy00", "session_20260502_000000_curr0000"]
    assert series["opus"][0]["id_changed"] is False
    assert series["opus"][1]["id_changed"] is True
    assert series["opus"][1]["share"] == 0.34
    assert "fable" not in series


def test_pairs_sum_by_family_and_highlight(make_full_session):
    pairs = {(p["reader"], p["target"]): p for p in build_record(_runs(make_full_session, 3))["pairs"]}
    assert pairs[("opus", "sonnet")] == {
        "reader": "opus", "target": "sonnet", "disputed": 3, "endorsed": 0, "highlight": False}
    assert pairs[("sonnet", "haiku")]["endorsed"] == 6
    assert pairs[("sonnet", "haiku")]["highlight"] is True
    assert HIGHLIGHT_RATIO == 8


def test_flags_list_and_persistent_rule(make_full_session):
    rec = build_record(_runs(make_full_session, 3))
    assert [f["family"] for f in rec["flags"]] == ["sonnet", "sonnet", "sonnet"]
    assert rec["flags"][0]["gloss"].startswith("Genuine recognition asymmetry")
    assert rec["persistent"] == {"present": True, "family": "sonnet", "rule": RULE}
    assert build_record(_runs(make_full_session, 2))["persistent"]["present"] is False


def test_recomputed_flags_count_and_are_marked(make_full_session):
    rec = build_record(_runs(make_full_session, 1, persist_metrics=False))
    assert rec["runs"][0]["recomputed"] is True
    assert rec["flags"] and all(f["recomputed"] for f in rec["flags"])


def test_record_dyad_excluded_from_series_and_flags(make_full_session):
    rec = build_record([normalize_session(make_full_session(dyad=True))])
    assert rec["runs"][0]["parity"] == "undefined"
    assert rec["flags"] == []
    assert all(p["share"] is None for fam in rec["series"].values() for p in fam)


def test_empty_record():
    assert build_record([]) == {
        "runs": [], "series": {}, "threshold": 0.15, "pairs": [], "flags": [],
        "persistent": {"present": False, "family": None, "rule": RULE},
    }
