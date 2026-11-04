"""The single render shape the dashboard reads, built from a persisted Session."""

from golden_lattice.dashboard.normalize import load_normalized, normalize_session
from golden_lattice.memory_graph.store import JsonFileSessionStore


def _by_text(n):
    return {c["text"]: c for c in n["claims"]}


def test_claims_carry_readers_and_fate(make_full_session):
    n = normalize_session(make_full_session())
    c = _by_text(n)
    assert c["sonnet one"]["disputed_by"] == ["claude-opus-5", "claude-haiku-4-5-20251001"]
    assert [d["reason"] for d in c["sonnet one"]["disagreements"]] == [
        "opus disagrees with sonnet one",
        "haiku disagrees with sonnet one",
    ]
    assert c["sonnet one"]["critiques"] == [
        {"speaker": "claude-opus-5", "turn_id": "t1", "content": "opus critiques sonnet one in dialogue"}
    ]
    assert c["sonnet one"]["disposition"] == "modified"
    assert c["sonnet one"]["disputed_hedge"].startswith("[DISPUTED")
    assert c["sonnet one"]["surfaced_note"] == "opus and haiku both push back on sonnet one"
    assert c["opus two"]["disputed_by"] == ["claude-haiku-4-5-20251001"]  # critique only
    assert c["opus two"]["agreed_by"] == ["claude-sonnet-5", "claude-haiku-4-5-20251001"]
    assert c["opus two"]["elevated"] is True
    assert c["sonnet two"]["disposition"] == "omitted"
    assert c["sonnet two"]["omission_reason"] == "redundant with opus two"


def test_stats_count_claims_not_events(make_full_session):
    s = normalize_session(make_full_session())["stats"]
    assert s == {
        "claims": 6, "disputed": 3, "both": 1, "elevated": 1, "omitted": 1,
        "modified": 1, "critiques": 2, "augments": 1, "converges": 2, "missing": 1,
    }


def test_synthesis_lines_link_back(make_full_session):
    n = normalize_session(make_full_session())
    c = _by_text(n)
    assert c["opus one"]["synthesis_line"] == 0
    assert c["sonnet one"]["synthesis_line"] == 2
    assert c["sonnet two"]["synthesis_line"] is None
    assert n["synthesis_lines"][2]["tag"] == "S"
    assert n["synthesis_lines"][2]["hedge"].startswith("[DISPUTED")


def test_synthesis_lines_unmatched_and_duplicate(make_full_session):
    session = make_full_session()
    out = session.phase_4.output + "\n[O] a line the engine added\n[O] opus one"
    session = session.model_copy(
        update={"phase_4": session.phase_4.model_copy(update={"output": out})}
    )
    n = normalize_session(session)
    assert n["synthesis_lines"][5]["claim_id"] is None
    assert n["synthesis_lines"][6]["claim_id"] is None  # a claim matches at most once
    assert _by_text(n)["opus one"]["synthesis_line"] == 0


def test_pairs_one_per_cross_reading(make_full_session):
    pairs = {(p["reader"], p["target"]): p for p in normalize_session(make_full_session())["pairs"]}
    assert len(pairs) == 6
    assert pairs[("claude-haiku-4-5-20251001", "claude-opus-5")] == {
        "reader": "claude-haiku-4-5-20251001", "target": "claude-opus-5",
        "disputed": 0, "endorsed": 2, "missing": 0,
    }
    assert pairs[("claude-sonnet-5", "claude-opus-5")]["missing"] == 1


def test_interpretations_carry_reading_and_gloss(make_full_session):
    n = normalize_session(make_full_session())
    assert n["metrics_recomputed"] is False
    assert len(n["interpretations"]) == 1
    i = n["interpretations"][0]
    assert (i["family"], i["dimension"], i["reading"], i["share"]) == (
        "sonnet", "edge_case_coverage_share", "not_recognized", 0.1,
    )
    assert i["gloss"].startswith("Genuine recognition asymmetry")


def test_metrics_recomputed_when_absent(make_full_session):
    n = normalize_session(make_full_session(persist_metrics=False))
    assert n["metrics_recomputed"] is True and n["metrics"] is not None


def test_normalize_dyad_metrics_none_not_recomputed(make_full_session):
    n = normalize_session(make_full_session(dyad=True))
    assert n["metrics"] is None and n["metrics_recomputed"] is False
    assert n["interpretations"] == [] and n["stats"]["claims"] == 4


def test_seats_in_invited_order_with_family_and_legacy(make_full_session):
    seats = normalize_session(make_full_session(legacy=True))["seats"]
    assert [s["family"] for s in seats] == ["opus", "sonnet", "haiku"]
    assert seats[0]["id"] == "claude-opus-4-7"
    assert seats[0]["legacy"] is True and seats[0]["label"] == "OPUS"
    assert seats[0]["claim_count"] == 2


def test_normalize_phase0_none_passes_through(make_full_session):
    assert normalize_session(make_full_session())["phase0"] is None


def test_normalize_zero_disputes_stats(make_full_session):
    session = make_full_session()
    session = session.model_copy(update={
        "phase_2": tuple(cr.model_copy(update={"disagreements": ()}) for cr in session.phase_2),
        "phase_3": tuple(t for t in session.phase_3 if t.channel != "critique"),
    })
    assert normalize_session(session)["stats"]["disputed"] == 0


def test_load_normalized_reports_unloadable(tmp_path, make_full_session):
    JsonFileSessionStore(tmp_path).save(make_full_session())
    (tmp_path / "session_bad.session.json").write_text("{}")
    bundle = load_normalized(tmp_path)
    assert [s["id"] for s in bundle.sessions] == ["session_20260504_120000_abcd1234"]
    assert bundle.unloadable[0]["id"] == "session_bad"
    assert bundle.unloadable[0]["error"]
