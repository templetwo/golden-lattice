"""TUI color palette — model columns, dialogue channels, dispositions, readings.

Colors chosen for legibility on a standard 256-color terminal. Each ModelId
keeps a consistent column color across the entire layout so a claim authored
by Opus reads as Opus everywhere it appears — in its column, in the loom as
the speaker of a turn, in the trace ledger as the row's author.

Channel colors carry the spec language. Critique is tension. Augment is
additive. Converge is the gold of cross-model agreement — the lattice's
namesake color.
"""

from __future__ import annotations

from golden_lattice.memory_graph.base import ModelId, SeatFamily, seat_family, seat_label
from golden_lattice.memory_graph.metrics import READING_GLOSS  # noqa: F401  (re-export)


FAMILY_COLOR: dict[SeatFamily, str] = {
    "fable": "bright_yellow",
    "opus": "magenta",
    "sonnet": "cyan",
    "haiku": "green",
}

MODEL_COLOR: dict[ModelId, str] = {m: FAMILY_COLOR[seat_family(m)] for m in ModelId}

CHANNEL_COLOR: dict[str, str] = {
    "critique": "red",
    "augment": "blue",
    "converge": "yellow",
}

DISPOSITION_COLOR: dict[str, str] = {
    "present": "bright_green",
    "modified": "yellow",
    "omitted": "bright_red",
}

READING_COLOR: dict[str, str] = {
    "not_recognized": "bright_red",
    "vocabulary_failed": "yellow",
    "peer_divergence": "magenta",
    "low_claim_volume": "dim white",
    "ambiguous": "dim white",
}



def model_label(model: ModelId) -> str:
    """Short human-readable model label, color-tagged for Rich markup."""
    short = seat_label(model)
    return f"[{MODEL_COLOR[model]}]{short}[/{MODEL_COLOR[model]}]"
