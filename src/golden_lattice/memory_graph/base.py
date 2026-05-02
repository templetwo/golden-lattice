"""Leaf primitives for the Memory Graph: enums and content addressing.

Lives below schema.py and tagging.py in the import graph so both can import without circularity.
"""

from __future__ import annotations

import hashlib
from enum import Enum


PARITY_THRESHOLD = 0.15


class ModelId(str, Enum):
    OPUS = "claude-opus-4-7"
    SONNET = "claude-sonnet-4-6"
    HAIKU = "claude-haiku-4-5"


class Phase(int, Enum):
    INDEPENDENT = 1
    CROSS_READING = 2
    DIALOGUE = 3
    SYNTHESIS = 4


def claim_id_for(source_model: ModelId, source_phase: Phase, text: str) -> str:
    payload = f"{source_model.value}|{source_phase.value}|{text}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]
