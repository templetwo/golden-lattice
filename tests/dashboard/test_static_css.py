"""Reading text must wrap, never clip: panes hide horizontal overflow, so a
long URL in a reason would otherwise be cut off (spec §2 anti-goal)."""

import re
from pathlib import Path

CSS = re.sub(r"/\*.*?\*/", "", (Path(__file__).resolve().parents[2] / "src/golden_lattice/dashboard/static/app.css").read_text(), flags=re.S)


def test_reading_text_wraps_long_tokens():
    rule = re.search(r"([^{}]+)\{[^{}]*overflow-wrap:\s*anywhere[^{}]*\}", CSS)
    assert rule, "no overflow-wrap: anywhere rule"
    selectors = {s.strip() for s in rule.group(1).split(",")}
    for needed in (".voice p", ".row .txt", ".turn p", ".line", ".note", ".hedge", ".refl"):
        assert needed in selectors, needed


def test_writing_strip_is_bounded_and_scrolls():
    """Twelve concurrent cards must not push the reading panes off screen
    (review of 34be2a7, finding 2)."""
    rule = re.search(r"\.writing\s*\{([^}]*)\}", CSS)
    assert rule and "max-height" in rule.group(1)
    cards = re.search(r"\.writing-cards\s*\{([^}]*)\}", CSS)
    assert cards and "overflow-y" in cards.group(1)
    assert re.search(r"\.writing\.expanded\s*\{[^}]*max-height", CSS), "an expanded state exists"
