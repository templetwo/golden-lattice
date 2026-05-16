"""Tests for Phase 0 (Investigation) schema — ARCHITECTURE.md §5.0.

Each test exercises one structural refusal: things the architecture must
make impossible to construct, not merely warn against. The four invariants
apply to Phase 0 the same way they apply to Phases 1–4 — these tests are
the runtime enforcement.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from golden_lattice.memory_graph.base import (
    INVESTIGATION_CAP,
    INVESTIGATION_TIMEZONE,
    ModelId,
)
from golden_lattice.memory_graph.phase_0 import (
    DateTimeGrounding,
    FailedSearch,
    InvestigationProposal,
    Phase0Investigation,
    SearchResult,
    datetime_grounding_id,
    failed_search_id,
    search_result_id,
)


NOW = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)


# --- DateTimeGrounding ---------------------------------------------------


def _grounding(when: datetime = NOW, tz: str = INVESTIGATION_TIMEZONE) -> DateTimeGrounding:
    return DateTimeGrounding(
        entry_id=datetime_grounding_id(when, tz),
        timestamp=when,
        timezone_name=tz,
        formatted_text=f"{when.isoformat()} ({tz})",
    )


def test_datetime_grounding_content_addressed_id():
    g = _grounding()
    assert g.entry_id == datetime_grounding_id(NOW, INVESTIGATION_TIMEZONE)


def test_datetime_grounding_refuses_mismatched_id():
    with pytest.raises(ValueError, match="content hash"):
        DateTimeGrounding(
            entry_id="0123456789abcdef",
            timestamp=NOW,
            timezone_name=INVESTIGATION_TIMEZONE,
            formatted_text="ignored",
        )


def test_datetime_grounding_refuses_empty_formatted_text():
    with pytest.raises(ValueError, match="formatted_text"):
        DateTimeGrounding(
            entry_id=datetime_grounding_id(NOW, INVESTIGATION_TIMEZONE),
            timestamp=NOW,
            timezone_name=INVESTIGATION_TIMEZONE,
            formatted_text="   ",
        )


def test_datetime_grounding_refuses_empty_timezone():
    with pytest.raises(ValueError, match="timezone_name"):
        DateTimeGrounding(
            entry_id=datetime_grounding_id(NOW, ""),
            timestamp=NOW,
            timezone_name="",
            formatted_text="x",
        )


# --- SearchResult --------------------------------------------------------


def _search(query: str = "what is the date today", text: str = "It is 2026-05-16.") -> SearchResult:
    return SearchResult(
        entry_id=search_result_id(query, NOW),
        query=query,
        result_text=text,
        source_urls=("https://example.com",),
        executed_at=NOW,
    )


def test_search_result_content_addressed_id():
    r = _search()
    assert r.entry_id == search_result_id(r.query, r.executed_at)


def test_search_result_refuses_mismatched_id():
    with pytest.raises(ValueError, match="content hash"):
        SearchResult(
            entry_id="deadbeefdeadbeef",
            query="q",
            result_text="r",
            executed_at=NOW,
        )


def test_search_result_refuses_empty_query():
    with pytest.raises(ValueError, match="query"):
        SearchResult(
            entry_id=search_result_id("", NOW),
            query="",
            result_text="r",
            executed_at=NOW,
        )


def test_search_result_refuses_empty_result_text():
    """A search that produced no result should be a FailedSearch, not an
    empty SearchResult — the type distinction is load-bearing for §8."""
    with pytest.raises(ValueError, match="result_text"):
        SearchResult(
            entry_id=search_result_id("q", NOW),
            query="q",
            result_text="",
            executed_at=NOW,
        )


# --- FailedSearch --------------------------------------------------------


def _failed(query: str = "what is the date today", reason: str = "rate limited") -> FailedSearch:
    return FailedSearch(
        entry_id=failed_search_id(query, NOW),
        query=query,
        reason=reason,
        attempted_at=NOW,
    )


def test_failed_search_content_addressed_id():
    f = _failed()
    assert f.entry_id == failed_search_id(f.query, f.attempted_at)


def test_failed_search_refuses_empty_reason():
    """A failed search without a stated reason is invisible failure — §8."""
    with pytest.raises(ValueError, match="reason"):
        FailedSearch(
            entry_id=failed_search_id("q", NOW),
            query="q",
            reason="",
            attempted_at=NOW,
        )


# --- InvestigationProposal ----------------------------------------------


def test_proposal_accepts_up_to_cap():
    queries = tuple(f"query {i}" for i in range(INVESTIGATION_CAP))
    p = InvestigationProposal(model_id=ModelId.OPUS, queries=queries)
    assert len(p.queries) == INVESTIGATION_CAP


def test_proposal_refuses_over_cap():
    queries = tuple(f"query {i}" for i in range(INVESTIGATION_CAP + 1))
    with pytest.raises(ValueError, match="cap"):
        InvestigationProposal(model_id=ModelId.OPUS, queries=queries)


def test_proposal_refuses_duplicate_queries():
    with pytest.raises(ValueError, match="duplicate queries"):
        InvestigationProposal(
            model_id=ModelId.OPUS,
            queries=("same", "same"),
        )


def test_proposal_refuses_empty_query():
    with pytest.raises(ValueError, match="empty"):
        InvestigationProposal(
            model_id=ModelId.OPUS,
            queries=("real query", "   "),
        )


def test_proposal_accepts_empty_queries_tuple():
    """A model proposing zero investigations is valid (empty union → skip
    to Phase 1 with grounding-only feed)."""
    p = InvestigationProposal(model_id=ModelId.OPUS, queries=())
    assert p.queries == ()


# --- Phase0Investigation ------------------------------------------------


def _well_formed_investigation() -> Phase0Investigation:
    grounding = _grounding()
    proposal = InvestigationProposal(
        model_id=ModelId.OPUS,
        queries=("what is the date today",),
    )
    result = _search(query="what is the date today", text="It is 2026-05-16.")
    return Phase0Investigation(
        proposals=(proposal,),
        feed=(grounding, result),
    )


def test_phase0_well_formed_constructs():
    inv = _well_formed_investigation()
    assert len(inv.feed) == 2
    assert isinstance(inv.feed[0], DateTimeGrounding)
    assert isinstance(inv.feed[1], SearchResult)


def test_phase0_refuses_empty_feed():
    with pytest.raises(ValueError, match="empty"):
        Phase0Investigation(proposals=(), feed=())


def test_phase0_refuses_feed_not_starting_with_grounding():
    result = _search()
    grounding = _grounding()
    proposal = InvestigationProposal(
        model_id=ModelId.OPUS,
        queries=(result.query,),
    )
    with pytest.raises(ValueError, match="DateTimeGrounding"):
        Phase0Investigation(
            proposals=(proposal,),
            feed=(result, grounding),  # wrong order — search before grounding
        )


def test_phase0_refuses_multiple_grounding_entries():
    g1 = _grounding()
    g2 = _grounding(when=NOW + timedelta(seconds=1))
    with pytest.raises(ValueError, match="DateTimeGrounding"):
        Phase0Investigation(proposals=(), feed=(g1, g2))


def test_phase0_refuses_orphan_search_entry():
    """A search entry must trace to some proposal's query — rule-based
    union does not produce orphan executions."""
    g = _grounding()
    proposal = InvestigationProposal(
        model_id=ModelId.OPUS,
        queries=("proposed query",),
    )
    orphan_search = _search(query="never proposed", text="x")
    with pytest.raises(ValueError, match="did not appear in any proposal"):
        Phase0Investigation(
            proposals=(proposal,),
            feed=(g, orphan_search),
        )


def test_phase0_refuses_duplicate_proposal_model_ids():
    p1 = InvestigationProposal(model_id=ModelId.OPUS, queries=("a",))
    p2 = InvestigationProposal(model_id=ModelId.OPUS, queries=("b",))
    g = _grounding()
    with pytest.raises(ValueError, match="duplicate proposal model_ids"):
        Phase0Investigation(proposals=(p1, p2), feed=(g,))


def test_phase0_refuses_duplicate_feed_entry_ids():
    """Content-addressed dedup is invariant: two entries with identical
    content collapse to one entry_id at construction. Two FeedEntry objects
    sharing an entry_id must not appear in the same feed."""
    g = _grounding()
    proposal = InvestigationProposal(model_id=ModelId.OPUS, queries=("q",))
    r1 = SearchResult(
        entry_id=search_result_id("q", NOW),
        query="q",
        result_text="text",
        executed_at=NOW,
    )
    r2 = SearchResult(
        entry_id=search_result_id("q", NOW),
        query="q",
        result_text="text",
        executed_at=NOW,
    )
    with pytest.raises(ValueError, match="duplicate entry_id"):
        Phase0Investigation(proposals=(proposal,), feed=(g, r1, r2))


def test_phase0_grounding_only_feed_is_valid():
    """Empty union → skip to Phase 1 path: feed contains only the grounding
    entry, no proposals (or empty proposals). Valid."""
    inv = Phase0Investigation(proposals=(), feed=(_grounding(),))
    assert len(inv.feed) == 1


def test_phase0_feed_entry_ids_helper():
    inv = _well_formed_investigation()
    ids = inv.feed_entry_ids()
    assert len(ids) == 2
    for e in inv.feed:
        assert e.entry_id in ids


# --- Failed search threads through to a clean feed ----------------------


def test_phase0_accepts_failed_search_traced_to_proposal():
    g = _grounding()
    proposal = InvestigationProposal(
        model_id=ModelId.OPUS,
        queries=("query that fails",),
    )
    failed = _failed(query="query that fails", reason="rate limited")
    inv = Phase0Investigation(proposals=(proposal,), feed=(g, failed))
    assert isinstance(inv.feed[1], FailedSearch)


# --- Session-level integration: tool_provenance resolves to feed --------


from golden_lattice.memory_graph.base import FocusTag, Phase, claim_id_for
from golden_lattice.memory_graph.schema import (
    Claim,
    IndependentResponse,
    Session,
)


def _claim(model: ModelId, text: str, tool_provenance: tuple[str, ...] = ()) -> Claim:
    return Claim(
        claim_id=claim_id_for(model, Phase.INDEPENDENT, text),
        source_model=model,
        source_phase=Phase.INDEPENDENT,
        text=text,
        tool_provenance=tool_provenance,
    )


def _response(model: ModelId, claims: tuple[Claim, ...]) -> IndependentResponse:
    return IndependentResponse(
        model_id=model,
        prompt_hash="h",
        response="r",
        focus_tag=FocusTag.CORRECTNESS,
        confidence=0.7,
        claims=claims,
        generation_started_at=NOW,
        generation_completed_at=NOW,
    )


def _minimal_triad(
    *,
    phase_0: Phase0Investigation | None = None,
    claim_tool_provenance: dict[ModelId, tuple[str, ...]] | None = None,
) -> Session:
    """Triadic session helper. claim_tool_provenance maps a model to the
    tool_provenance tuple to attach to that model's single claim."""
    tp = claim_tool_provenance or {}
    return Session(
        session_id="phase-0-int",
        prompt="p",
        prompt_hash="h",
        models_invited=(ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU),
        phase_0=phase_0,
        phase_1={
            m: _response(m, (_claim(m, f"{m.value} claim", tp.get(m, ())),))
            for m in (ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU)
        },
    )


