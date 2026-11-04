"""Seat identity: legacy ids load; every id maps to a family."""

import pytest

from golden_lattice.memory_graph.base import (
    LEGACY_MODEL_IDS,
    SEAT_FAMILY_ORDER,
    ModelId,
    seat_family,
    seat_label,
)
from golden_lattice.orchestrator import DEFAULT_INVITED_MODELS


def test_legacy_ids_are_members():
    assert ModelId("claude-opus-4-7") is ModelId.LEGACY_OPUS_4_7
    assert ModelId("claude-sonnet-4-6") is ModelId.LEGACY_SONNET_4_6
    assert ModelId("claude-haiku-4-5") is ModelId.LEGACY_HAIKU_4_5
    # The May 2026 ids are always legacy. Later id moves add members here
    # (e.g. claude-haiku-4-5-20251001 once Haiku moves on), so this is a subset.
    assert {ModelId.LEGACY_OPUS_4_7, ModelId.LEGACY_SONNET_4_6, ModelId.LEGACY_HAIKU_4_5} <= LEGACY_MODEL_IDS
    assert all(m.name.startswith("LEGACY_") for m in LEGACY_MODEL_IDS)


def test_every_member_has_a_family():
    for member in ModelId:
        assert seat_family(member) in SEAT_FAMILY_ORDER


def test_family_is_shared_across_generations():
    assert seat_family(ModelId.OPUS) == seat_family(ModelId.LEGACY_OPUS_4_7) == "opus"
    assert seat_family("claude-haiku-4-5-20251001") == "haiku"
    assert seat_label(ModelId.LEGACY_SONNET_4_6) == "SONNET"


def test_unknown_family_refused():
    with pytest.raises(ValueError):
        seat_family("claude-nova-1")


def test_legacy_members_are_never_invited_by_default():
    assert not (set(DEFAULT_INVITED_MODELS) & LEGACY_MODEL_IDS)
