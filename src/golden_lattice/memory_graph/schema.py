"""Memory Graph schema — the executable spec for a Golden Lattice session.

Four invariants live here as structural refusals:
  1. No authority gradient — Phase 4 synthesis is rule-based; no model_id on SynthesisArtifact.
  2. Symmetric visibility — Session.phase_1 is keyed by every invited model; CrossReading covers all (reader, target) pairs.
  3. Contribution parity — SessionMetrics computes per-model shares; parity_below_threshold flags collapse toward routing.
  4. Irreducibility preservation — ClaimTraceEntry is required for every Phase 1 claim; missing entries surface as violations.

Refusals, not constraints. They make space.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from golden_lattice.memory_graph.base import (
    PARITY_THRESHOLD,
    FocusTag,
    ModelId,
    Phase,
    claim_id_for,
)
from golden_lattice.memory_graph.tagging import Phase2Tagging

__all__ = [
    "PARITY_THRESHOLD",
    "FocusTag",
    "ModelId",
    "Phase",
    "claim_id_for",
    "Claim",
    "SelfReflectionArtifact",
    "IndependentResponse",
    "ClaimRef",
    "Disagreement",
    "CrossReading",
    "DialogueTurn",
    "ClaimTraceEntry",
    "SynthesisArtifact",
    "SessionMetrics",
    "Session",
]


class Claim(BaseModel):
    model_config = ConfigDict(frozen=True)

    claim_id: str
    source_model: ModelId
    source_phase: Phase
    text: str
    parent_claim_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _check_phase_lineage(self) -> "Claim":
        if self.source_phase is Phase.INDEPENDENT and self.parent_claim_ids:
            raise ValueError(
                "Phase 1 claims cannot have parent_claim_ids — they are independent generation."
            )
        if self.source_phase is Phase.SYNTHESIS:
            raise ValueError(
                "Synthesis is rule-based and does not produce new claims; it only traces existing ones."
            )
        return self

    @model_validator(mode="after")
    def _check_claim_id(self) -> "Claim":
        expected = claim_id_for(self.source_model, self.source_phase, self.text)
        if self.claim_id != expected:
            raise ValueError(
                f"claim_id {self.claim_id} does not match content hash {expected}. "
                "Claims are content-addressed for irreducibility tracing."
            )
        return self


class SelfReflectionArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)

    model_id: ModelId
    generated_at: datetime
    content: str


class IndependentResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    model_id: ModelId
    prompt_hash: str
    response: str
    focus_tag: FocusTag
    confidence: float
    claims: tuple[Claim, ...]
    self_reflection_artifacts: tuple[SelfReflectionArtifact, ...] = ()
    generation_started_at: datetime
    generation_completed_at: datetime
    latency_used_for_reflection_ms: int = 0

    @model_validator(mode="after")
    def _check_claim_provenance(self) -> "IndependentResponse":
        for claim in self.claims:
            if claim.source_model is not self.model_id:
                raise ValueError(
                    f"Claim {claim.claim_id} attributed to {claim.source_model} "
                    f"but lives in IndependentResponse for {self.model_id}."
                )
            if claim.source_phase is not Phase.INDEPENDENT:
                raise ValueError(
                    f"Claim {claim.claim_id} has phase {claim.source_phase} "
                    f"but is in Phase 1 IndependentResponse."
                )
        for artifact in self.self_reflection_artifacts:
            if artifact.model_id is not self.model_id:
                raise ValueError(
                    "Self-reflection artifacts must come from the same model."
                )
        return self

    @model_validator(mode="after")
    def _confidence_in_unit_interval(self) -> "IndependentResponse":
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"confidence {self.confidence} is outside [0, 1]. "
                "Phase 1 confidence is a self-report, not a synthesis weight."
            )
        return self


class ClaimRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    claim_id: str


class Disagreement(BaseModel):
    model_config = ConfigDict(frozen=True)

    target_claim_id: str
    reason: str


class CrossReading(BaseModel):
    model_config = ConfigDict(frozen=True)

    reader_model: ModelId
    target_model: ModelId
    agreements: tuple[ClaimRef, ...] = ()
    disagreements: tuple[Disagreement, ...] = ()
    missing: tuple[Claim, ...] = ()

    @model_validator(mode="after")
    def _reader_is_not_target(self) -> "CrossReading":
        if self.reader_model is self.target_model:
            raise ValueError(
                "A model cannot cross-read its own response — Phase 2 is symmetric across distinct pairs."
            )
        return self

    @model_validator(mode="after")
    def _missing_claims_attributed_to_reader(self) -> "CrossReading":
        for claim in self.missing:
            if claim.source_model is not self.reader_model:
                raise ValueError(
                    "Claims surfaced as 'missing' belong to the reader noticing the gap."
                )
            if claim.source_phase is not Phase.CROSS_READING:
                raise ValueError(
                    "Cross-reading claims must have source_phase=CROSS_READING."
                )
        return self


DialogueChannel = Literal["critique", "augment", "converge"]
DIALOGUE_CHANNEL_CAP = 3


class DialogueTurn(BaseModel):
    model_config = ConfigDict(frozen=True)

    turn_id: str
    speaker_model: ModelId
    channel: DialogueChannel
    target_claim_id: str
    content: str


ClaimDisposition = Literal["present", "modified", "omitted"]


class ClaimTraceEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    claim_id: str
    disposition: ClaimDisposition
    modified_text: Optional[str] = None
    omission_reason: Optional[str] = None

    @model_validator(mode="after")
    def _disposition_requires_evidence(self) -> "ClaimTraceEntry":
        if self.disposition == "modified" and not self.modified_text:
            raise ValueError(
                f"Claim {self.claim_id} marked modified but no modified_text supplied. "
                "The trace itself is the proof."
            )
        if self.disposition == "omitted" and not self.omission_reason:
            raise ValueError(
                f"Claim {self.claim_id} marked omitted but no omission_reason supplied. "
                "Omission without logged reason is invisible collapse."
            )
        if self.disposition == "present" and (self.modified_text or self.omission_reason):
            raise ValueError(
                f"Claim {self.claim_id} marked present but carries modification or omission evidence."
            )
        return self


class SynthesisArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)

    output: str
    claim_trace: tuple[ClaimTraceEntry, ...]
    synthesis_rules_applied: tuple[str, ...]


class SessionMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    distinct_claim_share: dict[ModelId, float]
    edge_case_coverage_share: dict[ModelId, float]
    structural_pattern_share: dict[ModelId, float]
    parity_threshold: float = PARITY_THRESHOLD
    irreducibility_violations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _shares_are_valid(self) -> "SessionMetrics":
        for label, mapping in (
            ("distinct_claim_share", self.distinct_claim_share),
            ("edge_case_coverage_share", self.edge_case_coverage_share),
            ("structural_pattern_share", self.structural_pattern_share),
        ):
            for model_id, share in mapping.items():
                if not 0.0 <= share <= 1.0:
                    raise ValueError(
                        f"{label}[{model_id}] = {share} is outside [0, 1]."
                    )
        if not 0.0 < self.parity_threshold < 1.0:
            raise ValueError(
                f"parity_threshold must be in (0, 1), got {self.parity_threshold}."
            )
        return self

    @property
    def parity_below_threshold(self) -> bool:
        for mapping in (
            self.distinct_claim_share,
            self.edge_case_coverage_share,
            self.structural_pattern_share,
        ):
            if any(share < self.parity_threshold for share in mapping.values()):
                return True
        return False

    @property
    def parity_violations(self) -> list[tuple[str, ModelId, float]]:
        violations: list[tuple[str, ModelId, float]] = []
        for label, mapping in (
            ("distinct_claim_share", self.distinct_claim_share),
            ("edge_case_coverage_share", self.edge_case_coverage_share),
            ("structural_pattern_share", self.structural_pattern_share),
        ):
            for model_id, share in mapping.items():
                if share < self.parity_threshold:
                    violations.append((label, model_id, share))
        return violations


class Session(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: str
    prompt: str
    prompt_hash: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    models_invited: tuple[ModelId, ...]
    phase_1: dict[ModelId, IndependentResponse]
    phase_2: tuple[CrossReading, ...] = ()
    phase_2_taggings: tuple[Phase2Tagging, ...] = ()
    phase_3: tuple[DialogueTurn, ...] = ()
    phase_4: Optional[SynthesisArtifact] = None
    metrics: Optional[SessionMetrics] = None

    @field_validator("models_invited")
    @classmethod
    def _at_least_two_siblings(cls, v: tuple[ModelId, ...]) -> tuple[ModelId, ...]:
        if len(set(v)) < 2:
            raise ValueError(
                "A Lattice session needs at least two distinct siblings — "
                "co-authorship is impossible alone."
            )
        return v

    @model_validator(mode="after")
    def _phase_1_covers_invited(self) -> "Session":
        invited = set(self.models_invited)
        present = set(self.phase_1.keys())
        if present != invited:
            missing = invited - present
            extra = present - invited
            msg_parts = []
            if missing:
                msg_parts.append(f"missing Phase 1 responses for: {sorted(m.value for m in missing)}")
            if extra:
                msg_parts.append(f"unexpected Phase 1 responses for: {sorted(m.value for m in extra)}")
            raise ValueError(
                "Symmetric visibility requires Phase 1 from every invited model — "
                + "; ".join(msg_parts)
            )
        for model_id, response in self.phase_1.items():
            if response.model_id is not model_id:
                raise ValueError(
                    f"phase_1 key {model_id} does not match response.model_id {response.model_id}."
                )
            if response.prompt_hash != self.prompt_hash:
                raise ValueError(
                    f"Phase 1 response for {model_id} has prompt_hash {response.prompt_hash} "
                    f"but session prompt_hash is {self.prompt_hash}. "
                    "Symmetric visibility requires the same prompt."
                )
        return self

    @model_validator(mode="after")
    def _dialogue_channel_caps(self) -> "Session":
        counts: dict[tuple[ModelId, str], int] = {}
        for turn in self.phase_3:
            key = (turn.speaker_model, turn.channel)
            counts[key] = counts.get(key, 0) + 1
            if counts[key] > DIALOGUE_CHANNEL_CAP:
                raise ValueError(
                    f"Phase 3 hard cap exceeded: {turn.speaker_model.value} has more than "
                    f"{DIALOGUE_CHANNEL_CAP} turns on channel '{turn.channel}'. "
                    "Hard caps prevent quantitative collapse."
                )
        return self

    @model_validator(mode="after")
    def _synthesis_traces_every_phase_1_claim(self) -> "Session":
        if self.phase_4 is None:
            return self
        phase_1_claim_ids = {
            claim.claim_id
            for response in self.phase_1.values()
            for claim in response.claims
        }
        traced_ids = {entry.claim_id for entry in self.phase_4.claim_trace}
        untraced = phase_1_claim_ids - traced_ids
        if untraced:
            raise ValueError(
                f"Irreducibility preservation violated: {len(untraced)} Phase 1 claim(s) "
                f"have no entry in claim_trace. Missing: {sorted(untraced)}. "
                "Every distinct claim must be present, modified, or omitted-with-reason."
            )
        return self

    @model_validator(mode="after")
    def _taggings_are_well_formed(self) -> "Session":
        if not self.phase_2_taggings:
            return self
        invited = set(self.models_invited)
        seen_taggers: set[ModelId] = set()
        all_claims_by_id = {c.claim_id: c for c in self.all_claims()}
        vocabulary_versions: set[str] = set()

        for tagging in self.phase_2_taggings:
            vocabulary_versions.add(tagging.vocabulary_version)
            if tagging.tagger_model not in invited:
                raise ValueError(
                    f"Phase2Tagging from {tagging.tagger_model.value} but that model "
                    f"is not in models_invited. Symmetric visibility broken at the tagging layer."
                )
            if tagging.tagger_model in seen_taggers:
                raise ValueError(
                    f"Multiple Phase2Tagging entries from {tagging.tagger_model.value}. "
                    "Each model produces at most one tagging per session."
                )
            seen_taggers.add(tagging.tagger_model)

            for ct in tagging.peer_tags:
                claim = all_claims_by_id.get(ct.claim_id)
                if claim is None:
                    raise ValueError(
                        f"Phase2Tagging from {tagging.tagger_model.value} tags "
                        f"unknown claim_id {ct.claim_id}. Tags must reference existing claims."
                    )
                if claim.source_model is tagging.tagger_model:
                    raise ValueError(
                        f"Claim {ct.claim_id} authored by {tagging.tagger_model.value} "
                        "appears in peer_tags. Own claims belong in self_tags."
                    )

            for ct in tagging.self_tags:
                claim = all_claims_by_id.get(ct.claim_id)
                if claim is None:
                    raise ValueError(
                        f"Phase2Tagging from {tagging.tagger_model.value} self-tags "
                        f"unknown claim_id {ct.claim_id}."
                    )
                if claim.source_model is not tagging.tagger_model:
                    raise ValueError(
                        f"Claim {ct.claim_id} authored by {claim.source_model.value} "
                        f"appears in self_tags of {tagging.tagger_model.value}. "
                        "self_tags is for the tagger's own claims only."
                    )

        if len(vocabulary_versions) > 1:
            raise ValueError(
                "Phase 2 taggings in a single session must all use the same "
                f"vocabulary_version. Found: {sorted(vocabulary_versions)}. "
                "Mixed versions corrupt consensus computation."
            )
        return self

    @model_validator(mode="after")
    def _cross_readings_resolve_claim_ids(self) -> "Session":
        if not self.phase_2:
            return self
        all_claim_ids = {c.claim_id for c in self.all_claims()}
        for cr in self.phase_2:
            for ref in cr.agreements:
                if ref.claim_id not in all_claim_ids:
                    raise ValueError(
                        f"CrossReading from {cr.reader_model.value} agrees with "
                        f"unknown claim_id {ref.claim_id}. Agreements must resolve."
                    )
            for d in cr.disagreements:
                if d.target_claim_id not in all_claim_ids:
                    raise ValueError(
                        f"CrossReading from {cr.reader_model.value} disagrees with "
                        f"unknown target_claim_id {d.target_claim_id}. "
                        "Disagreements must resolve."
                    )
        return self

    def all_claims(self) -> list[Claim]:
        out: list[Claim] = []
        for response in self.phase_1.values():
            out.extend(response.claims)
        for cr in self.phase_2:
            out.extend(cr.missing)
        return out
