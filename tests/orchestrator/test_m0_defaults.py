"""M0 checks: attribution of a missing tool block, and the aligned defaults."""

from __future__ import annotations

import pytest
from types import SimpleNamespace

from golden_lattice.memory_graph.base import ModelId
from golden_lattice.orchestrator import DEFAULT_INVITED_MODELS
from golden_lattice.orchestrator import anthropic_client as ac
from golden_lattice.orchestrator.config import LatticeConfig
from golden_lattice.orchestrator.errors import OrchestratorProviderError


def test_missing_tool_block_is_attributed_to_the_calling_model_and_phase():
    response = SimpleNamespace(content=[SimpleNamespace(type="text", text="no tool here")])
    with pytest.raises(OrchestratorProviderError) as info:
        ac._extract_tool_input(response, "record", model=ModelId.HAIKU, phase="phase_1")
    assert info.value.model == ModelId.HAIKU
    assert info.value.phase == "phase_1"


def test_placeholder_seat_is_gone_from_the_error_path():
    response = SimpleNamespace(content=[])
    with pytest.raises(OrchestratorProviderError) as info:
        ac._extract_tool_input(response, "record", model=ModelId.SONNET, phase="phase_3")
    assert info.value.model != ModelId.OPUS
    assert info.value.model == ModelId.SONNET


def test_present_tool_block_still_extracts():
    block = SimpleNamespace(type="tool_use", name="record", input={"x": "ok"})
    response = SimpleNamespace(content=[block])
    assert ac._extract_tool_input(response, "record", model=ModelId.OPUS, phase="phase_1") == {"x": "ok"}


def test_default_timeouts_match_the_pilot_allowance():
    cfg = LatticeConfig()
    assert cfg.timeout_phase_1_seconds == 300.0
    assert cfg.timeout_self_reflection_seconds == 90.0
    assert cfg.timeout_phase_2_seconds == 180.0
    assert cfg.timeout_phase_3_seconds == 240.0


def test_default_peers_are_the_three_live_seats():
    assert set(DEFAULT_INVITED_MODELS) == {ModelId.OPUS, ModelId.SONNET, ModelId.HAIKU}
