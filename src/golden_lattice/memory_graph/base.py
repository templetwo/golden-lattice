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


def claim_id_for(source_model: ModelId, source_phase: Phase, text: str) -> str:
    payload = f"{source_model.value}|{source_phase.value}|{text}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]
