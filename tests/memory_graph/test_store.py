"""Tests for JsonFileSessionStore and the SessionStore Protocol surface."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from golden_lattice.memory_graph.base import ModelId, Phase, claim_id_for
from golden_lattice.memory_graph.metrics import compute_consensus_pair_skew
from golden_lattice.memory_graph.schema import (
    Claim,
    IndependentResponse,
    Session,
)
from golden_lattice.memory_graph.store import (
    JsonFileSessionStore,
    SessionAlreadyExistsError,
    SessionNotFoundError,
    SessionStore,
    aggregate_alignment_pair_history,
)
from golden_lattice.memory_graph.tagging import (
    ClaimTags,
    EdgeCaseTag,
    Phase2Tagging,
    StructuralPatternTag,
)


NOW = datetime(2026, 5, 2, 12, 0, 0, tzinfo=timezone.utc)


def _phase1_claim(model: ModelId, text: str) -> Claim:
    return Claim(
        claim_id=claim_id_for(model, Phase.INDEPENDENT, text),
        source_model=model,
        source_phase=Phase.INDEPENDENT,
        text=text,
    )


def _independent_response(model: ModelId, prompt_hash: str, claims: tuple[Claim, ...]) -> IndependentResponse:
    return IndependentResponse(
        model_id=model,
        prompt_hash=prompt_hash,
        response="response text",
        claims=claims,
        generation_started_at=NOW,
        generation_completed_at=NOW,
    )


def _triad_session(
    session_id: str = "triad",
    phase_2_taggings: tuple[Phase2Tagging, ...] = (),
) -> tuple[Session, dict[ModelId, Claim]]:
    claims = {
        ModelId.OPUS: _phase1_claim(ModelId.OPUS, f"opus alpha {session_id}"),
        ModelId.SONNET: _phase1_claim(ModelId.SONNET, f"sonnet beta {session_id}"),
        ModelId.HAIKU: _phase1_claim(ModelId.HAIKU, f"haiku gamma {session_id}"),
    }
    session = Session(
        session_id=session_id,
        prompt="p",
        prompt_hash="h",
        models_invited=(ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU),
        phase_1={m: _independent_response(m, "h", (c,)) for m, c in claims.items()},
        phase_2_taggings=phase_2_taggings,
    )
    return session, claims


# --- Protocol conformance ------------------------------------------------


def test_jsonfile_store_satisfies_protocol(tmp_path: Path):
    store = JsonFileSessionStore(tmp_path)
    assert isinstance(store, SessionStore)


# --- Save / load round-trip ----------------------------------------------


def test_save_and_load_roundtrip(tmp_path: Path):
    store = JsonFileSessionStore(tmp_path)
    session, _ = _triad_session("rt-1")
    store.save(session)
    assert store.exists("rt-1")
    loaded = store.load("rt-1")
    assert loaded.session_id == "rt-1"
    assert loaded.models_invited == session.models_invited
    assert set(loaded.phase_1.keys()) == set(session.phase_1.keys())


def test_save_writes_indented_json(tmp_path: Path):
    store = JsonFileSessionStore(tmp_path)
    session, _ = _triad_session("legible")
    store.save(session)
    raw = (tmp_path / f"legible{store.SUFFIX}").read_text()
    assert "\n" in raw  # indented, not single-line
    assert '"session_id": "legible"' in raw  # human-greppable


def test_load_missing_session_raises(tmp_path: Path):
    store = JsonFileSessionStore(tmp_path)
    with pytest.raises(SessionNotFoundError):
        store.load("ghost")


def test_save_refuses_overwrite(tmp_path: Path):
    store = JsonFileSessionStore(tmp_path)
    session, _ = _triad_session("write-once")
    store.save(session)
    with pytest.raises(SessionAlreadyExistsError):
        store.save(session)


def test_delete_then_save_works(tmp_path: Path):
    store = JsonFileSessionStore(tmp_path)
    session, _ = _triad_session("rewrite")
    store.save(session)
    store.delete("rewrite")
    assert not store.exists("rewrite")
    store.save(session)
    assert store.exists("rewrite")


def test_delete_missing_raises(tmp_path: Path):
    store = JsonFileSessionStore(tmp_path)
    with pytest.raises(SessionNotFoundError):
        store.delete("ghost")


# --- Path traversal refusal ----------------------------------------------


@pytest.mark.parametrize("bad_id", ["../escape", "a/b", ".hidden"])
def test_path_traversal_session_ids_refused(tmp_path: Path, bad_id: str):
    store = JsonFileSessionStore(tmp_path)
    session, _ = _triad_session("ok")
    # Build a session whose id would attempt traversal — bypass via direct path lookup
    with pytest.raises(ValueError, match="path-traversal"):
        store._path_for(bad_id)


# --- list / iter ---------------------------------------------------------


def test_list_sessions_returns_sorted_ids(tmp_path: Path):
    store = JsonFileSessionStore(tmp_path)
    for sid in ("zeta", "alpha", "mu"):
        session, _ = _triad_session(sid)
        store.save(session)
    assert store.list_sessions() == ("alpha", "mu", "zeta")


def test_iter_sessions_yields_in_listed_order(tmp_path: Path):
    store = JsonFileSessionStore(tmp_path)
    for sid in ("c", "a", "b"):
        session, _ = _triad_session(sid)
        store.save(session)
    ids = [s.session_id for s in store.iter_sessions()]
    assert ids == ["a", "b", "c"]


# --- Cross-session queries -----------------------------------------------


def _consensus_session(session_id: str) -> Session:
    """Triad session where Sonnet+Haiku peer-tag Opus's claim → consensus pair {Sonnet, Haiku}."""
    session, claims = _triad_session(session_id)
    opus_id = claims[ModelId.OPUS].claim_id
    sonnet = Phase2Tagging(
        tagger_model=ModelId.SONNET,
        peer_tags=(ClaimTags(claim_id=opus_id, edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,)),),
    )
    haiku = Phase2Tagging(
        tagger_model=ModelId.HAIKU,
        peer_tags=(ClaimTags(claim_id=opus_id, edge_case_tags=(EdgeCaseTag.BOUNDARY_CONDITION,)),),
    )
    s, _ = _triad_session(session_id, phase_2_taggings=(sonnet, haiku))
    return s


