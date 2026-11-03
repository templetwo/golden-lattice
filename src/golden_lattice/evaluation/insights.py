"""Near-duplicate collapse for distinct-insight counting (eval harness step 3).

Pure functions. No model calls, no semantic judgement: two claims are the
same insight when their normalised token sets overlap at or above a fixed
Jaccard threshold. Greedy clustering in input order keeps the result
deterministic. The verdict band is 0.5 (ARCHITECTURE §5 design, see
harness-design.md); 0.6 and 0.7 are reported as sensitivity bands.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

_TOKEN = re.compile(r"[a-z0-9]+")


def tokens(text: str) -> frozenset[str]:
    """Lowercase alphanumeric tokens. Punctuation and hyphens split words."""
    return frozenset(_TOKEN.findall(text.lower()))


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity. Two empty sets score 0.0, so empty claims never merge."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _check_threshold(threshold: float) -> None:
    if not 0.0 < threshold <= 1.0:
        raise ValueError(f"threshold must be in (0, 1]; got {threshold}")


def collapse(claims: Sequence[str], *, threshold: float) -> list[list[int]]:
    """Group claim indices into clusters of near-duplicates, in input order.

    A claim joins the first existing cluster whose representative (its first
    member) it matches at or above the threshold; otherwise it starts a new
    cluster. Returns lists of original indices.
    """
    _check_threshold(threshold)
    representatives: list[frozenset[str]] = []
    clusters: list[list[int]] = []
    for index, text in enumerate(claims):
        token_set = tokens(text)
        for cluster_index, rep in enumerate(representatives):
            if jaccard(token_set, rep) >= threshold:
                clusters[cluster_index].append(index)
                break
        else:
            representatives.append(token_set)
            clusters.append([index])
    return clusters


def cluster_count(claims: Sequence[str], *, threshold: float) -> int:
    """Number of distinct insights after near-duplicate collapse."""
    return len(collapse(claims, threshold=threshold))
