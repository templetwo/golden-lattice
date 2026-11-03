"""Seeded fixture client for dry runs (eval harness step 2).

Satisfies the Phase 1, 2 and 3 wire protocols without any network access.
Output is a deterministic function of (seed, model, phase, prompt hash), so a
dry run is reproducible and can be replayed in tests. This is NOT evidence:
fixture prose and claims carry no content about the prompt. Reports must
label any run that used this client as "dry-run (fixture data, not evidence)".

Lives in src/ on purpose. It must not import from tests/, which would make
the harness depend on test helpers.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
from datetime import datetime, timezone
from typing import Optional

from golden_lattice.memory_graph.base import FocusTag, ModelId, Phase, claim_id_for
from golden_lattice.memory_graph.schema import (
    Claim,
    CrossReading,
    DialogueTurn,
    IndependentResponse,
    SelfReflectionArtifact,
)
from golden_lattice.memory_graph.tagging import Phase2Tagging

FIXTURE_TIME = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)
CLAIMS_PER_MODEL = 3
_FOCUS_ORDER = tuple(FocusTag)


class FixtureClient:
    """Deterministic, offline client satisfying Phase1/2/3 wire protocols."""

    def __init__(self, seed: int = 0) -> None:
        self._seed = seed
        self.calls: list[str] = []  # method names, for tests that audit traffic

    def _rng(self, *parts: str) -> random.Random:
        digest = hashlib.sha256(f"{self._seed}|{'|'.join(parts)}".encode()).digest()
        return random.Random(int.from_bytes(digest[:8], "big"))

    # --- Phase 1 ---------------------------------------------------------

    async def submit_phase_1_response(
        self,
        *,
        model_id: ModelId,
        original_prompt: str,
        prompt_hash: str,
        feed: Optional[tuple] = None,
    ) -> IndependentResponse:
        self.calls.append("phase_1")
        await asyncio.sleep(0)
        rng = self._rng(model_id.value, "p1", prompt_hash)
        texts = tuple(
            f"{model_id.value} fixture claim {i} variant {rng.randrange(10_000)}"
            for i in range(CLAIMS_PER_MODEL)
        )
        claims = tuple(
            Claim(
                claim_id=claim_id_for(model_id, Phase.INDEPENDENT, text),
                source_model=model_id,
                source_phase=Phase.INDEPENDENT,
                text=text,
            )
            for text in texts
        )
        return IndependentResponse(
            model_id=model_id,
            prompt_hash=prompt_hash,
            response=f"{model_id.value} fixture response",
            focus_tag=_FOCUS_ORDER[rng.randrange(len(_FOCUS_ORDER))],
            confidence=round(0.5 + rng.random() / 2, 3),
            claims=claims,
            generation_started_at=FIXTURE_TIME,
            generation_completed_at=FIXTURE_TIME,
        )

    async def submit_self_reflection(
        self,
        *,
        model_id: ModelId,
        phase_1_response: IndependentResponse,
    ) -> SelfReflectionArtifact:
        self.calls.append("self_reflection")
        await asyncio.sleep(0)
        return SelfReflectionArtifact(
            model_id=model_id,
            generated_at=FIXTURE_TIME,
            strongest_claim_id=phase_1_response.claims[0].claim_id,
            weakest_claim_id=phase_1_response.claims[1].claim_id,
            tag_justification=f"{model_id.value} fixture tag justification",
        )

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
        self.calls.append("cross_reading")
        await asyncio.sleep(0)
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
        self.calls.append("tagging")
        await asyncio.sleep(0)
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
        self.calls.append("phase_3")
        await asyncio.sleep(0)
        return ()

