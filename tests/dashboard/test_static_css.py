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
