"""Task 11 — executable documentation / constitutional surface invariants.

Prevents the public docs from drifting away from the live roster, closed
synthesis vocabulary, and the constrained test command the project actually
runs. Production behavior is untouched; these checks read files and enums only.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from golden_lattice.memory_graph.base import (
    CommitmentState,
    ModelId,
    SynthesisRule,
)
from golden_lattice.orchestrator import DEFAULT_INVITED_MODELS

REPO_ROOT = Path(__file__).resolve().parents[1]

PRIMARY_DOCS = (
    REPO_ROOT / "README.md",
    REPO_ROOT / "ARCHITECTURE.md",
)

# Active human-facing seat names (not provider endpoint strings).
ACTIVE_SEAT_DISPLAY = ("Fable", "Opus", "Sonnet", "Haiku")

ACTIVE_DOC_RELPATHS = (
    "README.md",
    "ARCHITECTURE.md",
    "CONTRIBUTING.md",
    "experiments/README.md",
    "experiments/protocol.md",
    "experiments/baselines/README.md",
    "experiments/reports/README.md",
)

# Stale claims that must not reappear in active documentation.
STALE_DOC_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "stale_test_count_69",
        re.compile(r"\b69\s+tests\b", re.IGNORECASE),
    ),
    (
        "operational_layer_absent",
        re.compile(
            r"(what is not in this repo yet|these will ship next|"
            r"remaining layers are forthcoming|"
            r"operational layer\.?\s+phase 2 cross-reading wire format)",
            re.IGNORECASE,
        ),
    ),
    (
        "triadic_default_claim",
        re.compile(
            r"(triadic[- ]only|default\s+triadic|three-seat\s+default|"
            r"default\s+roster\s+is\s+triadic|"
            r"three\s+peers\s+only|"
            r"opus,\s*sonnet,\s*and\s*haiku\s+as\s+the\s+three\s+peers)",
            re.IGNORECASE,
        ),
    ),
)

# Constrained pytest invocation documented for local/CI parity.
CONSTRAINED_PYTEST = "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Active roster names documented
# ---------------------------------------------------------------------------


def test_default_invited_models_match_active_four_seat_roster() -> None:
    assert DEFAULT_INVITED_MODELS == (
        ModelId.FABLE,
        ModelId.OPUS,
        ModelId.SONNET,
        ModelId.HAIKU,
    )


@pytest.mark.parametrize("doc_path", PRIMARY_DOCS, ids=lambda p: p.name)
def test_primary_docs_name_active_roster_seats(doc_path: Path) -> None:
    assert doc_path.is_file(), f"missing primary doc: {doc_path}"
    text = _read(doc_path)
    missing = [name for name in ACTIVE_SEAT_DISPLAY if name.lower() not in text.lower()]
    assert not missing, f"{doc_path.name} must document active seats {missing}"


# ---------------------------------------------------------------------------
# No stale triadic-only / default-three-seat claims
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rel", ACTIVE_DOC_RELPATHS)
def test_active_docs_have_no_stale_triadic_or_absentee_claims(rel: str) -> None:
    path = REPO_ROOT / rel
    if not path.is_file():
        pytest.skip(f"optional active doc absent: {rel}")
    text = _read(path)
    hits: list[str] = []
    for tag, pattern in STALE_DOC_PATTERNS:
        match = pattern.search(text)
        if match:
            hits.append(f"{tag}: {match.group(0)!r}")
    assert not hits, f"{rel} still contains stale documentation claims: {hits}"


# ---------------------------------------------------------------------------
# Documented constrained pytest command
# ---------------------------------------------------------------------------


def test_documented_constrained_pytest_command_is_present_and_accurate() -> None:
    """README (and CI) must advertise the constrained suite command.

    Accuracy means:
    - the exact constrained form appears in README.md
    - pyproject.toml routes pytest at the project test suite (testpaths=tests)
    - CI invokes pytest under the same plugin-autoload guard
    """
    readme = _read(REPO_ROOT / "README.md")
    assert CONSTRAINED_PYTEST in readme, (
        f"README.md must document the constrained pytest command: {CONSTRAINED_PYTEST!r}"
    )

    pyproject = tomllib.loads(_read(REPO_ROOT / "pyproject.toml"))
    pytest_opts = pyproject.get("tool", {}).get("pytest", {}).get("ini_options", {})
    testpaths = pytest_opts.get("testpaths")
    assert testpaths == ["tests"] or testpaths == "tests", (
        f"pyproject.toml tool.pytest.ini_options.testpaths must point at tests/, got {testpaths!r}"
    )

    ci_path = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    assert ci_path.is_file(), "CI workflow missing"
    ci = _read(ci_path)
    assert "PYTEST_DISABLE_PLUGIN_AUTOLOAD" in ci
    assert re.search(r"\bpytest\b", ci), "CI must run pytest"
    # Bare `pytest -q` under the env guard is the constrained suite; the
    # documented README form is the local equivalent.
    assert "pytest -q" in ci or CONSTRAINED_PYTEST in ci


# ---------------------------------------------------------------------------
# Synthesis rule / commitment state names match closed enums
# ---------------------------------------------------------------------------


def test_architecture_documents_every_synthesis_rule_enum_value() -> None:
    arch = _read(REPO_ROOT / "ARCHITECTURE.md")
    missing = [rule.value for rule in SynthesisRule if rule.value not in arch]
    assert not missing, (
        "ARCHITECTURE.md must name every SynthesisRule value from "
        f"src/golden_lattice/memory_graph/base.py; missing: {missing}"
    )


def test_architecture_documents_every_commitment_state_enum_value() -> None:
    arch = _read(REPO_ROOT / "ARCHITECTURE.md")
    missing = [state.value for state in CommitmentState if state.value not in arch]
    assert not missing, (
        "ARCHITECTURE.md must name every CommitmentState value from "
        f"src/golden_lattice/memory_graph/base.py; missing: {missing}"
    )


def test_architecture_synthesis_rule_tokens_stay_inside_closed_enum() -> None:
    """Any `snake_case` token presented as a synthesis rule id must be real.

    Looks for backtick-wrapped identifiers that equal a known rule-shaped
    pattern near the Phase 4 / synthesis vocabulary surface. Unknown rule ids
    are drift.
    """
    arch = _read(REPO_ROOT / "ARCHITECTURE.md")
    allowed = {rule.value for rule in SynthesisRule}
    # Capture backtick tokens that look like synthesis rule identifiers.
    candidates = set(re.findall(r"`([a-z]+(?:_[a-z]+)+)`", arch))
    rule_shaped = {
        tok
        for tok in candidates
        if tok.endswith(
            (
                "_preservation",
                "_elevation",
                "_surfacing",
            )
        )
        or tok
        in {
            "irreducibility_preservation",
            "agreement_elevation",
            "disagreement_surfacing",
            "attribution_preservation",
        }
    }
    unknown = sorted(rule_shaped - allowed)
    assert not unknown, (
        f"ARCHITECTURE.md names synthesis-rule tokens outside SynthesisRule: {unknown}"
    )


def test_architecture_commitment_state_tokens_stay_inside_closed_enum() -> None:
    arch = _read(REPO_ROOT / "ARCHITECTURE.md")
    allowed = {state.value for state in CommitmentState}
    # Only consider tokens that appear inside backticks as closed-vocab ids.
    candidates = set(re.findall(r"`([a-z_]+)`", arch))
    state_shaped = candidates & {
        "proposed",
        "defended",
        "challenged",
        "revised",
        "withdrawn",
        "reaffirmed",
        "unresolved",
        # trap: invented extras would also match if listed in backticks
    }
    # Expand: any backtick token that matches known commitment vocabulary pattern
    # and is used as a state label in the commitment section.
    commitment_section = arch
    if "### Commitment observations" in arch:
        commitment_section = arch.split("### Commitment observations", 1)[1]
    section_tokens = set(re.findall(r"`([a-z_]+)`", commitment_section))
    # Tokens that look like commitment states: pure lowercase words used as states.
    possible_states = {
        tok
        for tok in section_tokens
        if tok
        in {
            *allowed,
            "committed",  # common false friend
            "accepted",
            "rejected",
            "pending",
            "active",
            "closed",
        }
    }
    unknown = sorted(possible_states - allowed)
    assert not unknown, (
        "ARCHITECTURE.md commitment section names state tokens outside "
        f"CommitmentState: {unknown}"
    )
    # And every enum value is represented (belt + suspenders with the other test).
    missing = sorted(allowed - section_tokens)
    assert not missing, (
        "ARCHITECTURE.md commitment section must backtick-name every "
        f"CommitmentState value; missing: {missing}"
    )
