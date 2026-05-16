"""Leaf primitives for the Memory Graph: enums and content addressing.

Lives below schema.py and tagging.py in the import graph so both can import without circularity.
"""

from __future__ import annotations

import hashlib
from enum import Enum
from typing import Literal


PARITY_THRESHOLD = 0.15

EDGE_CASE_DIMENSION = "edge_case"
STRUCTURAL_PATTERN_DIMENSION = "structural_pattern"
Dimension = Literal["edge_case", "structural_pattern"]

# Phase 0 (Investigation) — see ARCHITECTURE.md §5.0.
# Flat per-model cap on proposed investigations. Differentiated USE is
# permitted; differentiated BUDGET would rebuild the Haiku→Sonnet→Opus
# pipeline through resource allocation (invariant 1).
INVESTIGATION_CAP = 3

# Temporal grounding zone for the Phase 0 precondition. Anthony's standing
# principle: ground temporally before acting; session texture is not elapsed
# real time. The orchestrator deterministically reads the current time in
# this zone and seeds it as the first feed entry — no authority gradient
# because no model decides it (same constitutional category as §8 prompt
# re-anchoring).
INVESTIGATION_TIMEZONE = "America/New_York"


class ModelId(str, Enum):
    OPUS = "claude-opus-4-7"
    SONNET = "claude-sonnet-4-6"
    HAIKU = "claude-haiku-4-5"


class Phase(int, Enum):
    INDEPENDENT = 1
    CROSS_READING = 2
    DIALOGUE = 3
    SYNTHESIS = 4


class FocusTag(str, Enum):
    CORRECTNESS = "correctness"
    CLARITY = "clarity"
    SPEED = "speed"
    NOVELTY = "novelty"
    ROBUSTNESS = "robustness"
    ELEGANCE = "elegance"


class SynthesisRule(str, Enum):
    """The four named Phase 4 synthesis rules per ARCHITECTURE.md §5.4.

    Closed vocabulary so a SynthesisArtifact's synthesis_rules_applied tuple
    cannot drift into free-form claims. Each rule the engine applies must be
    declared from this set.
    """

    IRREDUCIBILITY_PRESERVATION = "irreducibility_preservation"
    AGREEMENT_ELEVATION = "agreement_elevation"
    DISAGREEMENT_SURFACING = "disagreement_surfacing"
    ATTRIBUTION_PRESERVATION = "attribution_preservation"


class OutputMode(str, Enum):
    """Phase 4 output modes per ARCHITECTURE.md §7.

    - unified: single voice, attribution stripped.
    - layered: per-model sections.
    - annotated: synthesis with inline [O], [S], [H] attribution. Default.
    - transcript: full Phase 1-3 dialogue verbatim, no synthesis.

    The annotation is the proof we did not flatten.
    """

    UNIFIED = "unified"
    LAYERED = "layered"
    ANNOTATED = "annotated"
    TRANSCRIPT = "transcript"


# Default output mode per ARCHITECTURE.md §7. Single source of truth — both
# SynthesisArtifact.output_mode field default and synthesize() parameter default
# reference this constant. Spec revision happens in one place.
DEFAULT_OUTPUT_MODE: OutputMode = OutputMode.ANNOTATED


def claim_id_for(source_model: ModelId, source_phase: Phase, text: str) -> str:
    payload = f"{source_model.value}|{source_phase.value}|{text}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def feed_entry_id_for(entry_type: str, *components: str) -> str:
    """Content-addressed ID for a Phase 0 feed entry.

    Same discipline as claim_id_for — entry_id is derived from canonical
    content so Claim.tool_provenance references are tamper-evident. Two
    feed entries with identical content collapse to one entry_id; that's
    the dedup invariant (rule-based exact union, ARCHITECTURE.md §5.0).
    """
    payload = ("|".join((entry_type, *components))).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]
