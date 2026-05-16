"""Tests for the Phase 1 wire format — schemas, prompts, parsers, refusals."""

from datetime import datetime, timezone

import pytest

from golden_lattice.exchange.phase_1_independent import (
    PHASE_1_RESPONSE_TOOL_NAME,
    SELF_REFLECTION_TOOL_NAME,
    Phase1WireClient,
    build_phase_1_response_prompt,
    build_self_reflection_prompt,
    compose_phase_1_with_reflection,
    parse_phase_1_response_tool_use,
    parse_self_reflection_tool_use,
    phase_1_response_tool_schema,
    self_reflection_tool_schema,
)
from golden_lattice.memory_graph.schema import SelfReflectionArtifact
from golden_lattice.exchange.phase_2_cross_reading import WireParseError
from golden_lattice.memory_graph.base import (
    FocusTag,
    ModelId,
    Phase,
    claim_id_for,
)
from golden_lattice.memory_graph.schema import (
    Claim,
    IndependentResponse,
)


NOW = datetime(2026, 5, 3, 12, 0, 0, tzinfo=timezone.utc)


# --- Tool schemas --------------------------------------------------------


def test_phase_1_response_tool_schema_advertises_focus_tag_vocab():
    schema = phase_1_response_tool_schema()
    assert schema["name"] == PHASE_1_RESPONSE_TOOL_NAME
    enum = schema["input_schema"]["properties"]["focus_tag"]["enum"]
    assert set(enum) == {t.value for t in FocusTag}
    assert schema["input_schema"]["additionalProperties"] is False


def test_phase_1_response_tool_schema_pins_confidence_to_unit_interval():
    schema = phase_1_response_tool_schema()
    conf = schema["input_schema"]["properties"]["confidence"]
    assert conf["minimum"] == 0.0
    assert conf["maximum"] == 1.0


def test_self_reflection_tool_schema_required_fields():
    schema = self_reflection_tool_schema()
    assert schema["name"] == SELF_REFLECTION_TOOL_NAME
    required = set(schema["input_schema"]["required"])
    assert required == {"strongest_claim_id", "weakest_claim_id", "tag_justification"}


# --- Prompt builders -----------------------------------------------------


def test_phase_1_response_prompt_names_role_and_tool():
    system, user = build_phase_1_response_prompt(
        model_id=ModelId.OPUS,
        original_prompt="design a cache",
    )
    assert ModelId.OPUS.value in system
    assert PHASE_1_RESPONSE_TOOL_NAME in system
    assert FocusTag.CORRECTNESS.value in system
    assert "design a cache" in user


def test_phase_1_response_prompt_advertises_full_focus_tag_vocabulary():
    """If a FocusTag is added, the prompt must surface it. Mirror of the Phase 2 vocabulary test."""
    system, _ = build_phase_1_response_prompt(
        model_id=ModelId.OPUS,
        original_prompt="p",
    )
    for tag in FocusTag:
        assert tag.value in system, f"focus_tag {tag.value} missing from prompt"


def test_phase_1_response_prompt_uses_peer_utility_test():
    """The peer-utility test sentence is the load-bearing structural criterion for decomposition."""
    system, _ = build_phase_1_response_prompt(
        model_id=ModelId.OPUS,
        original_prompt="p",
    )
    assert "peers couldn't meaningfully tag" in system
    assert "bundles multiple arguments" in system


def test_phase_1_response_prompt_clarifies_confidence_logged_not_weighted():
    """Removes the elicitation incentive to inflate confidence as synthesis-attention competition."""
    system, _ = build_phase_1_response_prompt(
        model_id=ModelId.OPUS,
        original_prompt="p",
    )
    assert "logged for analysis" in system
    assert "does not weight your contribution" in system


# --- Phase 0 feed inclusion (Slice A fix) --------------------------------


def test_phase_1_response_prompt_without_feed_has_no_evidence_section():
    """Backward compat: no feed → no SHARED EVIDENCE section in the user
    prompt. Existing sessions and tests that don't pass a feed see the
    pre-amendment prompt unchanged."""
    _, user = build_phase_1_response_prompt(
        model_id=ModelId.OPUS,
        original_prompt="p",
    )
    assert "SHARED EVIDENCE" not in user
    assert "Phase 0" not in user


