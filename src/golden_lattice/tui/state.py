"""TUI state accumulator — folds LatticeEvents into a snapshot the layout reads.

One TuiState per session. Each event mutates the state in place; the renderer
rebuilds the Rich layout from the current state on every refresh. No panel
owns state; every panel reads from this accumulator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from golden_lattice.events import (
    LatticeEvent,
    Phase1ClaimEvent,
    Phase1ResponseCompletedEvent,
    Phase1ResponseStartedEvent,
    Phase2CrossReadingEvent,
    Phase2TaggingEvent,
    Phase3TurnEvent,
    Phase4ArtifactEvent,
    Phase4FlagInterpretationsEvent,
    Phase4MetricsEvent,
    SelfReflectionEvent,
    SessionCompletedEvent,
    SessionStartedEvent,
)
from golden_lattice.memory_graph.base import ModelId


@dataclass
class TuiState:
    session_id: Optional[str] = None
    prompt: Optional[str] = None
    invited_models: tuple[ModelId, ...] = ()

    phase_1_started_ms: dict[ModelId, int] = field(default_factory=dict)
    phase_1_claims: dict[ModelId, list[Phase1ClaimEvent]] = field(default_factory=dict)
    phase_1_completed: dict[ModelId, Phase1ResponseCompletedEvent] = field(default_factory=dict)
    self_reflections: dict[ModelId, SelfReflectionEvent] = field(default_factory=dict)

    cross_readings: list[Phase2CrossReadingEvent] = field(default_factory=list)
    taggings: list[Phase2TaggingEvent] = field(default_factory=list)

    turns: list[Phase3TurnEvent] = field(default_factory=list)

    artifact: Optional[Phase4ArtifactEvent] = None
    metrics_event: Optional[Phase4MetricsEvent] = None
    flag_event: Optional[Phase4FlagInterpretationsEvent] = None

    session_complete: bool = False
    current_offset_ms: int = 0


def apply_event(state: TuiState, event: LatticeEvent) -> None:
    """Fold one event into state. Pure-ish — mutates state in place."""
    state.current_offset_ms = event.timestamp_offset_ms

    if isinstance(event, SessionStartedEvent):
        state.session_id = event.session_id
        state.prompt = event.prompt
        state.invited_models = event.models_invited
        for m in event.models_invited:
            state.phase_1_claims.setdefault(m, [])
    elif isinstance(event, Phase1ResponseStartedEvent):
        state.phase_1_started_ms[event.model_id] = event.timestamp_offset_ms
    elif isinstance(event, Phase1ClaimEvent):
        state.phase_1_claims.setdefault(event.model_id, []).append(event)
    elif isinstance(event, Phase1ResponseCompletedEvent):
        state.phase_1_completed[event.model_id] = event
    elif isinstance(event, SelfReflectionEvent):
        state.self_reflections[event.model_id] = event
    elif isinstance(event, Phase2CrossReadingEvent):
        state.cross_readings.append(event)
    elif isinstance(event, Phase2TaggingEvent):
        state.taggings.append(event)
    elif isinstance(event, Phase3TurnEvent):
        state.turns.append(event)
    elif isinstance(event, Phase4ArtifactEvent):
        state.artifact = event
    elif isinstance(event, Phase4MetricsEvent):
        state.metrics_event = event
    elif isinstance(event, Phase4FlagInterpretationsEvent):
        state.flag_event = event
    elif isinstance(event, SessionCompletedEvent):
        state.session_complete = True


def converge_pairs_per_claim(state: TuiState) -> dict[str, list[Phase3TurnEvent]]:
    """For each claim_id targeted by ≥2 converge turns from distinct speakers,
    return the list of converge turns. This is the Rule 2 elevation condition
    — the architecture's premise being earned. The loom panel highlights
    these doubled converges visibly.
    """
    by_claim: dict[str, list[Phase3TurnEvent]] = {}
    for turn in state.turns:
        if turn.channel != "converge":
            continue
        for cid in turn.target_claim_ids:
            by_claim.setdefault(cid, []).append(turn)
    # Keep only claims where ≥2 distinct speakers converged.
    return {
        cid: turns
        for cid, turns in by_claim.items()
        if len({t.speaker_model for t in turns}) >= 2
    }
