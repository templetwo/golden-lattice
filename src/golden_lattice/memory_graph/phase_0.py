"""Phase 0 — Investigation. Pre-Phase-1 evidence-gathering phase.

Each model independently proposes investigations; the orchestrator unifies
proposals by rule-based exact dedup and executes them via a non-model
search executor; results land in a shared evidence feed that all three
Phase 1 generations consume identically. The feed is frozen before Phase 1.

The four invariants apply identically:

  - **No authority gradient.** Proposals are independent (Phase 1 independence
    pattern). Dedup is mechanical exact-match — semantic merge would require
    an adjudicator, and either model or rule choice violates invariant 1.
  - **Symmetric visibility.** The frozen feed is identical to all three Phase 1
    generations.
  - **Contribution parity.** Investigation cap is flat per-model (INVESTIGATION_CAP),
    not differentiated. Differentiated USE is permitted (Haiku may make three
    broad probes while Opus makes one deep one); differentiated BUDGET would
    rebuild the Haiku→Sonnet→Opus pipeline through resource allocation.
  - **Irreducibility preservation.** Feed-grounded claims carry tool_provenance
    references to feed entries; the irreducibility trace covers them same as
    prior-grounded claims.

A failed search becomes a typed FailedSearch feed entry (all peers see it).
Failed evidence is itself shared evidence — no silent failures per §8.

The temporal grounding entry (DateTimeGrounding) is always the first feed
entry, seeded by the orchestrator before any model proposes. It is not a
tool call, not in cap accounting, and not subject to authority-gradient
concerns — same constitutional category as the prompt re-anchoring of §8.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Union

from pydantic import BaseModel, ConfigDict, model_validator

from golden_lattice.memory_graph.base import (
    INVESTIGATION_CAP,
    ModelId,
    feed_entry_id_for,
)


__all__ = [
    "DateTimeGrounding",
    "SearchResult",
    "FailedSearch",
    "FeedEntry",
    "InvestigationProposal",
    "Phase0Investigation",
    "datetime_grounding_id",
    "search_result_id",
    "failed_search_id",
]


def datetime_grounding_id(timestamp: datetime, timezone_name: str) -> str:
    return feed_entry_id_for(
        "datetime_grounding", timestamp.isoformat(), timezone_name
    )


def search_result_id(query: str, executed_at: datetime) -> str:
    return feed_entry_id_for("search_result", query, executed_at.isoformat())


def failed_search_id(query: str, attempted_at: datetime) -> str:
    return feed_entry_id_for("failed_search", query, attempted_at.isoformat())


class DateTimeGrounding(BaseModel):
    """The precondition feed entry. Always first in any Phase 0 feed.

    Deterministically computed by the orchestrator before any model proposes
    investigations. Not a tool call, not in cap accounting, no authority
    gradient — same constitutional category as §8 prompt re-anchoring.
    """

    model_config = ConfigDict(frozen=True)

    entry_id: str
    entry_type: Literal["datetime_grounding"] = "datetime_grounding"
    timestamp: datetime
    timezone_name: str
    formatted_text: str

    @model_validator(mode="after")
    def _entry_id_matches_content(self) -> "DateTimeGrounding":
        expected = datetime_grounding_id(self.timestamp, self.timezone_name)
        if self.entry_id != expected:
            raise ValueError(
                f"DateTimeGrounding entry_id {self.entry_id} does not match "
                f"content hash {expected}. Feed entries are content-addressed."
            )
        if not self.formatted_text.strip():
            raise ValueError("formatted_text must be non-empty.")
        if not self.timezone_name.strip():
            raise ValueError("timezone_name must be non-empty.")
        return self


class SearchResult(BaseModel):
    """Successful investigation result. Feed entry produced by the
    orchestrator's non-model search executor — no authority gradient."""

    model_config = ConfigDict(frozen=True)

    entry_id: str
    entry_type: Literal["search_result"] = "search_result"
    query: str
    result_text: str
    source_urls: tuple[str, ...] = ()
    executed_at: datetime

    @model_validator(mode="after")
    def _entry_id_matches_content(self) -> "SearchResult":
        expected = search_result_id(self.query, self.executed_at)
        if self.entry_id != expected:
            raise ValueError(
                f"SearchResult entry_id {self.entry_id} does not match "
                f"content hash {expected}. Feed entries are content-addressed."
            )
        if not self.query.strip():
            raise ValueError("query must be non-empty.")
        if not self.result_text.strip():
            raise ValueError(
                "result_text must be non-empty for a successful search. "
                "Use FailedSearch for executions that produced no result."
            )
        return self


