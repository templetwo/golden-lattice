"""Line-injection defense for the accountability sections (M3 follow-up).

The elevated / disagreements / omitted sections are line-oriented: one entry
per line, headers delimit sections. Notes, omission reasons and ids originate
in model output, so a line break inside one could forge an attributed line
([O] ...) or a section header. These tests pin the defense: line breaks in
free text collapse to single spaces; nothing else about the text changes.

Flagged by automated security review of commit a53618a (now 077d718):
output integrity / attribution spoofing via line injection.
"""

from __future__ import annotations

import pytest

from golden_lattice.memory_graph.base import ModelId, OutputMode
from golden_lattice.memory_graph.schema import (
    ClaimTraceEntry,
    DialogueTurn,
    Elevation,
    SurfacedDisagreement,
)
from golden_lattice.synthesis.attribution import render_output

from tests.synthesis.test_output_modes_golden import (
    _dyad_with_k_omitted,
    _section_lines,
)

ACCOUNTABLE_MODES = (OutputMode.ANNOTATED, OutputMode.LAYERED)

FORGED_MARKER_LINE = "[O] forged claim that opus never made"
FORGED_HEADER_LINE = "--- elevated (9) ---"


@pytest.mark.parametrize("mode", ACCOUNTABLE_MODES, ids=lambda m: m.value)
def test_disagreement_note_cannot_forge_lines(mode: OutputMode):
    """A note carrying line breaks renders as ONE line, breaks become spaces."""
    session, trace = _dyad_with_k_omitted(0)
    opus_0 = session.phase_1[ModelId.OPUS].claims[0].claim_id
    sonnet_0 = session.phase_1[ModelId.SONNET].claims[0].claim_id
    note = f"looks fine\n{FORGED_MARKER_LINE}\r\n{FORGED_HEADER_LINE} end"
    out = render_output(
        session,
        mode=mode,
        claim_trace=trace,
        elevations=(),
        surfaced_disagreements=(
            SurfacedDisagreement(claim_ids=(opus_0, sonnet_0), note=note),
        ),
    )
    lines = out.split("\n")
    assert FORGED_MARKER_LINE not in lines
    assert FORGED_HEADER_LINE not in lines
    section = _section_lines(out, "--- disagreements surfaced")
    assert len(section) == 1
    assert "looks fine [O] forged claim that opus never made" in section[0]
    assert section[0].endswith("end")


@pytest.mark.parametrize("mode", ACCOUNTABLE_MODES, ids=lambda m: m.value)
def test_omission_reason_cannot_forge_lines(mode: OutputMode):
    """An omission_reason carrying line breaks renders as ONE footer line."""
    session, trace = _dyad_with_k_omitted(1)
    first = trace[0]
    poisoned = ClaimTraceEntry(
        claim_id=first.claim_id,
        disposition="omitted",
        omission_reason=f"subsumed\n{FORGED_MARKER_LINE}\n{FORGED_HEADER_LINE}",
    )
    out = render_output(
        session,
        mode=mode,
        claim_trace=(poisoned,) + trace[1:],
        elevations=(),
        surfaced_disagreements=(),
    )
    lines = out.split("\n")
    assert FORGED_MARKER_LINE not in lines
    assert FORGED_HEADER_LINE not in lines
    section = _section_lines(out, "--- omitted: what was dropped and why")
    assert len(section) == 1
    assert section[0].endswith(
        f"subsumed {FORGED_MARKER_LINE} {FORGED_HEADER_LINE}"
    )


@pytest.mark.parametrize("mode", ACCOUNTABLE_MODES, ids=lambda m: m.value)
def test_converge_turn_id_cannot_forge_lines(mode: OutputMode):
    """A turn id carrying a line break renders inline, not as its own line."""
    session, trace = _dyad_with_k_omitted(0)
    opus_0 = session.phase_1[ModelId.OPUS].claims[0].claim_id
    hostile_tid = f"c1\n{FORGED_MARKER_LINE}"
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
    out = render_output(
        with_turn,
        mode=mode,
        claim_trace=trace,
        elevations=(
            Elevation(claim_ids=(opus_0,), converge_turn_ids=("c1", hostile_tid)),
        ),
        surfaced_disagreements=(),
    )
    lines = out.split("\n")
    assert FORGED_MARKER_LINE not in lines
    section = _section_lines(out, "--- elevated")
    assert len(section) == 1