def test_phase_1_response_prompt_with_grounding_only_feed_surfaces_datetime():
    from datetime import datetime, timezone
    from golden_lattice.memory_graph.phase_0 import (
        DateTimeGrounding,
        datetime_grounding_id,
    )
    when = datetime(2026, 5, 17, 14, 30, 0, tzinfo=timezone.utc)
    grounding = DateTimeGrounding(
        entry_id=datetime_grounding_id(when, "America/New_York"),
        timestamp=when,
        timezone_name="America/New_York",
        formatted_text="2026-05-17 14:30:00 (America/New_York)",
    )
    _, user = build_phase_1_response_prompt(
        model_id=ModelId.OPUS,
        original_prompt="p",
        feed=(grounding,),
    )
    assert "SHARED EVIDENCE" in user
    assert "2026-05-17 14:30:00" in user
    assert "America/New_York" in user


def test_phase_1_response_prompt_includes_search_result_content():
    from datetime import datetime, timezone
    from golden_lattice.memory_graph.phase_0 import (
        DateTimeGrounding,
        SearchResult,
        datetime_grounding_id,
        search_result_id,
    )
    when = datetime(2026, 5, 17, 14, 30, 0, tzinfo=timezone.utc)
    grounding = DateTimeGrounding(
        entry_id=datetime_grounding_id(when, "America/New_York"),
        timestamp=when,
        timezone_name="America/New_York",
        formatted_text="2026-05-17 14:30:00 (America/New_York)",
    )
    result = SearchResult(
        entry_id=search_result_id("https://github.com/templetwo", when),
        query="https://github.com/templetwo",
        result_text="Temple of Two — Anthony's research repo. Contains golden-lattice and related projects.",
        source_urls=("https://github.com/templetwo",),
        executed_at=when,
    )
    _, user = build_phase_1_response_prompt(
        model_id=ModelId.OPUS,
        original_prompt="evaluate this work",
        feed=(grounding, result),
    )
    assert "https://github.com/templetwo" in user
    assert "Temple of Two" in user
    assert "Anthony's research repo" in user


def test_phase_1_response_prompt_includes_failed_search_with_reason():
    from datetime import datetime, timezone
    from golden_lattice.memory_graph.phase_0 import (
        DateTimeGrounding,
        FailedSearch,
        datetime_grounding_id,
        failed_search_id,
    )
    when = datetime(2026, 5, 17, 14, 30, 0, tzinfo=timezone.utc)
    grounding = DateTimeGrounding(
        entry_id=datetime_grounding_id(when, "America/New_York"),
        timestamp=when,
        timezone_name="America/New_York",
        formatted_text="2026-05-17 14:30:00 (America/New_York)",
    )
    failed = FailedSearch(
        entry_id=failed_search_id("https://unreachable.example", when),
        query="https://unreachable.example",
        reason="connection timeout",
        attempted_at=when,
    )
    _, user = build_phase_1_response_prompt(
        model_id=ModelId.OPUS,
        original_prompt="p",
        feed=(grounding, failed),
    )
    assert "https://unreachable.example" in user
    assert "connection timeout" in user
    # Failed evidence is itself shared evidence — must be marked, not buried.
    assert "fail" in user.lower() or "could not" in user.lower() or "did not" in user.lower()


def test_phase_1_response_prompt_renders_feed_in_order():
    """Feed entries appear in the prompt in their feed-order (grounding
    first, then search entries in arrival order)."""
    from datetime import datetime, timedelta, timezone
    from golden_lattice.memory_graph.phase_0 import (
        DateTimeGrounding,
        SearchResult,
        datetime_grounding_id,
        search_result_id,
    )
    when = datetime(2026, 5, 17, 14, 30, 0, tzinfo=timezone.utc)
    grounding = DateTimeGrounding(
        entry_id=datetime_grounding_id(when, "America/New_York"),
        timestamp=when,
        timezone_name="America/New_York",
        formatted_text="2026-05-17 (now)",
    )
    r1 = SearchResult(
        entry_id=search_result_id("alpha-query", when),
        query="alpha-query",
        result_text="ALPHA_RESULT_TEXT",
        executed_at=when,
    )
    r2 = SearchResult(
        entry_id=search_result_id("beta-query", when + timedelta(seconds=1)),
        query="beta-query",
        result_text="BETA_RESULT_TEXT",
        executed_at=when + timedelta(seconds=1),
    )
    _, user = build_phase_1_response_prompt(
        model_id=ModelId.OPUS,
        original_prompt="p",
        feed=(grounding, r1, r2),
    )
    alpha_idx = user.index("ALPHA_RESULT_TEXT")
    beta_idx = user.index("BETA_RESULT_TEXT")
    assert alpha_idx < beta_idx


