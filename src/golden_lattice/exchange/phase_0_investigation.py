"""Phase 0 wire layer — the seam between the orchestrator and the outside world.

Two interfaces:

  Phase0WireClient — a model-facing async client. Each invited model runs
    this independently during Phase 0a (no peer visibility per the Phase 1
    independence pattern). Returns an InvestigationProposal: the queries
    the model wants answered before generating its Phase 1 response.

  SearchClient — a non-model search executor. Pure I/O to a search service
    (Tavily, Brave, Anthropic's web_search, or any other). Takes a query
    string and returns either a SearchResult (success) or a FailedSearch
    (typed failure). Authority-gradient-clean because no model decides
    what to return — the executor is not a model.

Both are runtime_checkable Protocols. Concrete implementations
(AnthropicPhase0Client against the API, TavilySearchClient against Tavily's
HTTP endpoint, etc.) live separately. Test stubs satisfy both Protocols
without any network access.

The orchestrator's _run_phase_0 helper takes both clients as parameters and
weaves them together: gather proposals from N model wire calls in parallel,
unify by rule-based exact dedup, dispatch search executions in parallel,
build the frozen evidence feed.
"""

from __future__ import annotations

from typing import Protocol, Union, runtime_checkable

from golden_lattice.memory_graph.base import ModelId
from golden_lattice.memory_graph.phase_0 import (
    FailedSearch,
    InvestigationProposal,
    SearchResult,
)


__all__ = [
    "Phase0WireClient",
    "SearchClient",
]


@runtime_checkable
class Phase0WireClient(Protocol):
    """Model-facing async client for Phase 0 proposals.

    Concrete implementations: AnthropicPhase0Client (against the Anthropic
    API), test stubs (canned proposals). Each call is independent — no peer
    visibility, no shared state across model_ids in a single Phase 0 dispatch.
    """

    async def submit_investigation_proposal(
        self,
        *,
        model_id: ModelId,
        original_prompt: str,
        max_queries: int,
    ) -> InvestigationProposal:
        """Ask the given model to propose investigations for the prompt.

        Implementations must respect max_queries as a hard cap (matching the
        substrate's INVESTIGATION_CAP). Returning more queries than the cap
        is a wire-layer bug; the substrate would refuse the InvestigationProposal
        at construction anyway, but the wire layer should enforce the cap
        before construction.
        """
        ...


@runtime_checkable
class SearchClient(Protocol):
    """Non-model search executor.

    Concrete implementations: TavilySearchClient, BraveSearchClient, any
    HTTP search API client, test stubs. Pure I/O — no model involvement,
    no authority gradient implication.

    Returns SearchResult on success, FailedSearch on failure. Network
    exceptions and timeouts should be caught and converted to FailedSearch
    (typed failure per §8 no-silent-failures). Precondition failures
    (credit balance, executor unreachable at startup) remain orchestrator-
    layer aborts; they are not feed entries.
    """

    async def execute_search(
        self, query: str
    ) -> Union[SearchResult, FailedSearch]:
        ...
