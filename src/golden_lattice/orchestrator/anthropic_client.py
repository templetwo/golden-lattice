"""AnthropicClient — concrete provider client implementing all three wire Protocols.

Single class implements Phase1WireClient, Phase2WireClient, Phase3WireClient.
Per the audit: minimum ceremony, runtime_checkable Protocols still hold,
auth/retry/error mapping live in one place.

This module imports the anthropic SDK lazily — orchestrator tests using
StubAnthropicClient should not require the SDK to be installed. The import
happens inside __init__ so a missing SDK surfaces only when AnthropicClient
is instantiated, not when the module is imported.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Optional

from golden_lattice.exchange.phase_1_independent import (
    build_phase_1_response_prompt,
    build_self_reflection_prompt,
    parse_phase_1_response_tool_use,
    parse_self_reflection_tool_use,
    phase_1_response_tool_schema,
    self_reflection_tool_schema,
)
from golden_lattice.exchange.phase_2_cross_reading import (
    WireParseError,
    build_cross_reading_prompt,
    build_phase_2_tagging_prompt,
    cross_reading_tool_schema,
    parse_cross_reading_tool_use,
    parse_phase_2_tagging_tool_use,
    phase_2_tagging_tool_schema,
)
from golden_lattice.exchange.phase_3_dialogue import (
    build_phase_3_dialogue_prompt,
    parse_phase_3_dialogue_tool_use,
    phase_3_dialogue_tool_schema,
)
from golden_lattice.memory_graph.base import ModelId
from golden_lattice.memory_graph.schema import (
    Claim,
    CrossReading,
    DialogueTurn,
    IndependentResponse,
    SelfReflectionArtifact,
)
from golden_lattice.memory_graph.tagging import Phase2Tagging
from golden_lattice.orchestrator.errors import OrchestratorProviderError


def _prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


class AnthropicClient:
    """Implements Phase1WireClient, Phase2WireClient, Phase3WireClient.

    Wraps the anthropic SDK's async client. Each method:
      1. Builds the (system, user) prompts via the wire layer's builders.
      2. Calls messages.create with the appropriate tool schema and forced
         tool_choice.
      3. Extracts the tool_use content block.
      4. Delegates parsing to the wire layer's parser.

    Errors from the SDK get mapped to OrchestratorProviderError with model
    and phase context. Timeouts are NOT handled here — the orchestrator
    wraps each call in asyncio.wait_for and surfaces OrchestratorTimeoutError
    at the orchestrator layer.
    """

    def __init__(self, *, api_key: Optional[str] = None) -> None:
        try:
            import anthropic  # lazy import — stubs don't need the SDK
        except ImportError as exc:  # pragma: no cover - import-time check
            raise ImportError(
                "AnthropicClient requires the 'anthropic' package. "
                "Install with: pip install anthropic"
            ) from exc
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    # --- Phase 1 ---------------------------------------------------------

    async def submit_phase_1_response(
        self,
        *,
        model_id: ModelId,
        original_prompt: str,
        prompt_hash: str,
    ) -> IndependentResponse:
        system, user = build_phase_1_response_prompt(
            model_id=model_id, original_prompt=original_prompt
        )
        tool = phase_1_response_tool_schema()
        started = datetime.now(timezone.utc)
        try:
            response = await self._client.messages.create(
                model=model_id.value,
                max_tokens=4096,
                system=system,
                messages=[{"role": "user", "content": user}],
                tools=[tool],
                tool_choice={"type": "tool", "name": tool["name"]},
            )
        except Exception as exc:
            raise OrchestratorProviderError(
                model=model_id, phase="phase_1", underlying=exc
            ) from exc
        completed = datetime.now(timezone.utc)
        tool_input = _extract_tool_input(response, tool["name"])
        try:
            return parse_phase_1_response_tool_use(
                tool_input,
                expected_model=model_id,
                prompt_hash=prompt_hash,
                generation_started_at=started,
                generation_completed_at=completed,
            )
        except WireParseError as exc:
            raise WireParseError(
                f"[model={model_id.value}, phase=phase_1] {exc}"
            ) from exc

    async def submit_self_reflection(
        self,
        *,
        model_id: ModelId,
        phase_1_response: IndependentResponse,
    ) -> SelfReflectionArtifact:
        system, user = build_self_reflection_prompt(
            model_id=model_id, phase_1_response=phase_1_response
        )
        tool = self_reflection_tool_schema()
        try:
            response = await self._client.messages.create(
                model=model_id.value,
                max_tokens=2048,
                system=system,
                messages=[{"role": "user", "content": user}],
                tools=[tool],
                tool_choice={"type": "tool", "name": tool["name"]},
            )
        except Exception as exc:
            raise OrchestratorProviderError(
                model=model_id, phase="self_reflection", underlying=exc
            ) from exc
        tool_input = _extract_tool_input(response, tool["name"])
        own_claim_ids = {c.claim_id for c in phase_1_response.claims}
        try:
            return parse_self_reflection_tool_use(
                tool_input,
                expected_model=model_id,
                own_claim_ids=own_claim_ids,
                generated_at=datetime.now(timezone.utc),
            )
        except WireParseError as exc:
            raise WireParseError(
                f"[model={model_id.value}, phase=self_reflection] {exc}"
            ) from exc

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
        system, user = build_cross_reading_prompt(
            reader_model=reader_model,
            target_model=target_model,
            original_prompt=original_prompt,
            target_response=target_response,
            target_claims=target_claims,
            target_self_reflection=target_self_reflection,
        )
        tool = cross_reading_tool_schema()
        try:
            response = await self._client.messages.create(
                model=reader_model.value,
                max_tokens=4096,
                system=system,
                messages=[{"role": "user", "content": user}],
                tools=[tool],
                tool_choice={"type": "tool", "name": tool["name"]},
            )
        except Exception as exc:
            raise OrchestratorProviderError(
                model=reader_model, phase="phase_2_cross_reading", underlying=exc
            ) from exc
        tool_input = _extract_tool_input(response, tool["name"])
        try:
            return parse_cross_reading_tool_use(
                tool_input,
                expected_reader=reader_model,
                expected_target=target_model,
                valid_claim_ids=valid_claim_ids,
            )
        except WireParseError as exc:
            raise WireParseError(
                f"[reader={reader_model.value}, target={target_model.value}, "
                f"phase=phase_2_cross_reading] {exc}"
            ) from exc

    async def submit_phase_2_tagging(
        self,
        *,
        tagger_model: ModelId,
        original_prompt: str,
        own_claims: tuple[Claim, ...],
        peer_claims: tuple[Claim, ...],
        valid_claim_ids: Optional[set[str]] = None,
    ) -> Phase2Tagging:
        system, user = build_phase_2_tagging_prompt(
            tagger_model=tagger_model,
            original_prompt=original_prompt,
            own_claims=own_claims,
            peer_claims=peer_claims,
        )
        tool = phase_2_tagging_tool_schema()
        try:
            response = await self._client.messages.create(
                model=tagger_model.value,
                max_tokens=4096,
                system=system,
                messages=[{"role": "user", "content": user}],
                tools=[tool],
                tool_choice={"type": "tool", "name": tool["name"]},
            )
        except Exception as exc:
            raise OrchestratorProviderError(
                model=tagger_model, phase="phase_2_tagging", underlying=exc
            ) from exc
        tool_input = _extract_tool_input(response, tool["name"])
        try:
            return parse_phase_2_tagging_tool_use(
                tool_input,
                expected_tagger=tagger_model,
                valid_claim_ids=valid_claim_ids,
            )
        except WireParseError as exc:
            # Re-raise with tagger_model context so failure analysis preserves
            # which model produced the malformed emission. Without this wrap,
            # the WireParseError surfaces from the parser without model
            # attribution and the diagnostic data is lost.
            raise WireParseError(
                f"[tagger={tagger_model.value}, phase=phase_2_tagging] {exc}"
            ) from exc

    # --- Phase 3 ---------------------------------------------------------

    async def submit_phase_3_dialogue(
        self,
        *,
        speaker_model: ModelId,
        original_prompt: str,
        own_phase_1_response: str,
        peer_phase_1_blocks: tuple[tuple[ModelId, str, tuple[Claim, ...]], ...],
        valid_claim_ids: Optional[set[str]] = None,
    ) -> tuple[DialogueTurn, ...]:
        system, user = build_phase_3_dialogue_prompt(
            speaker_model=speaker_model,
            original_prompt=original_prompt,
            own_phase_1_response=own_phase_1_response,
            peer_phase_1_blocks=peer_phase_1_blocks,
        )
        tool = phase_3_dialogue_tool_schema()
        try:
            response = await self._client.messages.create(
                model=speaker_model.value,
                max_tokens=4096,
                system=system,
                messages=[{"role": "user", "content": user}],
                tools=[tool],
                tool_choice={"type": "tool", "name": tool["name"]},
            )
        except Exception as exc:
            raise OrchestratorProviderError(
                model=speaker_model, phase="phase_3", underlying=exc
            ) from exc
        tool_input = _extract_tool_input(response, tool["name"])
        try:
            return parse_phase_3_dialogue_tool_use(
                tool_input,
                expected_speaker=speaker_model,
                valid_claim_ids=valid_claim_ids,
                turn_id_prefix=f"{speaker_model.value}_p3_",
            )
        except WireParseError as exc:
            raise WireParseError(
                f"[speaker={speaker_model.value}, phase=phase_3] {exc}"
            ) from exc


def _extract_tool_input(response: Any, tool_name: str) -> dict[str, Any]:
    """Extract the tool_use block's input dict from an Anthropic messages response."""
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
            return dict(block.input)
    raise OrchestratorProviderError(
        model=ModelId.OPUS,  # placeholder — caller has better context
        phase="response_parsing",
        underlying=ValueError(f"No tool_use block named {tool_name!r} in response"),
    )
