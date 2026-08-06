"""Lifecycle regression for the Golden Lattice experiment adapter.

Root cause under test: constructing one AnthropicClient (AsyncAnthropic) and
calling the sync run_lattice_session wrapper (asyncio.run) once per
longitudinal step binds HTTP transports to closed event loops.

Contract:
- all steps of one longitudinal session share a single running event loop
- steps are driven via run_lattice_session_async (not the sync wrapper)
- the underlying AsyncAnthropic client is closed in that same loop on
  success and on mid-sequence error
- prior-step continuity, structured outputs, error/aborted records, and
  no-fabrication behavior are preserved

No network calls. Mocks only.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from experiments.baselines.golden_lattice import GoldenLatticeSUT
from experiments.baselines.protocol import GroundingMode, RunMode, StepStatus


# ---------------------------------------------------------------------------
# Fixtures / fakes
# ---------------------------------------------------------------------------


class _FakeUnderlyingClient:
    """Stand-in for anthropic.AsyncAnthropic — tracks close + loop identity."""

    def __init__(self) -> None:
        self.close_calls: list[int] = []
        self.is_closed = False

    async def close(self) -> None:
        loop = asyncio.get_running_loop()
        self.close_calls.append(id(loop))
        self.is_closed = True


class _FakeAnthropicClient:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._client = _FakeUnderlyingClient()
        self.init_args = args
        self.init_kwargs = kwargs


class _FakeTavilyClient:
    """Stand-in for TavilySearchClient — tracks close + loop identity."""

    instances: list["_FakeTavilyClient"] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.init_args = args
        self.init_kwargs = kwargs
        self.close_calls: list[int] = []
        self.is_closed = False
        self.__class__.instances.append(self)

    async def aclose(self) -> None:
        self.close_calls.append(id(asyncio.get_running_loop()))
        self.is_closed = True


def _fake_lattice_session(
    *,
    session_id: str,
    output: str,
    commitment_transitions: Optional[list[Any]] = None,
) -> SimpleNamespace:
    phase_4 = SimpleNamespace(output=output)
    return SimpleNamespace(
        session_id=session_id,
        phase_4=phase_4,
        commitment_transitions=commitment_transitions or (),
    )


def _two_step_bundles() -> list[dict[str, Any]]:
    return [
        {
            "step_id": "present_initial_claim",
            "perturbation_id": "claim.initial",
            "rendered_prompt": "STEP1_PROMPT_BODY",
            "sequence_index": 0,
        },
        {
            "step_id": "apply_controlled_challenge",
            "perturbation_id": "challenge.controlled",
            "rendered_prompt": "STEP2_PROMPT_BODY",
            "sequence_index": 1,
        },
    ]


@pytest.fixture
def live_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOLDEN_LATTICE_EXPERIMENT_LIVE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")


def _patch_orchestrator(
    *,
    run_session_async: Any,
    client_cls: Any = _FakeAnthropicClient,
    sync_run: Any | None = None,
):
    """Patch local imports inside GoldenLatticeSUT._run_live."""
    orch = MagicMock()
    orch.AnthropicClient = client_cls
    # Real LatticeConfig is fine; only need construction not to explode.
    from golden_lattice.orchestrator import LatticeConfig

    orch.LatticeConfig = LatticeConfig
    orch.run_lattice_session_async = run_session_async
    if sync_run is not None:
        orch.run_lattice_session = sync_run
    else:
        orch.run_lattice_session = MagicMock(
            side_effect=AssertionError(
                "sync run_lattice_session must not be used by the live adapter"
            )
        )
    return patch.dict("sys.modules", {"golden_lattice.orchestrator": orch}), orch


# ---------------------------------------------------------------------------
# Grounding availability
# ---------------------------------------------------------------------------


def test_tavily_grounded_live_requires_tavily_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOLDEN_LATTICE_EXPERIMENT_LIVE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    availability = GoldenLatticeSUT().availability(
        RunMode.LIVE,
        grounding_mode=GroundingMode.TAVILY,
    )

    assert availability.available is False
    assert "TAVILY_API_KEY" in availability.reason


def test_tavily_grounded_live_requires_both_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOLDEN_LATTICE_EXPERIMENT_LIVE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key-not-real")

    availability = GoldenLatticeSUT().availability(
        RunMode.LIVE,
        grounding_mode=GroundingMode.TAVILY,
    )

    assert availability.available is True
    assert availability.reason == ""


def test_grounded_session_injects_and_closes_tavily_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOLDEN_LATTICE_EXPERIMENT_LIVE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key-not-real")
    _FakeTavilyClient.instances.clear()
    seen: list[dict[str, Any]] = []

    async def fake_run_async(prompt: str, **kwargs: Any) -> SimpleNamespace:
        seen.append(kwargs)
        return _fake_lattice_session(
            session_id=kwargs.get("session_id") or "s",
            output="grounded output",
        )

    import golden_lattice.exchange.tavily_search_client as tavily_module
    import golden_lattice.orchestrator as real_orch

    with patch.object(
        real_orch, "run_lattice_session_async", fake_run_async, create=True
    ), patch.object(real_orch, "AnthropicClient", _FakeAnthropicClient, create=True), patch.object(
        real_orch,
        "run_lattice_session",
        MagicMock(side_effect=AssertionError("sync wrapper must not be called")),
        create=True,
    ), patch.object(tavily_module, "TavilySearchClient", _FakeTavilyClient):
        result = GoldenLatticeSUT().run_session(
            {"id": "task.grounded"},
            mode=RunMode.LIVE,
            grounding_mode=GroundingMode.TAVILY,
            session_id="sess-grounded",
            prompt_bundles=_two_step_bundles(),
        )

    assert result.status == StepStatus.COMPLETED.value
    assert len(seen) == 2
    assert all(item["phase_0_client"] is seen[0]["phase_0_client"] for item in seen)
    assert all(item["search_client"] is seen[0]["search_client"] for item in seen)
    assert _FakeTavilyClient.instances
    tavily = _FakeTavilyClient.instances[0]
    assert tavily.init_kwargs["api_key"] == "test-tavily-key-not-real"
    assert tavily.is_closed is True
    assert tavily.close_calls


def test_ungrounded_session_does_not_inject_phase_0_clients(
    live_env: None,
) -> None:
    seen: list[dict[str, Any]] = []

    async def fake_run_async(prompt: str, **kwargs: Any) -> SimpleNamespace:
        seen.append(kwargs)
        return _fake_lattice_session(
            session_id=kwargs.get("session_id") or "s",
            output="ungrounded output",
        )

    import golden_lattice.orchestrator as real_orch

    with patch.object(
        real_orch, "run_lattice_session_async", fake_run_async, create=True
    ), patch.object(real_orch, "AnthropicClient", _FakeAnthropicClient, create=True), patch.object(
        real_orch,
        "run_lattice_session",
        MagicMock(side_effect=AssertionError("sync wrapper must not be called")),
        create=True,
    ):
        result = GoldenLatticeSUT().run_session(
            {"id": "task.ungrounded"},
            mode=RunMode.LIVE,
            grounding_mode=GroundingMode.NONE,
            session_id="sess-ungrounded",
            prompt_bundles=_two_step_bundles(),
        )

    assert result.status == StepStatus.COMPLETED.value
    assert seen
    assert all(item["phase_0_client"] is None for item in seen)
    assert all(item["search_client"] is None for item in seen)


# ---------------------------------------------------------------------------
# One-loop lifecycle
# ---------------------------------------------------------------------------


def test_longitudinal_steps_share_one_event_loop(live_env: None) -> None:
    """All steps must run under the same asyncio loop (no per-step asyncio.run)."""
    loop_ids: list[int] = []
    call_count = {"n": 0}

    async def fake_run_async(prompt: str, **kwargs: Any) -> SimpleNamespace:
        loop_ids.append(id(asyncio.get_running_loop()))
        call_count["n"] += 1
        sid = kwargs.get("session_id") or f"s{call_count['n']}"
        return _fake_lattice_session(
            session_id=sid,
            output=f"output-{call_count['n']}",
        )

    module_patch, orch = _patch_orchestrator(run_session_async=fake_run_async)
    with module_patch:
        # Also ensure import path resolves: _run_live does
        # `from golden_lattice.orchestrator import ...`
        with patch(
            "golden_lattice.orchestrator.run_lattice_session_async",
            new=fake_run_async,
            create=True,
        ), patch(
            "golden_lattice.orchestrator.AnthropicClient",
            new=_FakeAnthropicClient,
            create=True,
        ):
            # Force import to see our fakes via the real package if present.
            import golden_lattice.orchestrator as real_orch

            with patch.object(
                real_orch, "run_lattice_session_async", fake_run_async, create=True
            ), patch.object(
                real_orch, "AnthropicClient", _FakeAnthropicClient, create=True
            ), patch.object(
                real_orch,
                "run_lattice_session",
                MagicMock(
                    side_effect=AssertionError("sync wrapper must not be called")
                ),
                create=True,
            ):
                sut = GoldenLatticeSUT()
                result = sut.run_session(
                    {"id": "task.lifecycle"},
                    mode=RunMode.LIVE,
                    session_id="sess-loop",
                    prompt_bundles=_two_step_bundles(),
                )

    assert result.status == StepStatus.COMPLETED.value
    assert len(result.steps) == 2
    assert call_count["n"] == 2
    assert len(loop_ids) == 2
    assert loop_ids[0] == loop_ids[1], (
        f"steps ran on different loops: {loop_ids} — adapter must use one "
        "asyncio.run wrapping all longitudinal steps via run_lattice_session_async"
    )


def test_closes_underlying_async_client_on_success(live_env: None) -> None:
    clients: list[_FakeAnthropicClient] = []

    class TrackingClient(_FakeAnthropicClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            clients.append(self)

    async def fake_run_async(prompt: str, **kwargs: Any) -> SimpleNamespace:
        sid = kwargs.get("session_id") or "s"
        return _fake_lattice_session(session_id=sid, output="ok")

    import golden_lattice.orchestrator as real_orch

    with patch.object(
        real_orch, "run_lattice_session_async", fake_run_async, create=True
    ), patch.object(real_orch, "AnthropicClient", TrackingClient, create=True), patch.object(
        real_orch,
        "run_lattice_session",
        MagicMock(side_effect=AssertionError("sync wrapper must not be called")),
        create=True,
    ):
        sut = GoldenLatticeSUT()
        result = sut.run_session(
            {"id": "task.close.ok"},
            mode=RunMode.LIVE,
            session_id="sess-close-ok",
            prompt_bundles=_two_step_bundles(),
        )

    assert result.status == StepStatus.COMPLETED.value
    assert len(clients) == 1
    underlying = clients[0]._client
    assert underlying.is_closed is True
    assert len(underlying.close_calls) == 1
    # Close must have happened inside a running loop (recorded loop id).
    assert underlying.close_calls[0] != 0


def test_closes_underlying_async_client_on_step_error(live_env: None) -> None:
    clients: list[_FakeAnthropicClient] = []

    class TrackingClient(_FakeAnthropicClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            clients.append(self)

    async def fake_run_async(prompt: str, **kwargs: Any) -> SimpleNamespace:
        raise RuntimeError("simulated provider failure")

    import golden_lattice.orchestrator as real_orch

    with patch.object(
        real_orch, "run_lattice_session_async", fake_run_async, create=True
    ), patch.object(real_orch, "AnthropicClient", TrackingClient, create=True), patch.object(
        real_orch,
        "run_lattice_session",
        MagicMock(side_effect=AssertionError("sync wrapper must not be called")),
        create=True,
    ):
        sut = GoldenLatticeSUT()
        result = sut.run_session(
            {"id": "task.close.err"},
            mode=RunMode.LIVE,
            session_id="sess-close-err",
            prompt_bundles=_two_step_bundles(),
        )

    assert result.status == StepStatus.ABORTED.value
    assert result.steps[0].status == StepStatus.ERROR.value
    assert "simulated provider failure" in (result.steps[0].reason or "")
    assert result.steps[1].status == StepStatus.ABORTED.value
    assert result.steps[1].reason == "aborted after prior step error"
    assert len(clients) == 1
    underlying = clients[0]._client
    assert underlying.is_closed is True
    assert len(underlying.close_calls) == 1


def test_close_uses_same_loop_as_steps(live_env: None) -> None:
    clients: list[_FakeAnthropicClient] = []
    step_loop_ids: list[int] = []

    class TrackingClient(_FakeAnthropicClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            clients.append(self)

    async def fake_run_async(prompt: str, **kwargs: Any) -> SimpleNamespace:
        step_loop_ids.append(id(asyncio.get_running_loop()))
        return _fake_lattice_session(
            session_id=kwargs.get("session_id") or "s",
            output="ok",
        )

    import golden_lattice.orchestrator as real_orch

    with patch.object(
        real_orch, "run_lattice_session_async", fake_run_async, create=True
    ), patch.object(real_orch, "AnthropicClient", TrackingClient, create=True), patch.object(
        real_orch,
        "run_lattice_session",
        MagicMock(side_effect=AssertionError("sync wrapper must not be called")),
        create=True,
    ):
        GoldenLatticeSUT().run_session(
            {"id": "task.same-loop-close"},
            mode=RunMode.LIVE,
            session_id="sess-same",
            prompt_bundles=_two_step_bundles(),
        )

    assert step_loop_ids
    assert clients[0]._client.close_calls == [step_loop_ids[0]]


# ---------------------------------------------------------------------------
# Continuity / structured outputs / honesty
# ---------------------------------------------------------------------------


def test_prior_step_continuity_injected_into_later_prompts(live_env: None) -> None:
    seen_prompts: list[str] = []

    async def fake_run_async(prompt: str, **kwargs: Any) -> SimpleNamespace:
        seen_prompts.append(prompt)
        n = len(seen_prompts)
        return _fake_lattice_session(
            session_id=kwargs.get("session_id") or f"s{n}",
            output=f"CANONICAL_OUTPUT_STEP_{n}",
        )

    import golden_lattice.orchestrator as real_orch

    with patch.object(
        real_orch, "run_lattice_session_async", fake_run_async, create=True
    ), patch.object(
        real_orch, "AnthropicClient", _FakeAnthropicClient, create=True
    ), patch.object(
        real_orch,
        "run_lattice_session",
        MagicMock(side_effect=AssertionError("sync wrapper must not be called")),
        create=True,
    ):
        result = GoldenLatticeSUT().run_session(
            {"id": "task.continuity"},
            mode=RunMode.LIVE,
            session_id="sess-cont",
            prompt_bundles=_two_step_bundles(),
        )

    assert len(seen_prompts) == 2
    assert "STEP1_PROMPT_BODY" in seen_prompts[0]
    assert "Prior step outputs" not in seen_prompts[0]
    assert "CANONICAL_OUTPUT_STEP_1" in seen_prompts[1]
    assert "STEP2_PROMPT_BODY" in seen_prompts[1]
    assert "Prior step outputs from this longitudinal session" in seen_prompts[1]
    # Continuity is product output only — never fabricated synthesis text.
    assert result.steps[0].raw_output == "CANONICAL_OUTPUT_STEP_1"
    assert result.steps[1].raw_output == "CANONICAL_OUTPUT_STEP_2"


def test_structured_outputs_and_explicit_transitions_preserved(
    live_env: None,
) -> None:
    transitions = [
        {"claim_id": "c1", "from_state": "proposed", "to_state": "committed"},
        SimpleNamespace(
            model_dump=lambda: {
                "claim_id": "c2",
                "from_state": "committed",
                "to_state": "withdrawn",
            }
        ),
    ]

    async def fake_run_async(prompt: str, **kwargs: Any) -> SimpleNamespace:
        return _fake_lattice_session(
            session_id=kwargs.get("session_id") or "s",
            output="synthesis text from phase_4",
            commitment_transitions=transitions,
        )

    import golden_lattice.orchestrator as real_orch

    with patch.object(
        real_orch, "run_lattice_session_async", fake_run_async, create=True
    ), patch.object(
        real_orch, "AnthropicClient", _FakeAnthropicClient, create=True
    ), patch.object(
        real_orch,
        "run_lattice_session",
        MagicMock(side_effect=AssertionError("sync wrapper must not be called")),
        create=True,
    ):
        result = GoldenLatticeSUT().run_session(
            {"id": "task.structured"},
            mode=RunMode.LIVE,
            session_id="sess-struct",
            prompt_bundles=_two_step_bundles()[:1],
        )

    step = result.steps[0]
    assert step.status == StepStatus.COMPLETED.value
    assert step.raw_output == "synthesis text from phase_4"
    assert step.structured is not None
    assert step.structured.get("lattice_session_id")
    assert step.commitment_transitions is not None
    assert step.commitment_transitions[0]["claim_id"] == "c1"
    assert step.commitment_transitions[1]["claim_id"] == "c2"


def test_missing_phase_4_output_is_honest_not_fabricated(live_env: None) -> None:
    async def fake_run_async(prompt: str, **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            session_id=kwargs.get("session_id") or "empty",
            phase_4=None,
            commitment_transitions=(),
        )

    import golden_lattice.orchestrator as real_orch

    with patch.object(
        real_orch, "run_lattice_session_async", fake_run_async, create=True
    ), patch.object(
        real_orch, "AnthropicClient", _FakeAnthropicClient, create=True
    ), patch.object(
        real_orch,
        "run_lattice_session",
        MagicMock(side_effect=AssertionError("sync wrapper must not be called")),
        create=True,
    ):
        result = GoldenLatticeSUT().run_session(
            {"id": "task.nofake"},
            mode=RunMode.LIVE,
            session_id="sess-nofake",
            prompt_bundles=_two_step_bundles()[:1],
        )

    raw = result.steps[0].raw_output or ""
    assert "without phase_4.output" in raw
    assert "fabricat" not in raw.lower()


def test_metadata_declares_async_entrypoint(live_env: None) -> None:
    async def fake_run_async(prompt: str, **kwargs: Any) -> SimpleNamespace:
        return _fake_lattice_session(
            session_id=kwargs.get("session_id") or "s",
            output="ok",
        )

    import golden_lattice.orchestrator as real_orch

    with patch.object(
        real_orch, "run_lattice_session_async", fake_run_async, create=True
    ), patch.object(
        real_orch, "AnthropicClient", _FakeAnthropicClient, create=True
    ), patch.object(
        real_orch,
        "run_lattice_session",
        MagicMock(side_effect=AssertionError("sync wrapper must not be called")),
        create=True,
    ):
        result = GoldenLatticeSUT().run_session(
            {"id": "task.meta"},
            mode=RunMode.LIVE,
            session_id="sess-meta",
            prompt_bundles=_two_step_bundles()[:1],
        )

    assert "run_lattice_session_async" in result.metadata.get("reuses", "")
    assert result.metadata.get("synthesis") == "canonical_phase_4"