class FailedSearch(BaseModel):
    """Failed investigation. Typed feed entry all peers see — failed evidence
    is itself shared evidence (§8 no-silent-failures)."""

    model_config = ConfigDict(frozen=True)

    entry_id: str
    entry_type: Literal["failed_search"] = "failed_search"
    query: str
    reason: str
    attempted_at: datetime

    @model_validator(mode="after")
    def _entry_id_matches_content(self) -> "FailedSearch":
        expected = failed_search_id(self.query, self.attempted_at)
        if self.entry_id != expected:
            raise ValueError(
                f"FailedSearch entry_id {self.entry_id} does not match "
                f"content hash {expected}. Feed entries are content-addressed."
            )
        if not self.query.strip():
            raise ValueError("query must be non-empty.")
        if not self.reason.strip():
            raise ValueError(
                "reason must be non-empty. A failed search without a stated "
                "reason is invisible failure — §8 no-silent-failures."
            )
        return self


FeedEntry = Union[DateTimeGrounding, SearchResult, FailedSearch]


class InvestigationProposal(BaseModel):
    """One model's set of proposed investigations.

    Submitted independently during Phase 0a (no peer visibility); combined
    with peer proposals by rule-based exact/structural union; never
    semantically merged (that would require an adjudicator → authority
    gradient → invariant 1 breach).
    """

    model_config = ConfigDict(frozen=True)

    model_id: ModelId
    queries: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _cap_and_uniqueness(self) -> "InvestigationProposal":
        if len(self.queries) > INVESTIGATION_CAP:
            raise ValueError(
                f"InvestigationProposal from {self.model_id.value} has "
                f"{len(self.queries)} queries; cap is {INVESTIGATION_CAP}. "
                "Flat per-model cap is load-bearing on invariant 1 — "
                "differentiated BUDGET would rebuild the Haiku→Sonnet→Opus "
                "pipeline through resource allocation."
            )
        if len(self.queries) != len(set(self.queries)):
            raise ValueError(
                f"InvestigationProposal from {self.model_id.value} has "
                "duplicate queries. A single model cannot duplicate its "
                "own proposals; dedup across models happens at union time."
            )
        for q in self.queries:
            if not q.strip():
                raise ValueError(
                    f"InvestigationProposal from {self.model_id.value} "
                    "has empty/whitespace-only query."
                )
        return self


class Phase0Investigation(BaseModel):
    """The Phase 0 artifact: per-model proposals + frozen evidence feed.

    Substrate enforces:
      - Each invited model submits at most one proposal (model_id unique).
      - First feed entry is DateTimeGrounding (temporal precondition).
      - Exactly one DateTimeGrounding entry in the feed.
      - Every non-grounding feed entry traces to at least one proposal's
        query (rule-based union, no orphan executions).
      - All feed entry_ids are distinct (content-addressed dedup at the
        construction boundary).
    """

    model_config = ConfigDict(frozen=True)

    proposals: tuple[InvestigationProposal, ...]
    feed: tuple[FeedEntry, ...]

    @model_validator(mode="after")
    def _proposal_model_ids_unique(self) -> "Phase0Investigation":
        model_ids = [p.model_id for p in self.proposals]
        if len(model_ids) != len(set(model_ids)):
            raise ValueError(
                "Phase0Investigation has duplicate proposal model_ids. "
                "Each invited model submits at most one proposal."
            )
        return self

    @model_validator(mode="after")
    def _feed_starts_with_datetime_grounding(self) -> "Phase0Investigation":
        if not self.feed:
            raise ValueError(
                "Phase 0 feed cannot be empty. The temporal grounding entry "
                "is the precondition and must always be present (and first)."
            )
        first = self.feed[0]
        if not isinstance(first, DateTimeGrounding):
            raise ValueError(
                f"First feed entry must be DateTimeGrounding (temporal "
                f"grounding precondition per §5.0). Got "
                f"{type(first).__name__}."
            )
        grounding_count = sum(
            1 for e in self.feed if isinstance(e, DateTimeGrounding)
        )
        if grounding_count != 1:
            raise ValueError(
                f"Phase 0 feed has {grounding_count} DateTimeGrounding "
                "entries; exactly one (the first) is required."
            )
        return self

    @model_validator(mode="after")
    def _feed_search_entries_trace_to_proposals(self) -> "Phase0Investigation":
        proposed_queries: set[str] = set()
        for p in self.proposals:
            proposed_queries.update(p.queries)
        for e in self.feed:
            if isinstance(e, DateTimeGrounding):
                continue
            if e.query not in proposed_queries:
                raise ValueError(
                    f"Feed entry {e.entry_id} has query {e.query!r} which "
                    "did not appear in any proposal. Every search in the "
                    "feed must trace to a proposed investigation — "
                    "rule-based union, no orphan executions."
                )
        return self

    @model_validator(mode="after")
    def _feed_entry_ids_unique(self) -> "Phase0Investigation":
        ids = [e.entry_id for e in self.feed]
        if len(ids) != len(set(ids)):
            raise ValueError(
                "Phase 0 feed has duplicate entry_id values. Content-"
                "addressed dedup is invariant: two entries with identical "
                "content collapse to one at construction."
            )
        return self

    def feed_entry_ids(self) -> set[str]:
        return {e.entry_id for e in self.feed}
