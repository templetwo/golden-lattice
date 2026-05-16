"""Tests for the Phase 0 builder + parser (model-facing wire).

These are pure-function tests of the prompt construction and tool-use
parsing that the AnthropicClient delegates to. They do not require the
anthropic SDK to be installed; they exercise the wire-layer contract
directly.

A separate integration test exercises AnthropicClient.submit_investigation_proposal
with a mocked SDK client.
"""

from __future__ import annotations

import pytest

from golden_lattice.exchange.phase_0_investigation import (
    build_investigation_proposal_prompt,
    investigation_proposal_tool_schema,
    parse_investigation_proposal_tool_use,
)
from golden_lattice.memory_graph.base import INVESTIGATION_CAP, ModelId
from golden_lattice.memory_graph.phase_0 import InvestigationProposal


# --- Tool schema ---------------------------------------------------------


def test_investigation_proposal_tool_schema_has_required_fields():
    schema = investigation_proposal_tool_schema(max_queries=3)
    assert schema["name"]
    assert "queries" in schema["input_schema"]["properties"]
    assert schema["input_schema"]["required"] == ["queries"]


def test_investigation_proposal_tool_schema_caps_max_items():
    schema = investigation_proposal_tool_schema(max_queries=3)
    assert schema["input_schema"]["properties"]["queries"]["maxItems"] == 3


def test_investigation_proposal_tool_schema_describes_url_vs_search():
    """The model must know URLs route to fetch and others to search — the
    routing is in the orchestrator's SearchClient, but the model needs to
    propose well-formed strings."""
    schema = investigation_proposal_tool_schema(max_queries=3)
    description = (
        schema.get("description", "")
        + " "
        + schema["input_schema"]["properties"]["queries"].get("description", "")
    ).lower()
    assert "url" in description or "fetch" in description
    assert "search" in description


# --- Prompt builder ------------------------------------------------------


def test_build_investigation_proposal_prompt_returns_system_and_user():
    system, user = build_investigation_proposal_prompt(
        model_id=ModelId.OPUS,
        original_prompt="What is the weather in Philadelphia?",
        max_queries=3,
    )
    assert isinstance(system, str)
    assert isinstance(user, str)
    assert system.strip()
    assert user.strip()


def test_build_investigation_proposal_prompt_names_model_id():
    system, _ = build_investigation_proposal_prompt(
        model_id=ModelId.SONNET,
        original_prompt="x",
        max_queries=3,
    )
    assert ModelId.SONNET.value in system or "Sonnet" in system or "sonnet" in system


def test_build_investigation_proposal_prompt_mentions_cap():
    system, _ = build_investigation_proposal_prompt(
        model_id=ModelId.OPUS,
        original_prompt="x",
        max_queries=3,
    )
    assert "3" in system


def test_build_investigation_proposal_prompt_includes_original_prompt():
    _, user = build_investigation_proposal_prompt(
        model_id=ModelId.OPUS,
        original_prompt="Tell me about Project XYZ",
        max_queries=3,
    )
    assert "Project XYZ" in user


def test_build_investigation_proposal_prompt_allows_empty_proposal():
    system, _ = build_investigation_proposal_prompt(
        model_id=ModelId.OPUS,
        original_prompt="x",
        max_queries=3,
    )
    # The system prompt must communicate that proposing nothing is valid.
    assert "empty" in system.lower() or "no investigation" in system.lower() or "zero" in system.lower()


# --- Parser --------------------------------------------------------------


def test_parse_well_formed_tool_use_returns_investigation_proposal():
    tool_input = {"queries": ["search for X", "https://example.com"]}
    p = parse_investigation_proposal_tool_use(tool_input, expected_model=ModelId.OPUS)
    assert isinstance(p, InvestigationProposal)
    assert p.model_id is ModelId.OPUS
    assert p.queries == ("search for X", "https://example.com")


def test_parse_empty_queries_returns_empty_proposal():
    """A model proposing nothing is valid — empty-union path."""
    tool_input = {"queries": []}
    p = parse_investigation_proposal_tool_use(tool_input, expected_model=ModelId.OPUS)
    assert p.queries == ()


def test_parse_over_cap_queries_truncates_at_wire_boundary():
    """If a model returns more queries than the cap, the wire boundary
    truncates before the substrate would refuse. Substrate refusal would
    abort the session; truncation preserves contribution gracefully."""
    tool_input = {"queries": [f"q{i}" for i in range(INVESTIGATION_CAP + 2)]}
    p = parse_investigation_proposal_tool_use(tool_input, expected_model=ModelId.OPUS)
    assert len(p.queries) == INVESTIGATION_CAP


def test_parse_filters_empty_and_whitespace_queries():
    tool_input = {"queries": ["real", "", "   ", "another"]}
    p = parse_investigation_proposal_tool_use(tool_input, expected_model=ModelId.OPUS)
    assert p.queries == ("real", "another")


def test_parse_dedupes_within_single_proposal():
    """Substrate refuses duplicate queries within a single proposal. The
    wire parser dedupes before substrate construction so model output that
    happens to repeat doesn't abort the session."""
    tool_input = {"queries": ["dup", "dup", "unique"]}
    p = parse_investigation_proposal_tool_use(tool_input, expected_model=ModelId.OPUS)
    assert p.queries == ("dup", "unique")


def test_parse_missing_queries_field_raises():
    from golden_lattice.exchange.phase_2_cross_reading import WireParseError

    with pytest.raises(WireParseError):
        parse_investigation_proposal_tool_use({}, expected_model=ModelId.OPUS)


def test_parse_non_list_queries_raises():
    from golden_lattice.exchange.phase_2_cross_reading import WireParseError

    with pytest.raises(WireParseError):
        parse_investigation_proposal_tool_use(
            {"queries": "not-a-list"}, expected_model=ModelId.OPUS
        )


def test_parse_non_string_queries_raises():
    from golden_lattice.exchange.phase_2_cross_reading import WireParseError

    with pytest.raises(WireParseError):
        parse_investigation_proposal_tool_use(
            {"queries": [1, 2, 3]}, expected_model=ModelId.OPUS
        )