def test_self_reflection_prompt_anchors_honest_self_read():
    """The honest-self-read sentence flips Phase 2 from criterion to temporal context."""
    response = IndependentResponse(
        model_id=ModelId.OPUS,
        prompt_hash="h",
        response="r",
        focus_tag=FocusTag.CORRECTNESS,
        confidence=0.5,
        claims=(
            Claim(
                claim_id=claim_id_for(ModelId.OPUS, Phase.INDEPENDENT, "alpha"),
                source_model=ModelId.OPUS,
                source_phase=Phase.INDEPENDENT,
                text="alpha",
            ),
        ),
        generation_started_at=NOW,
        generation_completed_at=NOW,
    )
    system, _ = build_self_reflection_prompt(
        model_id=ModelId.OPUS,
        phase_1_response=response,
    )
    assert "as you actually see them now" in system
    assert "not as you predict peers will see them" in system
    assert "honest read" in system


def test_self_reflection_prompt_renders_claim_ids_in_user_message():
    """Models reference claims by claim_id; the user message must surface those IDs."""
    claim_a = Claim(
        claim_id=claim_id_for(ModelId.OPUS, Phase.INDEPENDENT, "alpha-text"),
        source_model=ModelId.OPUS,
        source_phase=Phase.INDEPENDENT,
        text="alpha-text",
    )
    claim_b = Claim(
        claim_id=claim_id_for(ModelId.OPUS, Phase.INDEPENDENT, "beta-text"),
        source_model=ModelId.OPUS,
        source_phase=Phase.INDEPENDENT,
        text="beta-text",
    )
    response = IndependentResponse(
        model_id=ModelId.OPUS,
        prompt_hash="h",
        response="r",
        focus_tag=FocusTag.CORRECTNESS,
        confidence=0.5,
        claims=(claim_a, claim_b),
        generation_started_at=NOW,
        generation_completed_at=NOW,
    )
    _, user = build_self_reflection_prompt(
        model_id=ModelId.OPUS,
        phase_1_response=response,
    )
    assert claim_a.claim_id in user
    assert claim_b.claim_id in user
    assert "alpha-text" in user
    assert "beta-text" in user


def test_self_reflection_prompt_warns_against_editing_phase_1():
    response = IndependentResponse(
        model_id=ModelId.OPUS,
        prompt_hash="h",
        response="here is my response",
        focus_tag=FocusTag.CORRECTNESS,
        confidence=0.7,
        claims=(
            Claim(
                claim_id=claim_id_for(ModelId.OPUS, Phase.INDEPENDENT, "alpha"),
                source_model=ModelId.OPUS,
                source_phase=Phase.INDEPENDENT,
                text="alpha",
            ),
        ),
        generation_started_at=NOW,
        generation_completed_at=NOW,
    )
    system, user = build_self_reflection_prompt(
        model_id=ModelId.OPUS,
        phase_1_response=response,
    )
    assert "NOT refinement of Phase 1" in system
    assert SELF_REFLECTION_TOOL_NAME in system
    assert "here is my response" in user
    assert "correctness" in user


# --- Parser: phase_1_response --------------------------------------------


def test_parse_phase_1_response_happy_path_assigns_claim_ids():
    tool_input = {
        "response": "the cache should use LRU eviction with size N",
        "focus_tag": "correctness",
        "confidence": 0.8,
        "claim_texts": ["LRU eviction is the right default", "size N must be tunable"],
    }
    resp = parse_phase_1_response_tool_use(
        tool_input,
        expected_model=ModelId.OPUS,
        prompt_hash="h1",
        generation_started_at=NOW,
        generation_completed_at=NOW,
    )
    assert resp.model_id is ModelId.OPUS
    assert resp.focus_tag is FocusTag.CORRECTNESS
    assert resp.confidence == 0.8
    assert len(resp.claims) == 2
    expected_id_0 = claim_id_for(ModelId.OPUS, Phase.INDEPENDENT, "LRU eviction is the right default")
    assert resp.claims[0].claim_id == expected_id_0
    assert resp.self_reflection_artifacts == ()


