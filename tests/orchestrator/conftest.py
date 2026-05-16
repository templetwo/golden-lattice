"""Shared test fixtures for orchestrator tests.

StubAnthropicClient: satisfies all three wire Protocols with configurable
async callables. Tests assign per-method scripts that return Pydantic types.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import pytest

from golden_lattice.memory_graph.base import (
    FocusTag,
    ModelId,
    Phase,
    claim_id_for,
)
from golden_lattice.memory_graph.phase_0 import (
    FailedSearch,
    InvestigationProposal,
    SearchResult,
    failed_search_id,
    search_result_id,
)
from golden_lattice.memory_graph.schema import (
    Claim,
    CrossReading,
    DialogueTurn,
    IndependentResponse,
    SelfReflectionArtifact,
)
from golden_lattice.memory_graph.tagging import Phase2Tagging


NOW = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)


def make_phase_1_response(
    model: ModelId,
    *,
    prompt_hash: str = "h",
    claims_text: tuple[str, ...] = ("alpha", "beta"),
    focus_tag: FocusTag = FocusTag.CORRECTNESS,
    confidence: float = 0.8,
) -> IndependentResponse:
    """Helper to build Phase 1 responses with N claims."""
    claims = tuple(
        Claim(
            claim_id=claim_id_for(model, Phase.INDEPENDENT, f"{model.value} {t}"),
            source_model=model,
            source_phase=Phase.INDEPENDENT,
            text=f"{model.value} {t}",
        )
        for t in claims_text
    )
    return IndependentResponse(
        model_id=model,
        prompt_hash=prompt_hash,
        response=f"{model.value} response prose",
        focus_tag=focus_tag,
        confidence=confidence,
        claims=claims,
        generation_started_at=NOW,
        generation_completed_at=NOW,
    )


def make_self_reflection(
    model: ModelId,
    response: IndependentResponse,
) -> SelfReflectionArtifact:
    return SelfReflectionArtifact(
        model_id=model,
        generated_at=NOW,
        strongest_claim_id=response.claims[0].claim_id,
        weakest_claim_id=response.claims[1].claim_id,
        tag_justification=f"{model.value} chose its tag because of alpha",
    )


class StubAnthropicClient:
    """Stub satisfying Phase1WireClient + Phase2WireClient + Phase3WireClient.

    Each method delegates to an async callable that the test provides. Default
    callables return minimal substrate-valid artifacts for end-to-end happy-path
    flow without requiring tests to script every detail.
    """

    def __init__(self) -> None:
        # Default scripts: minimal valid responses.
        self.phase_1_responses: dict[ModelId, IndependentResponse] = {
            m: make_phase_1_response(m) for m in (ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU)
        }
        self.phase_1_delay_seconds: dict[ModelId, float] = {}
        self.self_reflection_delay_seconds: dict[ModelId, float] = {}
        self.cross_reading_delay_seconds: float = 0.0
        self.tagging_delay_seconds: float = 0.0
        self.phase_3_delay_seconds: float = 0.0

        # Hooks for tests that need per-call control.
        self.phase_1_hook: Optional[Callable[..., Any]] = None
        self.self_reflection_hook: Optional[Callable[..., Any]] = None
        self.cross_reading_hook: Optional[Callable[..., Any]] = None
        self.tagging_hook: Optional[Callable[..., Any]] = None
        self.phase_3_hook: Optional[Callable[..., Any]] = None

    # --- Phase 1 ---------------------------------------------------------

    async def submit_phase_1_response(
        self,
        *,
        model_id: ModelId,
        original_prompt: str,
        prompt_hash: str,
        feed: Optional[tuple] = None,
    ) -> IndependentResponse:
        delay = self.phase_1_delay_seconds.get(model_id, 0.0)
        if delay:
            await asyncio.sleep(delay)
        if self.phase_1_hook is not None:
            return await self.phase_1_hook(
                model_id=model_id,
                original_prompt=original_prompt,
                prompt_hash=prompt_hash,
            )
        # Default: prebuilt response with prompt_hash patched.
        canned = self.phase_1_responses[model_id]
        if canned.prompt_hash == prompt_hash:
            return canned
        return canned.model_copy(update={"prompt_hash": prompt_hash})

    async def submit_self_reflection(
        self,
        *,
        model_id: ModelId,
        phase_1_response: IndependentResponse,
    ) -> SelfReflectionArtifact:
        delay = self.self_reflection_delay_seconds.get(model_id, 0.0)
        if delay:
            await asyncio.sleep(delay)
        if self.self_reflection_hook is not None:
            return await self.self_reflection_hook(
                model_id=model_id,
                phase_1_response=phase_1_response,
            )
        return make_self_reflection(model_id, phase_1_response)

    # --- Phase 2 ---------------------------------------------------------

    async def submit_cross_reading(
        self,
        *,
        reader_model: ModelId,
        target_model: ModelId,
        original_prompt: str,
        target_response: str,
        target_claims: tuple[Claim, ...],
        target_self_reflection: Optional[str] = None,
        valid_claim_ids: Optional[set[str]] = None,
    ) -> CrossReading:
        if self.cross_reading_delay_seconds:
            await asyncio.sleep(self.cross_reading_delay_seconds)
        if self.cross_reading_hook is not None:
            return await self.cross_reading_hook(
                reader_model=reader_model, target_model=target_model
            )
        # Default: empty agreements/disagreements/missing.
        return CrossReading(reader_model=reader_model, target_model=target_model)

    async def submit_phase_2_tagging(
        self,
        *,
        tagger_model: ModelId,
        original_prompt: str,
        own_claims: tuple[Claim, ...],
        peer_claims: tuple[Claim, ...],
        valid_claim_ids: Optional[set[str]] = None,
    ) -> Phase2Tagging:
        if self.tagging_delay_seconds:
            await asyncio.sleep(self.tagging_delay_seconds)
        if self.tagging_hook is not None:
            return await self.tagging_hook(tagger_model=tagger_model)
        return Phase2Tagging(tagger_model=tagger_model)

    # --- Phase 3 ---------------------------------------------------------

    async def submit_phase_3_dialogue(
        self,
        *,
        speaker_model: ModelId,
        original_prompt: str,
        own_phase_1_response: str,
        peer_phase_1_blocks,
        valid_claim_ids: Optional[set[str]] = None,
    ) -> tuple[DialogueTurn, ...]:
        if self.phase_3_delay_seconds:
            await asyncio.sleep(self.phase_3_delay_seconds)
        if self.phase_3_hook is not None:
            return await self.phase_3_hook(speaker_model=speaker_model)
        # Default: empty dialogue.
        return ()


class StubPhase0Client:
    """Stub satisfying Phase0WireClient — returns canned investigation
    proposals per model.

    Default: each model proposes nothing (empty-union path). Tests that
    exercise Phase 0 with proposals set them via the canned_proposals dict.
    """

    def __init__(self) -> None:
        self.canned_proposals: dict[ModelId, tuple[str, ...]] = {
            ModelId.OPUS: (),
            ModelId.SONNET: (),
            ModelId.HAIKU: (),
        }
        self.proposal_delay_seconds: dict[ModelId, float] = {}

    async def submit_investigation_proposal(
        self,
        *,
        model_id: ModelId,
        original_prompt: str,
        max_queries: int,
    ) -> InvestigationProposal:
        delay = self.proposal_delay_seconds.get(model_id, 0.0)
        if delay:
            await asyncio.sleep(delay)
        queries = self.canned_proposals.get(model_id, ())[:max_queries]
        return InvestigationProposal(model_id=model_id, queries=queries)


class StubSearchClient:
    """Stub satisfying SearchClient — maps queries to canned results or
    canned failures. Defaults to a generic FailedSearch on unknown queries
    so tests must opt-in to results explicitly."""

    def __init__(self) -> None:
        self.results: dict[str, str] = {}
        self.fail_reasons: dict[str, str] = {}
        self.search_delay_seconds: float = 0.0
        # When set, all searches sleep this long (useful for timeout tests).

    async def execute_search(
        self, query: str
    ):  # -> Union[SearchResult, FailedSearch]
        if self.search_delay_seconds:
            await asyncio.sleep(self.search_delay_seconds)
        if query in self.fail_reasons:
            return FailedSearch(
                entry_id=failed_search_id(query, NOW),
                query=query,
                reason=self.fail_reasons[query],
                attempted_at=NOW,
            )
        if query in self.results:
            return SearchResult(
                entry_id=search_result_id(query, NOW),
                query=query,
                result_text=self.results[query],
                source_urls=("https://stub.example/" + query.replace(" ", "_"),),
                executed_at=NOW,
            )
        return FailedSearch(
            entry_id=failed_search_id(query, NOW),
            query=query,
            reason=f"stub has no canned result for {query!r}",
            attempted_at=NOW,
        )


@pytest.fixture
def stub_phase_0_client() -> StubPhase0Client:
    return StubPhase0Client()


@pytest.fixture
def stub_search_client() -> StubSearchClient:
    return StubSearchClient()


@pytest.fixture
def stub_client() -> StubAnthropicClient:
    return StubAnthropicClient()
