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

from golden_lattice.memory_graph.base import ModelId


MODEL_COLOR: dict[ModelId, str] = {
    ModelId.OPUS: "magenta",
    ModelId.SONNET: "cyan",
    ModelId.HAIKU: "green",
}

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

READING_GLOSS: dict[str, str] = {
    "not_recognized": "Genuine recognition asymmetry — peers did not see these claims as dimension-relevant.",
    "vocabulary_failed": "The closed vocabulary did not cover these contributions. Vocab needs work, not the model.",
    "peer_divergence": "Peers split per-claim on this model's contributions. Neither vocab failure nor under-contribution.",
    "low_claim_volume": "Too few claims to compute meaningfully. Measurement-underdetermined; not a parity verdict.",
    "ambiguous": "Histogram does not match a clean pattern. More sessions needed before declaring.",
}


def model_label(model: ModelId) -> str:
    """Short human-readable model label, color-tagged for Rich markup."""
    short = {
        ModelId.OPUS: "OPUS",
        ModelId.SONNET: "SONNET",
        ModelId.HAIKU: "HAIKU",
    }[model]
    return f"[{MODEL_COLOR[model]}]{short}[/{MODEL_COLOR[model]}]"