def test_parse_phase_1_rejects_unknown_focus_tag():
    tool_input = {
        "response": "r",
        "focus_tag": "vibes",
        "confidence": 0.5,
        "claim_texts": ["a"],
    }
    with pytest.raises(WireParseError, match="unknown focus_tag"):
        parse_phase_1_response_tool_use(
            tool_input,
            expected_model=ModelId.OPUS,
            prompt_hash="h",
            generation_started_at=NOW,
            generation_completed_at=NOW,
        )


def test_parse_phase_1_rejects_confidence_out_of_range():
    tool_input = {
        "response": "r",
        "focus_tag": "correctness",
        "confidence": 1.5,
        "claim_texts": ["a"],
    }
    with pytest.raises(WireParseError, match="outside"):
        parse_phase_1_response_tool_use(
            tool_input,
            expected_model=ModelId.OPUS,
            prompt_hash="h",
            generation_started_at=NOW,
            generation_completed_at=NOW,
        )


def test_parse_phase_1_rejects_empty_response():
    tool_input = {
        "response": "   ",
        "focus_tag": "correctness",
        "confidence": 0.5,
        "claim_texts": ["a"],
    }
    with pytest.raises(WireParseError, match="response must be a non-empty"):
        parse_phase_1_response_tool_use(
            tool_input,
            expected_model=ModelId.OPUS,
            prompt_hash="h",
            generation_started_at=NOW,
            generation_completed_at=NOW,
        )


def test_parse_phase_1_rejects_duplicate_claim_texts():
    tool_input = {
        "response": "r",
        "focus_tag": "correctness",
        "confidence": 0.5,
        "claim_texts": ["alpha", "alpha"],
    }
    with pytest.raises(WireParseError, match="duplicate claim_text"):
        parse_phase_1_response_tool_use(
            tool_input,
            expected_model=ModelId.OPUS,
            prompt_hash="h",
            generation_started_at=NOW,
            generation_completed_at=NOW,
        )


def test_parse_phase_1_rejects_empty_claim_text():
    tool_input = {
        "response": "r",
        "focus_tag": "correctness",
        "confidence": 0.5,
        "claim_texts": ["alpha", "   "],
    }
    with pytest.raises(WireParseError, match="non-empty strings"):
        parse_phase_1_response_tool_use(
            tool_input,
            expected_model=ModelId.OPUS,
            prompt_hash="h",
            generation_started_at=NOW,
            generation_completed_at=NOW,
        )


# --- Parser: self_reflection ---------------------------------------------


def test_parse_self_reflection_happy_path():
    own_id_a = claim_id_for(ModelId.OPUS, Phase.INDEPENDENT, "alpha")
    own_id_b = claim_id_for(ModelId.OPUS, Phase.INDEPENDENT, "beta")
    tool_input = {
        "strongest_claim_id": own_id_a,
        "weakest_claim_id": own_id_b,
        "tag_justification": "i picked correctness because alpha grounds it",
    }
    artifact = parse_self_reflection_tool_use(
        tool_input,
        expected_model=ModelId.OPUS,
        own_claim_ids={own_id_a, own_id_b},
        generated_at=NOW,
    )
    assert artifact.model_id is ModelId.OPUS
    assert artifact.strongest_claim_id == own_id_a
    assert artifact.weakest_claim_id == own_id_b
    assert artifact.generated_at == NOW


def test_parse_self_reflection_rejects_strongest_not_in_own_claims():
    tool_input = {
        "strongest_claim_id": "ghost",
        "weakest_claim_id": "abc",
        "tag_justification": "j",
    }
    with pytest.raises(WireParseError, match="strongest_claim_id"):
        parse_self_reflection_tool_use(
            tool_input,
            expected_model=ModelId.OPUS,
            own_claim_ids={"abc", "def"},
        )


