"""Lattice events — the shared protocol between live orchestration and replay.

The architecture's commitment: the live three-column TUI and the replay-from-disk
TUI consume the same event stream. The orchestrator (future) fires events via a
progress callback as phases unfold; the replay emitter walks a persisted Session
and yields events in natural temporal order. One renderer, two sources.

Each event is a frozen Pydantic model with a discriminating event_type literal
and the specific fields that event carries. A renderer dispatches via isinstance
or pattern-match on event_type. Timing is carried on every event as
timestamp_offset_ms relative to session start; the renderer chooses whether to
honor it (real-time playback) or fast-forward.
"""

from __future__ import annotations

from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict

from golden_lattice.memory_graph.base import (
    FocusTag,
    ModelId,
    OutputMode,
    SynthesisRule,
)
from golden_lattice.memory_graph.metrics import FlagInterpretation
from golden_lattice.memory_graph.schema import (
    ClaimTraceEntry,
    Elevation,
    SessionMetrics,
    SurfacedDisagreement,
)


__all__ = [
    "EventType",
    "SessionStartedEvent",
    "Phase0DatetimeGroundingEvent",
    "Phase0ProposalSubmittedEvent",
    "Phase0SearchResultEvent",
    "Phase0FailedSearchEvent",
    "Phase0FeedFrozenEvent",
    "ModelStreamDeltaEvent",
    "SessionErrorEvent",
    "Phase1ResponseStartedEvent",
    "Phase1ClaimEvent",
    "Phase1ResponseCompletedEvent",
    "SelfReflectionEvent",
    "Phase2CrossReadingEvent",
    "Phase2TaggingEvent",
    "Phase3TurnEvent",
    "Phase4ArtifactEvent",
    "Phase4MetricsEvent",
    "Phase4FlagInterpretationsEvent",
    "SessionCompletedEvent",
    "LatticeEvent",
]


class EventType:
    """Event-type string constants. Use as literal discriminators."""

    SESSION_STARTED = "session_started"
    PHASE_0_DATETIME_GROUNDING = "phase_0_datetime_grounding"
    PHASE_0_PROPOSAL_SUBMITTED = "phase_0_proposal_submitted"
    PHASE_0_SEARCH_RESULT = "phase_0_search_result"
    PHASE_0_FAILED_SEARCH = "phase_0_failed_search"
    PHASE_0_FEED_FROZEN = "phase_0_feed_frozen"
    MODEL_STREAM_DELTA = "model_stream_delta"
    SESSION_ERROR = "session_error"
    PHASE_1_RESPONSE_STARTED = "phase_1_response_started"
    PHASE_1_CLAIM = "phase_1_claim"
    PHASE_1_RESPONSE_COMPLETED = "phase_1_response_completed"
    SELF_REFLECTION = "self_reflection"
    PHASE_2_CROSS_READING = "phase_2_cross_reading"
    PHASE_2_TAGGING = "phase_2_tagging"
    PHASE_3_TURN = "phase_3_turn"
    PHASE_4_ARTIFACT = "phase_4_artifact"
    PHASE_4_METRICS = "phase_4_metrics"
    PHASE_4_FLAG_INTERPRETATIONS = "phase_4_flag_interpretations"
    SESSION_COMPLETED = "session_completed"


class _BaseEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    timestamp_offset_ms: int


class SessionStartedEvent(_BaseEvent):
    event_type: Literal["session_started"] = "session_started"
    session_id: str
    prompt: str
    prompt_hash: str
    models_invited: tuple[ModelId, ...]


class Phase0DatetimeGroundingEvent(_BaseEvent):
    """Temporal grounding seeded as the first feed entry — the §5.0
    precondition gate. Deterministic, no model involvement."""

    event_type: Literal["phase_0_datetime_grounding"] = "phase_0_datetime_grounding"
    entry_id: str
    timezone_name: str
    formatted_text: str


class Phase0ProposalSubmittedEvent(_BaseEvent):
    """One model submitted its InvestigationProposal during Phase 0a.
    No peer visibility (Phase 1 independence pattern)."""

    event_type: Literal["phase_0_proposal_submitted"] = "phase_0_proposal_submitted"
    model_id: ModelId
    queries: tuple[str, ...]


class Phase0SearchResultEvent(_BaseEvent):
    """A successful search landed as a feed entry."""

    event_type: Literal["phase_0_search_result"] = "phase_0_search_result"
    entry_id: str
    query: str
    result_text_preview: str
    source_urls: tuple[str, ...] = ()


