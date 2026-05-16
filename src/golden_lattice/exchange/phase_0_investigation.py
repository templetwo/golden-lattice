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

from typing import Any, Protocol, Union, runtime_checkable

from golden_lattice.exchange.phase_2_cross_reading import WireParseError
from golden_lattice.memory_graph.base import INVESTIGATION_CAP, ModelId
from golden_lattice.memory_graph.phase_0 import (
    FailedSearch,
    InvestigationProposal,
    SearchResult,
)


__all__ = [
    "Phase0WireClient",
    "SearchClient",
    "investigation_proposal_tool_schema",
    "build_investigation_proposal_prompt",
    "parse_investigation_proposal_tool_use",
]


# --- Tool schema, builder, parser for the model-facing proposal call ----


def investigation_proposal_tool_schema(*, max_queries: int = INVESTIGATION_CAP) -> dict:
    """Forced tool_use schema for InvestigationProposal generation.

    The model is forced (via tool_choice) to call this tool with a single
    field, `queries`: an array of strings. Each string is either a search
    phrase or an http(s) URL — the orchestrator's SearchClient routes URLs
    to /extract (page fetch) and other strings to /search (ranked snippets).
    """
    return {
        "name": "propose_investigation",
        "description": (
            "Propose up to N investigations you want consulted before "
            "generating your response. Each entry is either a search query "
            "(free text) or an http(s) URL to fetch. The orchestrator "
            "executes the deduplicated union across all peer proposals and "
            "seeds results into a shared evidence feed before independent "
            "generation begins. Proposing zero investigations is valid if "
            "the prompt needs no external evidence."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": max_queries,
                    "description": (
                        "Each entry: a search query string OR an http(s) "
                        "URL to fetch. URLs route to extract; other strings "
                        "route to search. Empty array is valid (no "
                        "investigation needed)."
                    ),
                }
            },
            "required": ["queries"],
        },
    }


def build_investigation_proposal_prompt(
    *,
    model_id: ModelId,
    original_prompt: str,
    max_queries: int = INVESTIGATION_CAP,
) -> tuple[str, str]:
    """Build (system, user) prompt pair asking the model to propose
    investigations independently. No peer visibility — same independence
    discipline as Phase 1."""
    system = (
        f"You are {model_id.value}, one of three peer models in a multi-model "
        f"investigation phase (Golden Lattice Phase 0). Before any of you "
        f"generates a response, each of you independently proposes up to "
        f"{max_queries} investigations you want consulted — search queries or "
        f"http(s) URLs to fetch. The orchestrator executes the deduplicated "
        f"union across all peer proposals and shares the results with all "
        f"three of you before Phase 1 begins.\n\n"
        f"Propose only investigations that would meaningfully improve your "
        f"response. URL fetches are for specific known resources; searches "
        f"are for topics. Propose zero (empty queries array) if the prompt "
        f"needs no external evidence — that is an explicit valid state, not "
        f"a failure. Maximum {max_queries} per proposal; the cap is structural."
    )
    user = (
        "User prompt:\n\n"
        f"{original_prompt}\n\n"
        f"Propose your investigations now via the propose_investigation tool."
    )
    return system, user


def parse_investigation_proposal_tool_use(
    tool_input: dict[str, Any],
    *,
    expected_model: ModelId,
) -> InvestigationProposal:
    """Parse a propose_investigation tool_use into an InvestigationProposal.

    Wire-boundary discipline (matches the substrate's refusal landscape):
      - empty/whitespace queries are filtered (substrate would refuse them)
      - duplicates within the proposal are deduped (substrate would refuse)
      - over-cap queries are truncated to the cap (substrate would refuse)

    These are graceful wire-layer adjustments, not silent failures: a model
    that returns mildly malformed output produces a valid proposal rather
    than aborting the session. Substrate-level refusals (uniqueness, cap)
    are still load-bearing for hand-constructed Sessions in tests.
    """
    if "queries" not in tool_input:
        raise WireParseError(
            "propose_investigation tool_use missing required 'queries' field."
        )
    raw = tool_input["queries"]
    if not isinstance(raw, list):
        raise WireParseError(
            f"propose_investigation 'queries' must be a list, got {type(raw).__name__}."
        )
    for item in raw:
        if not isinstance(item, str):
            raise WireParseError(
                f"propose_investigation 'queries' must contain strings; "
                f"got {type(item).__name__} ({item!r})."
            )

    cleaned: list[str] = []
    seen: set[str] = set()
    for q in raw:
        q_stripped = q.strip()
        if not q_stripped:
            continue
        if q_stripped in seen:
            continue
        seen.add(q_stripped)
        cleaned.append(q_stripped)
        if len(cleaned) >= INVESTIGATION_CAP:
            break

    return InvestigationProposal(
        model_id=expected_model,
        queries=tuple(cleaned),
    )


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