def test_parse_self_reflection_rejects_weakest_not_in_own_claims():
    tool_input = {
        "strongest_claim_id": "abc",
        "weakest_claim_id": "ghost",
        "tag_justification": "j",
    }
    with pytest.raises(WireParseError, match="weakest_claim_id"):
        parse_self_reflection_tool_use(
            tool_input,
            expected_model=ModelId.OPUS,
            own_claim_ids={"abc", "def"},
        )


def test_parse_self_reflection_rejects_empty_justification():
    tool_input = {
        "strongest_claim_id": "abc",
        "weakest_claim_id": "def",
        "tag_justification": "   ",
    }
    with pytest.raises(WireParseError, match="tag_justification"):
        parse_self_reflection_tool_use(
            tool_input,
            expected_model=ModelId.OPUS,
            own_claim_ids={"abc", "def"},
        )


# --- Protocol conformance ------------------------------------------------


# --- compose_phase_1_with_reflection helper ------------------------------


def _build_response_with_two_claims() -> IndependentResponse:
    a = Claim(
        claim_id=claim_id_for(ModelId.OPUS, Phase.INDEPENDENT, "alpha"),
        source_model=ModelId.OPUS,
        source_phase=Phase.INDEPENDENT,
        text="alpha",
    )
    b = Claim(
        claim_id=claim_id_for(ModelId.OPUS, Phase.INDEPENDENT, "beta"),
        source_model=ModelId.OPUS,
        source_phase=Phase.INDEPENDENT,
        text="beta",
    )
    return IndependentResponse(
        model_id=ModelId.OPUS,
        prompt_hash="h",
        response="r",
        focus_tag=FocusTag.CORRECTNESS,
        confidence=0.7,
        claims=(a, b),
        generation_started_at=NOW,
        generation_completed_at=NOW,
    )


def test_compose_helper_folds_reflection_into_response():
    response = _build_response_with_two_claims()
    a_id, b_id = response.claims[0].claim_id, response.claims[1].claim_id
    reflection = SelfReflectionArtifact(
        model_id=ModelId.OPUS,
        generated_at=NOW,
        strongest_claim_id=a_id,
        weakest_claim_id=b_id,
        tag_justification="alpha grounds correctness",
    )
    folded = compose_phase_1_with_reflection(response, reflection)
    assert folded.self_reflection_artifacts == (reflection,)
    assert folded.model_id is ModelId.OPUS
    assert folded.claims == response.claims


def test_compose_helper_refuses_mismatched_model_id():
    response = _build_response_with_two_claims()
    a_id, b_id = response.claims[0].claim_id, response.claims[1].claim_id
    reflection = SelfReflectionArtifact(
        model_id=ModelId.SONNET,
        generated_at=NOW,
        strongest_claim_id=a_id,
        weakest_claim_id=b_id,
        tag_justification="j",
    )
    with pytest.raises(ValueError, match="model_id"):
        compose_phase_1_with_reflection(response, reflection)


def test_compose_helper_refuses_strongest_not_in_response_claims():
    response = _build_response_with_two_claims()
    b_id = response.claims[1].claim_id
    reflection = SelfReflectionArtifact(
        model_id=ModelId.OPUS,
        generated_at=NOW,
        strongest_claim_id="ghost",
        weakest_claim_id=b_id,
        tag_justification="j",
    )
    with pytest.raises(ValueError, match="strongest_claim_id"):
        compose_phase_1_with_reflection(response, reflection)


def test_compose_helper_refuses_weakest_not_in_response_claims():
    response = _build_response_with_two_claims()
    a_id = response.claims[0].claim_id
    reflection = SelfReflectionArtifact(
        model_id=ModelId.OPUS,
        generated_at=NOW,
        strongest_claim_id=a_id,
        weakest_claim_id="ghost",
        tag_justification="j",
    )
    with pytest.raises(ValueError, match="weakest_claim_id"):
        compose_phase_1_with_reflection(response, reflection)


# --- Protocol conformance ------------------------------------------------


class _StubPhase1Client:
    def submit_phase_1_response(self, **kwargs):
        raise NotImplementedError

    def submit_self_reflection(self, **kwargs):
        raise NotImplementedError


def test_phase1wire_client_protocol_runtime_checkable():
    assert isinstance(_StubPhase1Client(), Phase1WireClient)
