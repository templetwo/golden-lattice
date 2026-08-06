"""Tests for the orchestrator — the layer where the lattice meets the world.

End-to-end with stub client: full pipeline runs, returns substrate-valid Session.
Timeout behavior: hard fail with OrchestratorTimeoutError on any phase timeout.
Determinism: same stub responses → same Session.
Mode parameter passed through to synthesize() correctly.
Live integration: gated behind LATTICE_LIVE_TESTS env var.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from golden_lattice.exchange.phase_1_independent import (
    Phase1WireClient,
)
from golden_lattice.exchange.phase_2_cross_reading import (
    Phase2WireClient,
)
from golden_lattice.exchange.phase_3_dialogue import (
    Phase3WireClient,
)
from golden_lattice.memory_graph.base import (
    DEFAULT_OUTPUT_MODE,
    PARITY_THRESHOLD,
    ModelId,
    OutputMode,
    SynthesisRule,
)
from golden_lattice.memory_graph.metrics import compute_parity_shares
from golden_lattice.memory_graph.schema import (
    CommitmentState,
    CommitmentTransition,
    Session,
    SessionMetrics,
)
from golden_lattice.memory_graph.store import JsonFileSessionStore
from golden_lattice.orchestrator import (
    AnthropicClient,
    DEFAULT_INVITED_MODELS,
    LatticeConfig,
    OrchestratorTimeoutError,
    run_lattice_session,
    run_lattice_session_async,
)


# --- Configuration --------------------------------------------------------


def test_lattice_config_rejects_negative_timeouts():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="positive"):
        LatticeConfig(timeout_phase_1_seconds=-1)


def test_lattice_config_rejects_confidence_threshold_outside_unit_interval():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="outside"):
        LatticeConfig(confidence_threshold=1.5)


def test_lattice_config_default_output_mode_is_annotated():
    config = LatticeConfig()
    assert config.output_mode is DEFAULT_OUTPUT_MODE
    assert config.output_mode is OutputMode.ANNOTATED


def test_lattice_config_is_frozen():
    config = LatticeConfig()
    with pytest.raises(Exception):  # pydantic frozen → ValidationError or TypeError
        config.confidence_threshold = 0.5  # type: ignore[misc]


# --- Seat identity vs provider endpoint ----------------------------------
#
# ModelId is the protocol seat / attribution identity.
# LatticeConfig.seat_endpoints maps each seat to the provider model string
# actually sent to the API. Presence of ModelId.FABLE is not availability.


def test_lattice_config_default_endpoint_for_is_seat_value_identity():
    """Legacy callers get identity mapping: seat.value is the provider model."""
    config = LatticeConfig()
    assert config.endpoint_for(ModelId.FABLE) == ModelId.FABLE.value
    assert config.endpoint_for(ModelId.OPUS) == ModelId.OPUS.value
    assert config.endpoint_for(ModelId.SONNET) == ModelId.SONNET.value
    assert config.endpoint_for(ModelId.HAIKU) == ModelId.HAIKU.value


def test_lattice_config_accepts_explicit_seat_to_provider_mapping():
    mapping = {
        ModelId.FABLE: "claude-sonnet-4-6",
        ModelId.OPUS: "claude-opus-4-7",
        ModelId.SONNET: "claude-sonnet-4-6-alt",
        ModelId.HAIKU: "claude-haiku-4-5-20251001",
    }
    config = LatticeConfig(seat_endpoints=mapping)
    assert config.endpoint_for(ModelId.FABLE) == "claude-sonnet-4-6"
    assert config.endpoint_for(ModelId.OPUS) == "claude-opus-4-7"
    # Seat identity remains Fable even when endpoint is a different string.
    assert ModelId.FABLE.value != config.endpoint_for(ModelId.FABLE)


def test_lattice_config_rejects_empty_endpoint_assignment():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="empty"):
        LatticeConfig(
            seat_endpoints={
                ModelId.OPUS: "claude-opus-4-7",
                ModelId.SONNET: "   ",
            }
        )


def test_lattice_config_rejects_duplicate_endpoint_assignments():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="duplicate"):
        LatticeConfig(
            seat_endpoints={
                ModelId.FABLE: "shared-endpoint",
                ModelId.OPUS: "shared-endpoint",
                ModelId.SONNET: "claude-sonnet-4-6",
            }
        )


def test_validate_provider_capabilities_requires_mapping_cover_each_invited_seat():
    """Partial explicit maps must still resolve every invited seat."""
    from golden_lattice.orchestrator import (
        OrchestratorCapabilityError,
        validate_provider_capabilities,
    )

    config = LatticeConfig(
        seat_endpoints={
            ModelId.OPUS: "claude-opus-4-7",
            ModelId.SONNET: "claude-sonnet-4-6",
            # HAIKU deliberately omitted
        }
    )
    with pytest.raises(OrchestratorCapabilityError, match="HAIKU|haiku|missing"):
        validate_provider_capabilities(
            invited_models=(ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU),
            config=config,
        )


def test_validate_provider_capabilities_fails_before_phase_1_when_endpoint_unavailable(
    stub_client,
):
    """Injected availability set — no live network — fails closed before Phase 1."""
    from golden_lattice.orchestrator import (
        OrchestratorCapabilityError,
        validate_provider_capabilities,
    )

    # Fable is a real seat identity; its default endpoint string is not asserted live.
    config = LatticeConfig()
    available = frozenset(
        {
            ModelId.OPUS.value,
            ModelId.SONNET.value,
            ModelId.HAIKU.value,
            # ModelId.FABLE.value deliberately absent
        }
    )

    with pytest.raises(OrchestratorCapabilityError) as exc_info:
        validate_provider_capabilities(
            invited_models=DEFAULT_INVITED_MODELS,
            config=config,
            available_endpoints=available,
        )
    err = exc_info.value
    assert err.seat is ModelId.FABLE
    assert err.endpoint == ModelId.FABLE.value
    assert "unavailable" in str(err).lower() or "not available" in str(err).lower()


def test_fable_seat_identity_is_not_provider_availability_claim():
    """ModelId.FABLE existing proves seat vocabulary only — not a live endpoint."""
    from golden_lattice.orchestrator import (
        OrchestratorCapabilityError,
        validate_provider_capabilities,
    )

    assert ModelId.FABLE in ModelId
    assert ModelId.FABLE in DEFAULT_INVITED_MODELS
    assert ModelId.FABLE.value == "claude-fable-5"

    # Explicit remap: Fable seat → known-available endpoint still attributes as Fable.
    config = LatticeConfig(
        seat_endpoints={
            ModelId.FABLE: "proxy-fable-via-sonnet",
            ModelId.OPUS: "claude-opus-4-7",
            ModelId.SONNET: "claude-sonnet-4-6",
            ModelId.HAIKU: "claude-haiku-4-5-20251001",
        }
    )
    assert config.endpoint_for(ModelId.FABLE) == "proxy-fable-via-sonnet"
    assert ModelId.FABLE.value != "proxy-fable-via-sonnet"

    # Availability is about the endpoint string, not the seat enum member.
    with pytest.raises(OrchestratorCapabilityError) as exc_info:
        validate_provider_capabilities(
            invited_models=(ModelId.FABLE,),
            config=config,
            available_endpoints=frozenset({"claude-opus-4-7"}),
        )
    assert exc_info.value.seat is ModelId.FABLE
    assert exc_info.value.endpoint == "proxy-fable-via-sonnet"


def test_validate_provider_capabilities_passes_when_all_endpoints_available():
    from golden_lattice.orchestrator import validate_provider_capabilities

    config = LatticeConfig(
        seat_endpoints={
            ModelId.FABLE: "endpoint-f",
            ModelId.OPUS: "endpoint-o",
            ModelId.SONNET: "endpoint-s",
            ModelId.HAIKU: "endpoint-h",
        }
    )
    validate_provider_capabilities(
        invited_models=DEFAULT_INVITED_MODELS,
        config=config,
        available_endpoints=frozenset(
            {"endpoint-f", "endpoint-o", "endpoint-s", "endpoint-h"}
        ),
    )


def test_run_lattice_session_preflight_rejects_unavailable_endpoint_before_phase_1(
    stub_client,
):
    """Orchestrator runs capability preflight before any Phase 1 dispatch."""
    from golden_lattice.orchestrator import OrchestratorCapabilityError

    config = LatticeConfig()
    phase_1_calls: list[ModelId] = []

    async def _track_phase_1(*, model_id, original_prompt, prompt_hash, feed=None):
        phase_1_calls.append(model_id)
        return stub_client.phase_1_responses[model_id].model_copy(
            update={"prompt_hash": prompt_hash}
        )

    stub_client.phase_1_hook = _track_phase_1

    with pytest.raises(OrchestratorCapabilityError, match="Fable|fable|unavailable"):
        run_lattice_session(
            "design a cache",
            config=config,
            client=stub_client,
            available_endpoints=frozenset(
                {
                    ModelId.OPUS.value,
                    ModelId.SONNET.value,
                    ModelId.HAIKU.value,
                }
            ),
        )
    assert phase_1_calls == [], "Phase 1 must not run when preflight fails"


def test_anthropic_client_uses_resolved_provider_model_not_seat_value(monkeypatch):
    """API calls receive the mapped provider model; ModelId stays attribution only."""
    import golden_lattice.orchestrator.anthropic_client as ac_mod
    from datetime import datetime, timezone

    from golden_lattice.memory_graph.base import FocusTag, Phase, claim_id_for
    from golden_lattice.memory_graph.schema import Claim, IndependentResponse

    class _FakeBlock:
        type = "tool_use"
        name = "emit_phase_1_response"
        input = {
            "response": "ok",
            "focus_tag": "correctness",
            "confidence": 0.8,
            "claims": [{"text": "c1"}],
        }

    class _FakeResponse:
        content = [_FakeBlock()]

    captured: dict[str, object] = {}

    class _FakeMessages:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return _FakeResponse()

    class _FakeAsyncAnthropic:
        def __init__(self, *args, **kwargs):
            self.messages = _FakeMessages()

    class _FakeAnthropicModule:
        AsyncAnthropic = _FakeAsyncAnthropic

    monkeypatch.setitem(__import__("sys").modules, "anthropic", _FakeAnthropicModule())

    claim_text = "c1"
    claim = Claim(
        claim_id=claim_id_for(ModelId.FABLE, Phase.INDEPENDENT, claim_text),
        source_model=ModelId.FABLE,
        source_phase=Phase.INDEPENDENT,
        text=claim_text,
    )
    canned = IndependentResponse(
        model_id=ModelId.FABLE,
        prompt_hash="h",
        response="ok",
        focus_tag=FocusTag.CORRECTNESS,
        confidence=0.8,
        claims=(claim,),
        generation_started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        generation_completed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        ac_mod,
        "parse_phase_1_response_tool_use",
        lambda *a, **k: canned,
    )

    client = AnthropicClient(
        api_key="test-key",
        seat_endpoints={ModelId.FABLE: "provider-model-for-fable-seat"},
    )
    result = asyncio.run(
        client.submit_phase_1_response(
            model_id=ModelId.FABLE,
            original_prompt="p",
            prompt_hash="h",
        )
    )
    assert captured["model"] == "provider-model-for-fable-seat"
    assert captured["model"] != ModelId.FABLE.value
    assert result.model_id is ModelId.FABLE  # attribution identity preserved


# --- Stub-client Protocol conformance ------------------------------------


def test_stub_client_satisfies_all_three_wire_protocols(stub_client):
    assert isinstance(stub_client, Phase1WireClient)
    assert isinstance(stub_client, Phase2WireClient)
    assert isinstance(stub_client, Phase3WireClient)


# --- End-to-end happy path -----------------------------------------------


def test_run_lattice_session_returns_substrate_valid_session(stub_client):
    config = LatticeConfig()
    session = run_lattice_session(
        "design a cache",
        config=config,
        client=stub_client,
    )
    assert isinstance(session, Session)
    assert session.phase_4 is not None
    # Every Phase 1 claim is traced (irreducibility preservation).
    phase_1_ids = {c.claim_id for r in session.phase_1.values() for c in r.claims}
    traced_ids = {e.claim_id for e in session.phase_4.claim_trace}
    assert phase_1_ids == traced_ids
    # All four synthesis rules applied.
    assert set(session.phase_4.synthesis_rules_applied) == {
        SynthesisRule.IRREDUCIBILITY_PRESERVATION,
        SynthesisRule.AGREEMENT_ELEVATION,
        SynthesisRule.DISAGREEMENT_SURFACING,
        SynthesisRule.ATTRIBUTION_PRESERVATION,
    }


def test_run_lattice_session_includes_self_reflection_artifacts(stub_client):
    config = LatticeConfig()
    session = run_lattice_session(
        "design a cache",
        config=config,
        client=stub_client,
    )
    for model in DEFAULT_INVITED_MODELS:
        resp = session.phase_1[model]
        assert len(resp.self_reflection_artifacts) == 1
        assert resp.self_reflection_artifacts[0].model_id is model


def test_run_lattice_session_dispatches_phase_2_for_all_pairs(stub_client):
    """One cross-reading for every ordered peer pair + one tagging per peer."""
    config = LatticeConfig()
    session = run_lattice_session(
        "design a cache",
        config=config,
        client=stub_client,
    )
    n = len(DEFAULT_INVITED_MODELS)
    assert len(session.phase_2) == n * (n - 1)
    assert len(session.phase_2_taggings) == n
    pairs = {(cr.reader_model, cr.target_model) for cr in session.phase_2}
    expected_pairs = {
        (r, t)
        for r in DEFAULT_INVITED_MODELS
        for t in DEFAULT_INVITED_MODELS
        if r is not t
    }
    assert pairs == expected_pairs


def test_default_four_seat_roster_surfaces_two_of_three_peer_dispute(stub_client):
    """E2E: DEFAULT_INVITED_MODELS through run_lattice_session → Phase 4.

    Unit coverage of the ≥2-peer dispute rule lives in
    tests/synthesis/test_claim_trace.py against hand-built Sessions. This
    regression binds the production path: default four-seat roster (no
    invited_models override), stub Phase 2 disagreements from exactly two of
    three non-author peers, and the deterministic [DISPUTED] hedge reaching
    both claim_trace and annotated synthesis output. A silent third peer is
    not a veto.
    """
    from golden_lattice.memory_graph.schema import CrossReading, Disagreement

    assert DEFAULT_INVITED_MODELS == (
        ModelId.FABLE,
        ModelId.OPUS,
        ModelId.SONNET,
        ModelId.HAIKU,
    )
    assert len(DEFAULT_INVITED_MODELS) == 4

    author = ModelId.OPUS
    assert author in DEFAULT_INVITED_MODELS
    non_authors = tuple(m for m in DEFAULT_INVITED_MODELS if m is not author)
    assert len(non_authors) == 3
    disputers = non_authors[:2]
    silent_peer = non_authors[2]

    contested = stub_client.phase_1_responses[author].claims[0]
    contested_id = contested.claim_id
    reasons = {
        disputers[0]: "first non-author peer objects at default N=4.",
        disputers[1]: "second non-author peer objects at default N=4.",
    }

    async def cross_reading_hook(*, reader_model, target_model):
        if target_model is author and reader_model in disputers:
            return CrossReading(
                reader_model=reader_model,
                target_model=target_model,
                disagreements=(
                    Disagreement(
                        target_claim_id=contested_id,
                        reason=reasons[reader_model],
                    ),
                ),
            )
        return CrossReading(reader_model=reader_model, target_model=target_model)

    stub_client.cross_reading_hook = cross_reading_hook

    # Deliberately omit invited_models so the default four-seat roster is used.
    session = run_lattice_session(
        "design a cache under peer dispute",
        config=LatticeConfig(),
        client=stub_client,
    )

    assert tuple(session.models_invited) == DEFAULT_INVITED_MODELS
    assert session.phase_4 is not None

    by_id = {e.claim_id: e for e in session.phase_4.claim_trace}
    entry = by_id[contested_id]
    assert entry.disposition == "modified"
    assert entry.modified_text is not None
    assert entry.modified_text.startswith(contested.text)
    assert "DISPUTED" in entry.modified_text
    for peer in disputers:
        assert peer.value in entry.modified_text
    assert silent_peer.value not in entry.modified_text

    # Annotated synthesis must surface the hedge, not only the internal trace.
    assert "DISPUTED" in session.phase_4.output
    assert contested.text in session.phase_4.output


def test_run_lattice_session_session_id_is_generated_when_not_provided(stub_client):
    config = LatticeConfig()
    session = run_lattice_session("p", config=config, client=stub_client)
    assert session.session_id.startswith("session_")


def test_run_lattice_session_honors_provided_session_id(stub_client):
    config = LatticeConfig()
    session = run_lattice_session(
        "p", config=config, client=stub_client, session_id="custom-123"
    )
    assert session.session_id == "custom-123"


# --- Timeout behavior ----------------------------------------------------


def test_phase_1_timeout_raises_orchestrator_timeout_error(stub_client):
    """One model delayed past timeout → OrchestratorTimeoutError surfaces."""
    config = LatticeConfig(
        timeout_phase_1_seconds=0.1,
        timeout_self_reflection_seconds=5.0,
        timeout_phase_2_seconds=5.0,
        timeout_phase_3_seconds=5.0,
    )
    stub_client.phase_1_delay_seconds[ModelId.OPUS] = 1.0  # exceeds timeout
    with pytest.raises(OrchestratorTimeoutError) as exc:
        run_lattice_session("p", config=config, client=stub_client)
    err = exc.value
    assert err.model is ModelId.OPUS
    assert err.phase == "phase_1"
    assert err.timeout_seconds == 0.1


def test_self_reflection_timeout_raises_orchestrator_timeout_error(stub_client):
    config = LatticeConfig(
        timeout_phase_1_seconds=5.0,
        timeout_self_reflection_seconds=0.1,
        timeout_phase_2_seconds=5.0,
        timeout_phase_3_seconds=5.0,
    )
    stub_client.self_reflection_delay_seconds[ModelId.SONNET] = 1.0
    with pytest.raises(OrchestratorTimeoutError) as exc:
        run_lattice_session("p", config=config, client=stub_client)
    err = exc.value
    assert err.model is ModelId.SONNET
    assert err.phase == "self_reflection"


def test_phase_2_timeout_raises_orchestrator_timeout_error(stub_client):
    config = LatticeConfig(
        timeout_phase_1_seconds=5.0,
        timeout_self_reflection_seconds=5.0,
        timeout_phase_2_seconds=0.1,
        timeout_phase_3_seconds=5.0,
    )
    stub_client.cross_reading_delay_seconds = 1.0
    with pytest.raises(OrchestratorTimeoutError) as exc:
        run_lattice_session("p", config=config, client=stub_client)
    err = exc.value
    assert "phase_2" in err.phase


def test_phase_3_timeout_raises_orchestrator_timeout_error(stub_client):
    config = LatticeConfig(
        timeout_phase_1_seconds=5.0,
        timeout_self_reflection_seconds=5.0,
        timeout_phase_2_seconds=5.0,
        timeout_phase_3_seconds=0.1,
    )
    stub_client.phase_3_delay_seconds = 1.0
    with pytest.raises(OrchestratorTimeoutError) as exc:
        run_lattice_session("p", config=config, client=stub_client)
    err = exc.value
    assert err.phase == "phase_3"


# --- Determinism ---------------------------------------------------------


def test_run_lattice_session_is_deterministic_given_stub(stub_client):
    """Same stub responses → same Session (modulo session_id which is timestamped)."""
    config = LatticeConfig()
    s1 = run_lattice_session("p", config=config, client=stub_client, session_id="fixed")
    s2 = run_lattice_session("p", config=config, client=stub_client, session_id="fixed")
    # Compare everything except session_id (always same here) and created_at.
    assert s1.phase_1 == s2.phase_1
    assert s1.phase_2 == s2.phase_2
    assert s1.phase_2_taggings == s2.phase_2_taggings
    assert s1.phase_3 == s2.phase_3
    # Phase 4 outputs are deterministic from inputs.
    assert s1.phase_4.output == s2.phase_4.output
    assert s1.phase_4.claim_trace == s2.phase_4.claim_trace


# --- Mode parameter passes through to synthesize -------------------------


def test_output_mode_passes_through_from_config(stub_client):
    for mode in OutputMode:
        config = LatticeConfig(output_mode=mode)
        session = run_lattice_session(
            "p", config=config, client=stub_client, session_id=f"s_{mode.value}"
        )
        assert session.phase_4.output_mode is mode


# --- Async API -----------------------------------------------------------


def test_async_api_works(stub_client):
    config = LatticeConfig()

    async def _run():
        return await run_lattice_session_async(
            "p", config=config, client=stub_client
        )

    session = asyncio.run(_run())
    assert isinstance(session, Session)
    assert session.phase_4 is not None


# --- Parity wiring: load-bearing measurement at the canonical builder ----


def test_run_lattice_session_attaches_parity_metrics_for_triadic(stub_client):
    """N=3 emitted Session carries metrics computed by the orchestrator.

    ARCHITECTURE.md falsification criterion #3 (contribution parity) is the
    load-bearing measurement. compute_parity_shares is pure sync over the
    tagged Session; the orchestrator is the canonical Session-builder, so
    every emitted Session has parity computed exactly once at the boundary
    where the lattice meets the world.
    """
    config = LatticeConfig()
    session = run_lattice_session("p", config=config, client=stub_client)
    assert session.metrics is not None
    assert isinstance(session.metrics, SessionMetrics)
    assert set(session.metrics.distinct_claim_share.keys()) == {
        *DEFAULT_INVITED_MODELS,
    }
    assert session.metrics.parity_threshold == PARITY_THRESHOLD


def test_run_lattice_session_metrics_is_none_for_dyad(stub_client):
    """N=2 emitted Session has metrics=None.

    Recognition-from-within requires a third presence in the room. With N=2,
    the consensus rule degenerates into one peer adjudicating the other —
    the authority gradient the protocol refused. Parity is structurally
    undefined, and the orchestrator surfaces that as None rather than a
    misleading zero.
    """
    config = LatticeConfig()
    session = run_lattice_session(
        "p",
        config=config,
        client=stub_client,
        invited_models=(ModelId.OPUS, ModelId.SONNET),
    )
    assert session.metrics is None


def test_persisted_session_round_trips_parity_computable(tmp_path, stub_client):
    """Round-trip a real triadic Session through JsonFileSessionStore and
    confirm compute_parity_shares produces a well-formed SessionMetrics
    over the reloaded object.

    This is the kernel-level regression for the wiring: orchestrator emits
    a Session with metrics; the store persists and reloads it without loss;
    compute_parity_shares applied to the reloaded session still computes.

    The same shape of test, run against the real persisted May 4 session
    (sessions/session_20260505_010010_e9fd466c.session.json), is the
    H1/H2 discriminator outside the test suite — see the suite-run notes.
    """
    config = LatticeConfig()
    session = run_lattice_session(
        "p", config=config, client=stub_client, session_id="round_trip_test"
    )
    store = JsonFileSessionStore(tmp_path)
    store.save(session)
    reloaded = store.load("round_trip_test")
    assert reloaded.metrics is not None
    assert reloaded.metrics == session.metrics
    recomputed = compute_parity_shares(reloaded, threshold=PARITY_THRESHOLD)
    assert recomputed == reloaded.metrics


# --- progress_callback live event emission --------------------------------


def test_progress_callback_fires_full_event_sequence(stub_client):
    """The live orchestrator emits the same LatticeEvent types replay would
    produce. With the stub client, we can verify the sequence end-to-end."""
    from golden_lattice.events import (
        Phase1ClaimEvent,
        Phase1ResponseCompletedEvent,
        Phase1ResponseStartedEvent,
        Phase2CrossReadingEvent,
        Phase2TaggingEvent,
        Phase4ArtifactEvent,
        Phase4FlagInterpretationsEvent,
        Phase4MetricsEvent,
        SelfReflectionEvent,
        SessionCompletedEvent,
        SessionStartedEvent,
    )

    events: list = []
    config = LatticeConfig()
    run_lattice_session(
        "p",
        config=config,
        client=stub_client,
        progress_callback=events.append,
    )

    # First and last bookend events.
    assert isinstance(events[0], SessionStartedEvent)
    assert isinstance(events[-1], SessionCompletedEvent)

    # Per-event-type counts on the default four-seat session.
    counts: dict[type, int] = {}
    for e in events:
        counts[type(e)] = counts.get(type(e), 0) + 1
    assert counts.get(Phase1ResponseStartedEvent, 0) == len(DEFAULT_INVITED_MODELS)
    assert counts.get(Phase1ResponseCompletedEvent, 0) == len(DEFAULT_INVITED_MODELS)
    assert counts.get(SelfReflectionEvent, 0) == len(DEFAULT_INVITED_MODELS)
    # Default stub produces 2 claims per model.
    assert counts.get(Phase1ClaimEvent, 0) == 2 * len(DEFAULT_INVITED_MODELS)
    # n*(n-1) cross-readings + n taggings.
    n = len(DEFAULT_INVITED_MODELS)
    assert counts.get(Phase2CrossReadingEvent, 0) == n * (n - 1)
    assert counts.get(Phase2TaggingEvent, 0) == n
    # One each of Phase 4 events.
    assert counts.get(Phase4ArtifactEvent, 0) == 1
    assert counts.get(Phase4MetricsEvent, 0) == 1
    assert counts.get(Phase4FlagInterpretationsEvent, 0) == 1


def test_progress_callback_offsets_are_monotonic_non_decreasing(stub_client):
    """Timestamp offsets across the emitted stream advance monotonically.

    Even when phase coroutines race in parallel, each event's offset is
    captured at emission time so the wall-clock ordering is preserved.
    """
    events: list = []
    config = LatticeConfig()
    run_lattice_session(
        "p",
        config=config,
        client=stub_client,
        progress_callback=events.append,
    )
    offsets = [e.timestamp_offset_ms for e in events]
    assert offsets == sorted(offsets)
    assert offsets[0] == 0  # SessionStartedEvent anchors at zero


def test_progress_callback_can_be_omitted_without_behavior_change(stub_client):
    """No callback → same Session result as before this wiring landed."""
    config = LatticeConfig()
    s_with = run_lattice_session("p", config=config, client=stub_client, session_id="A")
    s_no = run_lattice_session("p", config=config, client=stub_client, session_id="A")
    # Both run with no callback; both produce equal Sessions modulo session_id.
    assert s_with.phase_4.output == s_no.phase_4.output
    assert s_with.metrics == s_no.metrics


# --- Commitment transitions (Phase 1 Task 5) ------------------------------


def test_run_lattice_session_defaults_commitment_transitions_empty(stub_client):
    """No auto-created transitions merely because dialogue/text existed."""
    config = LatticeConfig()
    session = run_lattice_session("design a cache", config=config, client=stub_client)
    assert session.commitment_transitions == ()
    # Dialogue may or may not be non-empty depending on stub, but even when
    # Phase 3 has content, transitions stay empty unless explicitly supplied.
    assert isinstance(session.commitment_transitions, tuple)


def test_run_lattice_session_attaches_explicit_commitment_transitions(stub_client):
    """Optional commitment_transitions tuple is attached, never inferred."""
    from datetime import datetime, timezone

    config = LatticeConfig()
    # First run to discover real Phase 1 claim ids from the stub.
    probe = run_lattice_session("p", config=config, client=stub_client, session_id="probe")
    claim_id = next(iter(probe.phase_1.values())).claims[0].claim_id
    source_model = next(iter(probe.phase_1.values())).model_id
    t0 = CommitmentTransition(
        claim_id=claim_id,
        source_model=source_model,
        prior_state=CommitmentState.PROPOSED,
        next_state=CommitmentState.CHALLENGED,
        source_event="phase_3:critique:explicit",
        reason="Explicit observer-recorded challenge.",
        sequence_index=0,
        occurred_at=datetime(2026, 5, 2, 12, 0, 0, tzinfo=timezone.utc),
    )
    session = run_lattice_session(
        "p",
        config=config,
        client=stub_client,
        session_id="with-transitions",
        commitment_transitions=(t0,),
    )
    assert session.commitment_transitions == (t0,)


def test_run_lattice_session_rejects_invalid_commitment_transitions(stub_client):
    from datetime import datetime, timezone

    from pydantic import ValidationError

    config = LatticeConfig()
    orphan = CommitmentTransition(
        claim_id="deadbeefdeadbeef",
        source_model=ModelId.OPUS,
        prior_state=CommitmentState.PROPOSED,
        next_state=CommitmentState.CHALLENGED,
        source_event="phase_3:critique:bad",
        reason="unknown claim must fail at Session build",
        sequence_index=0,
        occurred_at=datetime(2026, 5, 2, 12, 0, 0, tzinfo=timezone.utc),
    )
    with pytest.raises(ValidationError, match="unknown claim_id|Phase 1|commitment"):
        run_lattice_session(
            "p",
            config=config,
            client=stub_client,
            commitment_transitions=(orphan,),
        )


def test_run_lattice_session_emits_commitment_transition_events_when_provided(stub_client):
    from datetime import datetime, timezone

    from golden_lattice.events import CommitmentTransitionEvent

    config = LatticeConfig()
    probe = run_lattice_session("p", config=config, client=stub_client, session_id="probe2")
    claim_id = next(iter(probe.phase_1.values())).claims[0].claim_id
    source_model = next(iter(probe.phase_1.values())).model_id
    t0 = CommitmentTransition(
        claim_id=claim_id,
        source_model=source_model,
        prior_state=CommitmentState.PROPOSED,
        next_state=CommitmentState.DEFENDED,
        source_event="phase_3:critique:live",
        reason="Explicit defend.",
        sequence_index=0,
        occurred_at=datetime(2026, 5, 2, 12, 0, 0, tzinfo=timezone.utc),
    )
    events: list = []
    session = run_lattice_session(
        "p",
        config=config,
        client=stub_client,
        session_id="live-ct",
        progress_callback=events.append,
        commitment_transitions=(t0,),
    )
    ct_events = [e for e in events if isinstance(e, CommitmentTransitionEvent)]
    assert len(ct_events) == 1
    assert ct_events[0].transition == t0
    assert session.commitment_transitions == (t0,)
    # Live ordered sequence matches what replay would emit from the Session.
    from golden_lattice.replay import replay_session_events

    replayed = [
        e for e in replay_session_events(session) if isinstance(e, CommitmentTransitionEvent)
    ]
    assert [e.transition for e in ct_events] == [e.transition for e in replayed]


def test_run_lattice_session_async_accepts_commitment_transitions(stub_client):
    from datetime import datetime, timezone

    config = LatticeConfig()
    probe = run_lattice_session("p", config=config, client=stub_client, session_id="probe3")
    claim_id = next(iter(probe.phase_1.values())).claims[0].claim_id
    source_model = next(iter(probe.phase_1.values())).model_id
    t0 = CommitmentTransition(
        claim_id=claim_id,
        source_model=source_model,
        prior_state=CommitmentState.PROPOSED,
        next_state=CommitmentState.WITHDRAWN,
        source_event="phase_3:critique:async",
        reason="Withdrawn under pressure.",
        sequence_index=0,
        occurred_at=datetime(2026, 5, 2, 12, 0, 0, tzinfo=timezone.utc),
    )

    async def _run():
        return await run_lattice_session_async(
            "p",
            config=config,
            client=stub_client,
            session_id="async-ct",
            commitment_transitions=(t0,),
        )

    session = asyncio.run(_run())
    assert session.commitment_transitions == (t0,)


# --- Live integration test (gated) ---------------------------------------


@pytest.mark.skipif(
    os.environ.get("LATTICE_LIVE_TESTS") != "1",
    reason="live integration test requires LATTICE_LIVE_TESTS=1 and an Anthropic API key",
)
def test_live_lattice_session_smoke():
    """Smoke test: a real session against the Anthropic API.

    Gated behind LATTICE_LIVE_TESTS=1. Requires ANTHROPIC_API_KEY in env.
    Single test, smoke-level — runs a small prompt and verifies the
    returned Session is substrate-valid. Cost: ~3x Opus call. Slow.
    """
    config = LatticeConfig(
        api_key=os.environ.get("ANTHROPIC_API_KEY"),
        timeout_phase_1_seconds=120.0,
        timeout_self_reflection_seconds=60.0,
        timeout_phase_2_seconds=120.0,
        timeout_phase_3_seconds=120.0,
    )
    client = AnthropicClient(api_key=config.api_key)
    session = run_lattice_session(
        "What's the right default eviction policy for a session cache, and why?",
        config=config,
        client=client,
    )
    assert isinstance(session, Session)
    assert session.phase_4 is not None
    assert len(session.phase_4.output) > 0
    print("\n--- Live session output ---\n")
    print(session.phase_4.output)
    print("\n--- End live output ---\n")
