"""End-to-end integration: Phase 3 wire → Session → store round-trip.

Triadic session with Phase 1 emissions and a Phase 3 dialogue from each speaker.
Exercises the wire parser → DialogueTurn substrate validators → Session-level
cap and resolution validators → JSON serialization round-trip.
"""

from datetime import datetime, timezone
from pathlib import Path

from golden_lattice.exchange.phase_1_independent import (
    compose_phase_1_with_reflection,
    parse_phase_1_response_tool_use,
    parse_self_reflection_tool_use,
)
from golden_lattice.exchange.phase_3_dialogue import (
    parse_phase_3_dialogue_tool_use,
)
from golden_lattice.memory_graph.base import ModelId
from golden_lattice.memory_graph.schema import Session
from golden_lattice.memory_graph.store import JsonFileSessionStore


NOW = datetime(2026, 5, 3, 12, 0, 0, tzinfo=timezone.utc)


def _phase_1_input(focus_tag: str, claim_texts: list[str]) -> dict:
    return {
        "response": "the response prose",
        "focus_tag": focus_tag,
        "confidence": 0.7,
        "claim_texts": claim_texts,
    }


def test_phase_3_wire_to_session_to_store_roundtrip(tmp_path: Path):
    # Step 1: build Phase 1 for all three siblings.
    p1_inputs = {
        ModelId.OPUS: _phase_1_input("correctness", ["opus alpha", "opus beta"]),
        ModelId.SONNET: _phase_1_input("clarity", ["sonnet alpha", "sonnet beta"]),
        ModelId.HAIKU: _phase_1_input("speed", ["haiku alpha", "haiku beta"]),
    }
    folded = {}
    for model, tool_input in p1_inputs.items():
        draft = parse_phase_1_response_tool_use(
            tool_input,
            expected_model=model,
            prompt_hash="ph",
            generation_started_at=NOW,
            generation_completed_at=NOW,
        )
        own_ids = {c.claim_id for c in draft.claims}
        reflection = parse_self_reflection_tool_use(
            {
                "strongest_claim_id": draft.claims[0].claim_id,
                "weakest_claim_id": draft.claims[1].claim_id,
                "tag_justification": f"{model.value} chose its tag",
            },
            expected_model=model,
            own_claim_ids=own_ids,
            generated_at=NOW,
        )
        folded[model] = compose_phase_1_with_reflection(draft, reflection)

    # All claim_ids that exist by the time Phase 3 runs.
    all_claim_ids: set[str] = set()
    for resp in folded.values():
        for c in resp.claims:
            all_claim_ids.add(c.claim_id)

    # Step 2: each model emits Phase 3 dialogue.
    opus_p3_input = {
        "turns": [
            {
                "channel": "critique",
                "target_model": ModelId.SONNET.value,
                "target_claim_ids": [folded[ModelId.SONNET].claims[0].claim_id],
                "content": "sonnet alpha is too narrow",
            },
            {
                "channel": "critique",
                "target_model": ModelId.HAIKU.value,
                "target_claim_ids": [folded[ModelId.HAIKU].claims[0].claim_id],
                "content": "haiku alpha skips the boundary case",
            },
            {
                "channel": "augment",
                "content": "we should also consider failure under load",
            },
            {
                "channel": "converge",
                "target_model": ModelId.SONNET.value,
                "content": "agree with sonnet's overall framing direction",
            },
        ]
    }
    sonnet_p3_input = {
        "turns": [
            {
                "channel": "augment",
                "target_model": ModelId.OPUS.value,
                "content": "opus's correctness frame needs a clarity counterweight",
            },
            {
                "channel": "converge",
                "content": "all three of us are converging on similar tradeoffs",
            },
        ]
    }
    haiku_p3_input = {
        "turns": [
            {
                "channel": "augment",
                "content": "speed considerations should include cache coherency",
            },
        ]
    }

    opus_turns = parse_phase_3_dialogue_tool_use(
        opus_p3_input,
        expected_speaker=ModelId.OPUS,
        valid_claim_ids=all_claim_ids,
        turn_id_prefix="opus_p3_",
    )
    sonnet_turns = parse_phase_3_dialogue_tool_use(
        sonnet_p3_input,
        expected_speaker=ModelId.SONNET,
        valid_claim_ids=all_claim_ids,
        turn_id_prefix="sonnet_p3_",
    )
    haiku_turns = parse_phase_3_dialogue_tool_use(
        haiku_p3_input,
        expected_speaker=ModelId.HAIKU,
        valid_claim_ids=all_claim_ids,
        turn_id_prefix="haiku_p3_",
    )

    all_p3_turns = opus_turns + sonnet_turns + haiku_turns

    # Step 3: build Session.
    session = Session(
        session_id="e2e-phase-3",
        prompt="design a cache",
        prompt_hash="ph",
        models_invited=tuple(folded.keys()),
        phase_1=folded,
        phase_3=all_p3_turns,
    )
    assert len(session.phase_3) == 7

    # Verify cap structure honored at Session level.
    opus_critique_against_sonnet = [
        t
        for t in session.phase_3
        if t.speaker_model is ModelId.OPUS
        and t.channel == "critique"
        and t.target_model is ModelId.SONNET
    ]
    opus_critique_against_haiku = [
        t
        for t in session.phase_3
        if t.speaker_model is ModelId.OPUS
        and t.channel == "critique"
        and t.target_model is ModelId.HAIKU
    ]
    assert len(opus_critique_against_sonnet) == 1
    assert len(opus_critique_against_haiku) == 1

    # Step 4: round-trip through JSON store.
    store = JsonFileSessionStore(tmp_path)
    store.save(session)
    loaded = store.load("e2e-phase-3")
    assert loaded.session_id == session.session_id
    assert loaded.phase_3 == session.phase_3
    assert loaded.phase_1 == session.phase_1