def test_parity_history_covers_all_sessions(tmp_path: Path):
    store = JsonFileSessionStore(tmp_path)
    store.save(_consensus_session("a"))
    store.save(_consensus_session("b"))
    history = store.parity_history()
    ids = [sid for sid, _ in history]
    assert ids == ["a", "b"]
    for _, metrics in history:
        assert metrics is not None  # triads return SessionMetrics


def test_parity_history_threshold_passes_through(tmp_path: Path):
    store = JsonFileSessionStore(tmp_path)
    store.save(_consensus_session("only"))
    history = store.parity_history(threshold=0.18)
    assert history[0][1] is not None
    assert history[0][1].parity_threshold == 0.18


def test_alignment_pair_history_aggregates_skew(tmp_path: Path):
    """Three sessions, all consensus_pair = {Sonnet, Haiku}. Aggregate skew is infinite (no other pair appears) — but if we add a session with a different pair, skew falls."""
    store = JsonFileSessionStore(tmp_path)
    store.save(_consensus_session("s1"))
    store.save(_consensus_session("s2"))
    store.save(_consensus_session("s3"))

    history = store.alignment_pair_history()
    aggregate = aggregate_alignment_pair_history(history)
    # All three sessions contribute one consensus tag with voters {Sonnet, Haiku}
    assert aggregate[frozenset({ModelId.SONNET, ModelId.HAIKU})] == 3
    # Only one pair appears → skew is the trivial 1.0 (max == min when there's a single bucket)
    assert compute_consensus_pair_skew(aggregate) == 1.0


def test_alignment_pair_history_skew_detects_dominance(tmp_path: Path):
    store = JsonFileSessionStore(tmp_path)

    # Sessions where {Sonnet, Haiku} is consensus
    for sid in ("d1", "d2", "d3", "d4", "d5"):
        store.save(_consensus_session(sid))

    # One session where {Opus, Haiku} is consensus on Sonnet's claim
    s, claims = _triad_session("balance")
    sonnet_id = claims[ModelId.SONNET].claim_id
    opus_t = Phase2Tagging(
        tagger_model=ModelId.OPUS,
        peer_tags=(ClaimTags(claim_id=sonnet_id, structural_pattern_tags=(StructuralPatternTag.DECOMPOSITION,)),),
    )
    haiku_t = Phase2Tagging(
        tagger_model=ModelId.HAIKU,
        peer_tags=(ClaimTags(claim_id=sonnet_id, structural_pattern_tags=(StructuralPatternTag.DECOMPOSITION,)),),
    )
    balanced, _ = _triad_session("balance", phase_2_taggings=(opus_t, haiku_t))
    store.save(balanced)

    aggregate = aggregate_alignment_pair_history(store.alignment_pair_history())
    skew = compute_consensus_pair_skew(aggregate)
    # 5x {Sonnet, Haiku}, 1x {Opus, Haiku} → skew = 5.0
    assert skew == 5.0