class Phase0FailedSearchEvent(_BaseEvent):
    """A failed search landed as a typed feed entry — all peers see it
    (§8 no-silent-failures)."""

    event_type: Literal["phase_0_failed_search"] = "phase_0_failed_search"
    entry_id: str
    query: str
    reason: str


class Phase0FeedFrozenEvent(_BaseEvent):
    """The feed is frozen; Phase 1 can now begin. Mapping onto §8's freeze
    discipline applied upstream of independent generation."""

    event_type: Literal["phase_0_feed_frozen"] = "phase_0_feed_frozen"
    entry_count: int


class ModelStreamDeltaEvent(_BaseEvent):
    """One incremental provider delta, emitted while a model is generating.

    ``delta_kind`` distinguishes ordinary text from the partial JSON emitted
    by Anthropic while a forced tool call is being assembled. The latter is
    intentionally surfaced as transport evidence, not treated as a parsed
    claim until the final tool response validates.
    """

    event_type: Literal["model_stream_delta"] = "model_stream_delta"
    model_id: ModelId
    phase: str
    delta: str
    delta_kind: Literal["text", "tool_input"]


class SessionErrorEvent(_BaseEvent):
    """A live run failed but its transport remains available."""

    event_type: Literal["session_error"] = "session_error"
    message: str
    phase: Optional[str] = None
    model_id: Optional[ModelId] = None


class Phase1ResponseStartedEvent(_BaseEvent):
    event_type: Literal["phase_1_response_started"] = "phase_1_response_started"
    model_id: ModelId


class Phase1ClaimEvent(_BaseEvent):
    event_type: Literal["phase_1_claim"] = "phase_1_claim"
    model_id: ModelId
    claim_id: str
    text: str


class Phase1ResponseCompletedEvent(_BaseEvent):
    event_type: Literal["phase_1_response_completed"] = "phase_1_response_completed"
    model_id: ModelId
    focus_tag: FocusTag
    confidence: float
    claim_count: int


class SelfReflectionEvent(_BaseEvent):
    event_type: Literal["self_reflection"] = "self_reflection"
    model_id: ModelId
    strongest_claim_id: str
    weakest_claim_id: str
    tag_justification: str


class Phase2CrossReadingEvent(_BaseEvent):
    event_type: Literal["phase_2_cross_reading"] = "phase_2_cross_reading"
    reader_model: ModelId
    target_model: ModelId
    agreements_count: int
    disagreements_count: int
    missing_count: int


class Phase2TaggingEvent(_BaseEvent):
    event_type: Literal["phase_2_tagging"] = "phase_2_tagging"
    tagger_model: ModelId
    peer_tags_count: int
    self_tags_count: int


class Phase3TurnEvent(_BaseEvent):
    event_type: Literal["phase_3_turn"] = "phase_3_turn"
    turn_id: str
    speaker_model: ModelId
    channel: Literal["critique", "augment", "converge"]
    target_model: Optional[ModelId]
    target_claim_ids: tuple[str, ...]
    content: str


class Phase4ArtifactEvent(_BaseEvent):
    event_type: Literal["phase_4_artifact"] = "phase_4_artifact"
    output_mode: OutputMode
    synthesis_rules_applied: tuple[SynthesisRule, ...]
    output: str
    claim_trace: tuple[ClaimTraceEntry, ...]
    elevations: tuple[Elevation, ...]
    surfaced_disagreements: tuple[SurfacedDisagreement, ...]


class Phase4MetricsEvent(_BaseEvent):
    event_type: Literal["phase_4_metrics"] = "phase_4_metrics"
    metrics: Optional[SessionMetrics]


class Phase4FlagInterpretationsEvent(_BaseEvent):
    event_type: Literal["phase_4_flag_interpretations"] = "phase_4_flag_interpretations"
    interpretations: tuple[FlagInterpretation, ...]


class SessionCompletedEvent(_BaseEvent):
    event_type: Literal["session_completed"] = "session_completed"
    session_id: str


LatticeEvent = Union[
    SessionStartedEvent,
    Phase0DatetimeGroundingEvent,
    Phase0ProposalSubmittedEvent,
    Phase0SearchResultEvent,
    Phase0FailedSearchEvent,
    Phase0FeedFrozenEvent,
    ModelStreamDeltaEvent,
    SessionErrorEvent,
    Phase1ResponseStartedEvent,
    Phase1ClaimEvent,
    Phase1ResponseCompletedEvent,
    SelfReflectionEvent,
    Phase2CrossReadingEvent,
    Phase2TaggingEvent,
    Phase3TurnEvent,
    Phase4ArtifactEvent,
    Phase4MetricsEvent,
    Phase4FlagInterpretationsEvent,
    SessionCompletedEvent,
]