def test_session_accepts_phase_0_none_with_no_tool_provenance():
    s = _minimal_triad(phase_0=None)
    assert s.phase_0 is None
    for c in s.all_claims():
        assert c.tool_provenance == ()


def test_session_refuses_tool_provenance_when_phase_0_is_none():
    """Feed-grounding without a feed is impossible. Construction refuses."""
    with pytest.raises(ValueError, match="phase_0 is\\s+None"):
        _minimal_triad(
            phase_0=None,
            claim_tool_provenance={ModelId.OPUS: ("0123456789abcdef",)},
        )


def test_session_accepts_tool_provenance_resolving_to_feed():
    inv = _well_formed_investigation()
    # _well_formed_investigation has a SearchResult; reference its entry_id.
    feed_entry_id = inv.feed[1].entry_id
    s = _minimal_triad(
        phase_0=inv,
        claim_tool_provenance={ModelId.OPUS: (feed_entry_id,)},
    )
    opus_claim = s.phase_1[ModelId.OPUS].claims[0]
    assert opus_claim.tool_provenance == (feed_entry_id,)


def test_session_refuses_tool_provenance_referencing_unknown_entry():
    inv = _well_formed_investigation()
    with pytest.raises(ValueError, match="does not resolve to a Phase 0 feed"):
        _minimal_triad(
            phase_0=inv,
            claim_tool_provenance={ModelId.OPUS: ("ffffffffffffffff",)},
        )


def test_session_refuses_proposal_from_uninvited_model():
    """Phase 0 proposals must come from invited models — symmetric
    visibility at the investigation layer."""
    g = _grounding()
    # Construct a Phase 0 where Sonnet proposes, then build a session that
    # does NOT invite Sonnet.
    proposal_from_sonnet = InvestigationProposal(
        model_id=ModelId.SONNET, queries=("q",)
    )
    inv = Phase0Investigation(proposals=(proposal_from_sonnet,), feed=(g,))
    with pytest.raises(ValueError, match="not in models_invited"):
        Session(
            session_id="bad-invite",
            prompt="p",
            prompt_hash="h",
            # Sonnet missing from invited list:
            models_invited=(ModelId.OPUS, ModelId.HAIKU),
            phase_0=inv,
            phase_1={
                ModelId.OPUS: _response(ModelId.OPUS, (_claim(ModelId.OPUS, "o"),)),
                ModelId.HAIKU: _response(ModelId.HAIKU, (_claim(ModelId.HAIKU, "h"),)),
            },
        )
