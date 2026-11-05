"""M3 "Output you can trust": golden files over a persisted session, and the
accountability sections annotated and layered modes append.

Golden files live in tests/synthesis/golden/ and are compared byte-exact.
unified.txt and transcript.txt were generated from the renderer BEFORE the M3
change; they lock those two modes as unchanged. annotated.txt and
layered.txt were written by hand from the M3 template, then the renderer was
made to match them.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from golden_lattice.memory_graph.base import (
    FocusTag,
    ModelId,
    OutputMode,
    Phase,
    claim_id_for,
)
from golden_lattice.memory_graph.schema import (
    Claim,
    ClaimTraceEntry,
    DialogueTurn,
    Elevation,
    IndependentResponse,
    Session,
    SurfacedDisagreement,
)
from golden_lattice.synthesis.attribution import render_output
from golden_lattice.synthesis.claim_trace import build_claim_trace
from golden_lattice.synthesis.disagreement import compute_surfaced_disagreements
from golden_lattice.synthesis.elevation import compute_elevations

from tests.synthesis.golden_session import (
    SURFACING_THRESHOLD,
    build_base_session,
    golden_path,
    load_persisted_session,
    render_from_persisted,
)


ELEVATED_HEADER = "--- elevated ("
DISAGREEMENTS_HEADER = "--- disagreements surfaced ("
OMITTED_HEADER = "--- omitted: what was dropped and why ("
ACCOUNTABLE_MODES = (OutputMode.ANNOTATED, OutputMode.LAYERED)


# --- The persisted session is the one the builder makes ------------------


def test_persisted_session_matches_builder_and_real_rules():
    """Drift lock: the persisted Phases 1-3 equal the builder's, and the
    persisted Phase 4 fields equal what the synthesis rules compute now."""
    persisted = load_persisted_session()
    base = build_base_session()
    assert persisted.model_copy(update={"phase_4": None}).model_dump_json() == (
        base.model_dump_json()
    )
    assert persisted.phase_4 is not None
    assert persisted.phase_4.claim_trace == build_claim_trace(base)
    assert persisted.phase_4.elevations == compute_elevations(base)
    assert persisted.phase_4.surfaced_disagreements == compute_surfaced_disagreements(
        base, confidence_threshold=SURFACING_THRESHOLD
    )


def test_persisted_session_covers_every_case_the_goldens_claim():
    session = load_persisted_session()
    p4 = session.phase_4
    assert p4 is not None
    dispositions = [e.disposition for e in p4.claim_trace]
    assert {"present", "modified", "omitted"} <= set(dispositions)
    modified = [e for e in p4.claim_trace if e.disposition == "modified"]
    assert any("[DISPUTED — " in (e.modified_text or "") for e in modified)
    omitted = [e for e in p4.claim_trace if e.disposition == "omitted"]
    assert all(e.omission_reason for e in omitted)
    assert len(p4.elevations) >= 1
    assert len(p4.surfaced_disagreements) >= 1
    assert session.phase_2 and session.phase_3


# --- Golden files, byte-exact -------------------------------------------


@pytest.mark.parametrize("mode", list(OutputMode), ids=lambda m: m.value)
def test_render_matches_golden_byte_exact(mode: OutputMode):
    session = load_persisted_session()
    expected = golden_path(mode).read_bytes()
    actual = render_from_persisted(session, mode).encode("utf-8")
    assert actual == expected


@pytest.mark.parametrize("mode", list(OutputMode), ids=lambda m: m.value)
def test_render_is_byte_equal_across_runs(mode: OutputMode):
    session = load_persisted_session()
    assert render_from_persisted(session, mode) == render_from_persisted(session, mode)


def test_persisted_transcript_output_matches_its_golden():
    session = load_persisted_session()
    assert session.phase_4 is not None
    assert session.phase_4.output_mode is OutputMode.TRANSCRIPT
    assert session.phase_4.output.encode("utf-8") == golden_path(
        OutputMode.TRANSCRIPT
    ).read_bytes()


@pytest.mark.parametrize("mode", ACCOUNTABLE_MODES, ids=lambda m: m.value)
def test_accountability_sections_follow_body_in_fixed_order(mode: OutputMode):
    out = render_from_persisted(load_persisted_session(), mode)
    e = out.index("\n\n" + ELEVATED_HEADER)
    d = out.index("\n\n" + DISAGREEMENTS_HEADER)
    o = out.index("\n\n" + OMITTED_HEADER)
    assert e < d < o


@pytest.mark.parametrize(
    "mode", (OutputMode.UNIFIED, OutputMode.TRANSCRIPT), ids=lambda m: m.value
)
def test_unified_and_transcript_carry_no_accountability_sections(mode: OutputMode):
    out = render_from_persisted(load_persisted_session(), mode)
    for header in (ELEVATED_HEADER, DISAGREEMENTS_HEADER, OMITTED_HEADER):
        assert header not in out


# --- Property-style: K omitted entries -> exactly K omitted lines --------


NOW = datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)


def _claim(model: ModelId, text: str) -> Claim:
    return Claim(
        claim_id=claim_id_for(model, Phase.INDEPENDENT, text),
        source_model=model,
        source_phase=Phase.INDEPENDENT,
        text=text,
    )


def _response(model: ModelId, claims: tuple[Claim, ...]) -> IndependentResponse:
    return IndependentResponse(
        model_id=model,
        prompt_hash="h",
        response=f"{model.value} prose",
        focus_tag=FocusTag.CORRECTNESS,
        confidence=0.8,
        claims=claims,
        generation_started_at=NOW,
        generation_completed_at=NOW,
    )


def _dyad_with_k_omitted(k: int) -> tuple[Session, tuple[ClaimTraceEntry, ...]]:
    """Opus and Sonnet, three claims each. The first k claims in trace order
    are omitted, each with a distinct reason; the rest are present."""
    opus = tuple(_claim(ModelId.OPUS, f"opus claim {i}") for i in range(3))
    sonnet = tuple(_claim(ModelId.SONNET, f"sonnet claim {i}") for i in range(3))
    session = Session(
        session_id="k",
        prompt="p",
        prompt_hash="h",
        created_at=NOW,
        models_invited=(ModelId.OPUS, ModelId.SONNET),
        phase_1={
            ModelId.OPUS: _response(ModelId.OPUS, opus),
            ModelId.SONNET: _response(ModelId.SONNET, sonnet),
        },
    )
    trace = tuple(
        ClaimTraceEntry(
            claim_id=c.claim_id,
            disposition="omitted",
            omission_reason=f"subsumed_by_other_claim:reason-{i}",
        )
        if i < k
        else ClaimTraceEntry(claim_id=c.claim_id, disposition="present")
        for i, c in enumerate(opus + sonnet)
    )
    return session, trace


def _section_lines(out: str, header_prefix: str) -> list[str]:
    """Lines of the section whose header starts with header_prefix, up to the
    next blank line or the end."""
    start = out.index(header_prefix)
    section = out[start:].split("\n\n", 1)[0]
    return section.split("\n")[1:]


@pytest.mark.parametrize("mode", ACCOUNTABLE_MODES, ids=lambda m: m.value)
@pytest.mark.parametrize("k", [0, 1, 2, 3, 6])
def test_footer_lists_exactly_k_omitted_entries(mode: OutputMode, k: int):
    session, trace = _dyad_with_k_omitted(k)
    out = render_output(
        session,
        mode=mode,
        claim_trace=trace,
        elevations=(),
        surfaced_disagreements=(),
    )
    assert ELEVATED_HEADER not in out
    assert DISAGREEMENTS_HEADER not in out
    if k == 0:
        assert OMITTED_HEADER not in out
        return
    assert f"{OMITTED_HEADER}{k}) ---" in out
    lines = _section_lines(out, OMITTED_HEADER)
    omitted = [e for e in trace if e.disposition == "omitted"]
    assert len(lines) == k == len(omitted)
    for line, entry in zip(lines, omitted):  # trace order
        assert line.endswith(f"{entry.claim_id}: {entry.omission_reason}")
        assert line.startswith("[O] ") or line.startswith("[S] ")


@pytest.mark.parametrize("mode", ACCOUNTABLE_MODES, ids=lambda m: m.value)
def test_all_omitted_body_starts_with_footer_not_blank_lines(mode: OutputMode):
    session, trace = _dyad_with_k_omitted(6)
    out = render_output(
        session,
        mode=mode,
        claim_trace=trace,
        elevations=(),
        surfaced_disagreements=(),
    )
    assert out.startswith(f"{OMITTED_HEADER}6) ---\n")


@pytest.mark.parametrize("mode", ACCOUNTABLE_MODES, ids=lambda m: m.value)
def test_no_sections_when_all_tuples_empty_body_unchanged(mode: OutputMode):
    """Null data: with nothing elevated, surfaced or omitted, the output is
    exactly the body; no headers, no trailing blank lines."""
    session, trace = _dyad_with_k_omitted(0)
    out = render_output(
        session,
        mode=mode,
        claim_trace=trace,
        elevations=(),
        surfaced_disagreements=(),
    )
    for header in (ELEVATED_HEADER, DISAGREEMENTS_HEADER, OMITTED_HEADER):
        assert header not in out
    assert not out.endswith("\n")
    if mode is OutputMode.ANNOTATED:
        assert out.split("\n") == [
            "[O] opus claim 0",
            "[O] opus claim 1",
            "[O] opus claim 2",
            "[S] sonnet claim 0",
            "[S] sonnet claim 1",
            "[S] sonnet claim 2",
        ]


# --- Edges the renderer must not raise on --------------------------------


@pytest.mark.parametrize("mode", ACCOUNTABLE_MODES, ids=lambda m: m.value)
def test_omitted_claim_outside_phase_1_lookup_renders_without_marker(mode: OutputMode):
    session, trace = _dyad_with_k_omitted(0)
    stray = ClaimTraceEntry(
        claim_id="not-a-phase-1-claim",
        disposition="omitted",
        omission_reason="superseded_by_phase_3_revision:t9",
    )
    out = render_output(
        session,
        mode=mode,
        claim_trace=trace + (stray,),
        elevations=(),
        surfaced_disagreements=(),
    )
    assert _section_lines(out, OMITTED_HEADER) == [
        "not-a-phase-1-claim: superseded_by_phase_3_revision:t9"
    ]


@pytest.mark.parametrize("mode", ACCOUNTABLE_MODES, ids=lambda m: m.value)
def test_elevation_and_disagreement_lines(mode: OutputMode):
    """Elevation cites claim ids and each converge turn with its speaker; a
    turn missing from phase_3 renders as its id alone. Disagreement renders
    claim ids and the note verbatim."""
    session, trace = _dyad_with_k_omitted(0)
    opus_0 = session.phase_1[ModelId.OPUS].claims[0].claim_id
    sonnet_0 = session.phase_1[ModelId.SONNET].claims[0].claim_id
    with_turn = session.model_copy(
        update={
            "phase_3": (
                DialogueTurn(
                    turn_id="c1",
                    speaker_model=ModelId.SONNET,
                    channel="converge",
                    target_model=ModelId.OPUS,
                    target_claim_ids=(opus_0,),
                    content="aligned",
                ),
            )
        }
    )
    elevation = Elevation(claim_ids=(opus_0,), converge_turn_ids=("c1", "c-gone"))
    note = "claude-opus-5 disagrees with claude-sonnet-5: verbatim note, kept."
    disagreement = SurfacedDisagreement(claim_ids=(opus_0, sonnet_0), note=note)
    out = render_output(
        with_turn,
        mode=mode,
        claim_trace=trace,
        elevations=(elevation,),
        surfaced_disagreements=(disagreement,),
    )
    assert _section_lines(out, ELEVATED_HEADER) == [
        f"claim_ids: {opus_0} | converge: c1 (claude-sonnet-5), c-gone"
    ]
    assert _section_lines(out, DISAGREEMENTS_HEADER) == [
        f"claim_ids: {opus_0}, {sonnet_0} | {note}"
    ]
    assert "--- elevated (1) ---" in out
    assert "--- disagreements surfaced (1) ---" in out
