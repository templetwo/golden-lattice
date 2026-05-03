"""End-to-end integration: Phase 1 wire → Session → metrics → store round-trip.

Exercises the seam between wire-layer parsers and substrate Pydantic models.
Composition bugs that the unit tests miss surface here. Triadic so the metrics
path returns a real SessionMetrics instead of None.
"""

from datetime import datetime, timezone
from pathlib import Path

from golden_lattice.exchange.phase_1_independent import (
    compose_phase_1_with_reflection,
    parse_phase_1_response_tool_use,
    parse_self_reflection_tool_use,
)
from golden_lattice.memory_graph.base import ModelId
from golden_lattice.memory_graph.metrics import compute_parity_shares
from golden_lattice.memory_graph.schema import Session
from golden_lattice.memory_graph.store import JsonFileSessionStore


NOW = datetime(2026, 5, 3, 12, 0, 0, tzinfo=timezone.utc)


def _phase_1_tool_input(focus_tag: str, claim_texts: list[str]) -> dict:
    return {
        "response": "the response prose",
        "focus_tag": focus_tag,
        "confidence": 0.75,
        "claim_texts": claim_texts,
    }


def test_phase_1_wire_to_session_to_store_roundtrip(tmp_path: Path):
    # Triadic Phase 1 emission with two claims per model.
    triad_inputs = {
        ModelId.OPUS: _phase_1_tool_input(
            "correctness", ["opus claim alpha", "opus claim beta"]
        ),
        ModelId.SONNET: _phase_1_tool_input(
            "clarity", ["sonnet claim alpha", "sonnet claim beta"]
        ),
        ModelId.HAIKU: _phase_1_tool_input(
            "speed", ["haiku claim alpha", "haiku claim beta"]
        ),
    }

    # Step 1: parse each Phase 1 emission via the wire.
    drafts = {
        model: parse_phase_1_response_tool_use(
            tool_input,
            expected_model=model,
            prompt_hash="prompt-h",
            generation_started_at=NOW,
            generation_completed_at=NOW,
        )
        for model, tool_input in triad_inputs.items()
    }

    # Step 2: build self-reflection inputs against each draft and parse.
    folded = {}
    for model, draft in drafts.items():
        own_ids = {c.claim_id for c in draft.claims}
        reflection_input = {
            "strongest_claim_id": draft.claims[0].claim_id,
            "weakest_claim_id": draft.claims[1].claim_id,
            "tag_justification": f"{model.value} chose its tag because of alpha",
        }
        reflection = parse_self_reflection_tool_use(
            reflection_input,
            expected_model=model,
            own_claim_ids=own_ids,
            generated_at=NOW,
        )
        folded[model] = compose_phase_1_with_reflection(draft, reflection)

    # Step 3: build a Session from the folded responses.
    session = Session(
        session_id="e2e-phase-1",
        prompt="design a cache",
        prompt_hash="prompt-h",
        models_invited=tuple(folded.keys()),
        phase_1=folded,
    )

    # Substrate validators all passed.
    assert len(session.phase_1) == 3
    for model in (ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU):
        resp = session.phase_1[model]
        assert len(resp.self_reflection_artifacts) == 1
        artifact = resp.self_reflection_artifacts[0]
        assert artifact.model_id is model
        assert artifact.strongest_claim_id in {c.claim_id for c in resp.claims}

    # Step 4: metrics path runs (triad → SessionMetrics, not None).
    metrics = compute_parity_shares(session)
    assert metrics is not None
    # Six total Phase 1 claims, two from each model → equal distinct_claim_share.
    for model in (ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU):
        assert abs(metrics.distinct_claim_share[model] - 1 / 3) < 1e-9
    # No Phase 2 yet, so consensus shares are all zero.
    for model in (ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU):
        assert metrics.edge_case_coverage_share[model] == 0.0
        assert metrics.structural_pattern_share[model] == 0.0

    # Step 5: store round-trip preserves everything via JSON.
    store = JsonFileSessionStore(tmp_path)
    store.save(session)
    loaded = store.load("e2e-phase-1")
    assert loaded.session_id == session.session_id
    assert loaded.models_invited == session.models_invited
    for model in (ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU):
        original = session.phase_1[model]
        round_tripped = loaded.phase_1[model]
        assert round_tripped.response == original.response
        assert round_tripped.focus_tag is original.focus_tag
        assert round_tripped.confidence == original.confidence
        assert round_tripped.claims == original.claims
        assert round_tripped.self_reflection_artifacts == original.self_reflection_artifacts
