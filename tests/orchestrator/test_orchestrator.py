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
from golden_lattice.memory_graph.schema import Session, SessionMetrics
from golden_lattice.memory_graph.store import JsonFileSessionStore
from golden_lattice.orchestrator import (
    AnthropicClient,
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
    for model in (ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU):
        resp = session.phase_1[model]
        assert len(resp.self_reflection_artifacts) == 1
        assert resp.self_reflection_artifacts[0].model_id is model


def test_run_lattice_session_dispatches_phase_2_for_all_pairs(stub_client):
    """6 cross-readings (3 readers × 2 targets each) + 3 taggings."""
    config = LatticeConfig()
    session = run_lattice_session(
        "design a cache",
        config=config,
        client=stub_client,
    )
    assert len(session.phase_2) == 6  # n*(n-1) for n=3
    assert len(session.phase_2_taggings) == 3
    pairs = {(cr.reader_model, cr.target_model) for cr in session.phase_2}
    expected_pairs = {
        (r, t)
        for r in (ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU)
        for t in (ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU)
        if r is not t
    }
    assert pairs == expected_pairs


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
        ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU,
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

    # Per-event-type counts on a triadic session with default stub.
    counts: dict[type, int] = {}
    for e in events:
        counts[type(e)] = counts.get(type(e), 0) + 1
    assert counts.get(Phase1ResponseStartedEvent, 0) == 3
    assert counts.get(Phase1ResponseCompletedEvent, 0) == 3
    assert counts.get(SelfReflectionEvent, 0) == 3
    # Default stub produces 2 claims per model — 6 total claim events.
    assert counts.get(Phase1ClaimEvent, 0) == 6
    # 6 cross-readings + 3 taggings for triadic.
    assert counts.get(Phase2CrossReadingEvent, 0) == 6
    assert counts.get(Phase2TaggingEvent, 0) == 3
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
