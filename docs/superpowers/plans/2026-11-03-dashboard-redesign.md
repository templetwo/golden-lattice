# Dashboard Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the web dashboard as two pages, the Record (library plus cross-session strip) and the Session (rail-and-pin replay centered on a claim table that shows where the seats disagreed and why), and make every persisted session load again.

**Architecture:** Python stays the canonical layer: `memory_graph/base.py` gains legacy seat ids and a family helper; `dashboard/normalize.py` grows into the single render shape; a new `dashboard/record.py` sums across sessions; `dashboard/server.py` serves two routes, static files, and a sessions endpoint that names what it could not load. The frontend is vanilla ES modules served by aiohttp: a router, a seats module, a Record renderer, a Session renderer with rail and pin, a beat stepper for replay, and a live module that applies WebSocket events per section and hydrates from disk on completion.

**Tech Stack:** Python 3.11+, pydantic 2, aiohttp, pytest; browser-native JavaScript modules, CSS, inline SVG; node only to run the vendored palette validator.

**Spec:** `docs/superpowers/specs/2026-11-03-dashboard-redesign-design.md`

## Global Constraints

- Renderers compute no metrics and make no judgments; every number comes from `normalize.py`, `record.py`, `metrics.compute_parity_shares`, or `metrics.interpret_parity_flags` (spec §3).
- `events.py`, the orchestrator, synthesis, and the TUI's behavior are unchanged except the family-keyed lookups in Task 1 and the gloss move in Task 2 (spec §2, §3).
- Legacy ids `claude-opus-4-7`, `claude-sonnet-4-6`, `claude-haiku-4-5` load; they are never in `DEFAULT_INVITED_MODELS` (spec §5.1).
- Seat identity keys on family (`fable`, `opus`, `sonnet`, `haiku`) everywhere a seat is labelled, colored, or aggregated; the exact id is shown as provenance (spec §5.2).
- Never summarize, truncate, or paraphrase a disagreement reason, critique, omission reason, or modified text on the Session page (spec §2 anti-goals).
- A flag always renders with its reading and gloss; never a bare "FLAGGED" (spec §2).
- Unloadable session files are reported, never hidden (spec §5.4).
- No grid, list, or table assumes three seats (spec §2).
- No JavaScript build step, framework, or package manager; fonts stay as progressive enhancement (spec §2, §3).
- Dark surface `#0c0d11`; seat colors must pass the vendored validator with `--mode dark --surface "#0c0d11"` with no FAIL line; seat names are printed beside every colored mark (spec §7).
- The prompt is never display type: 15px on one line in the Session masthead, 13.5px in the Record's rows; nothing on either page is set above 22px, and only the Record's flag count reaches that (spec §7).
- `pytest` stays green (441 passed, 11 skipped at base 4a5962c); commit after every task with the Co-Authored-By line used on f32e396.
- `.superpowers/` and `.claude/launch.json` are never committed (spec §8).

## Review Focus

1. A session with `phase_0 is None` (every May run): 00 renders the "skipped" state and the Record row still lists it. Test pinned in Task 4 (`test_normalize_phase0_none_passes_through`) and Task 8 step 10.
2. A dyad session (`metrics is None` by rule, not by age): the Record row says parity is undefined, the chart and flags exclude it, and `metrics_recomputed` is false. Tests pinned in Task 4 (`test_normalize_dyad_metrics_none_not_recomputed`) and Task 5 (`test_record_dyad_excluded_from_series_and_flags`).
3. A synthesis output line that matches no claim, and a claim whose text appears twice: no link on the unmatched line, the first unmatched claim wins the duplicate. Test pinned in Task 4 (`test_synthesis_lines_unmatched_and_duplicate`).
4. A run with zero disputes: the default filter is empty, so the page says "no reader disputed anything in this run" and shows all claims. Test pinned in Task 4 (`test_normalize_zero_disputes_stats`) and Task 8 step 10.
5. `GET /sessions/{id}` for an unknown id returns JSON 404; an id containing `..` or `/` returns 400, never a 500. Test pinned in Task 6 (`test_session_endpoint_404_and_400`).

---

## File structure

Modified:
- `src/golden_lattice/memory_graph/base.py` — legacy members, `SeatFamily`, `seat_family`, `seat_label`, `SEAT_FAMILY_ORDER`, `LEGACY_MODEL_IDS`.
- `src/golden_lattice/synthesis/attribution.py` — marker by family.
- `src/golden_lattice/tui/colors.py` — color and label by family; imports `READING_GLOSS`.
- `src/golden_lattice/tui/renderer.py:128,436` — short names via `seat_label`.
- `src/golden_lattice/memory_graph/metrics.py` — `READING_GLOSS` lives here.
- `src/golden_lattice/dashboard/normalize.py` — the full render shape; `load_normalized`.
- `src/golden_lattice/dashboard/server.py` — routes, bundle cache, static files.
- `src/golden_lattice/dashboard/static/index.html` — rewritten as the shell.
- `scripts/run_lattice_live.py` — three live fixes.
- `README.md` — routes and running section.

Created:
- `src/golden_lattice/dashboard/record.py` — cross-session sums.
- `src/golden_lattice/dashboard/static/app.css`
- `src/golden_lattice/dashboard/static/app.js` — router and boot.
- `src/golden_lattice/dashboard/static/js/seats.js` — family lookup, palette, labels.
- `src/golden_lattice/dashboard/static/js/record.js`
- `src/golden_lattice/dashboard/static/js/session.js` — rail, pin, five sections.
- `src/golden_lattice/dashboard/static/js/beats.js` — replay stepping and transport.
- `src/golden_lattice/dashboard/static/js/live.js` — WebSocket, per-section apply, hydration.
- `scripts/validate_palette.js` — vendored validator.
- `tests/memory_graph/test_seats.py`
- `tests/dashboard/conftest.py` — `make_full_session`.
- `tests/dashboard/test_normalize.py`
- `tests/dashboard/test_record.py`
- `tests/fixtures/session_20260504_000000_legacy01.session.json`

---

### Task 1: Legacy seat ids and seat families

**Files:**
- Modify: `src/golden_lattice/memory_graph/base.py:34-40`
- Modify: `src/golden_lattice/synthesis/attribution.py:108-114,146-153`
- Modify: `src/golden_lattice/tui/colors.py:18-23,54-62`
- Modify: `src/golden_lattice/tui/renderer.py:128,436`
- Test: `tests/memory_graph/test_seats.py`, `tests/synthesis/test_attribution.py`

**Interfaces:**
- Produces, in `base.py`:
  - `ModelId.LEGACY_OPUS_4_7 = "claude-opus-4-7"`, `ModelId.LEGACY_SONNET_4_6 = "claude-sonnet-4-6"`, `ModelId.LEGACY_HAIKU_4_5 = "claude-haiku-4-5"`
  - `SeatFamily = Literal["fable", "opus", "sonnet", "haiku"]`
  - `SEAT_FAMILY_ORDER: tuple[SeatFamily, ...] = ("fable", "opus", "sonnet", "haiku")`
  - `LEGACY_MODEL_IDS: frozenset[ModelId]` holding the three legacy members
  - `seat_family(model: ModelId | str) -> SeatFamily` — the family name that is a substring of the id; raises `ValueError` for an id containing none of the four names
  - `seat_label(model: ModelId | str) -> str` — `seat_family(model).upper()`
- Later tasks use `seat_family`, `seat_label`, `SEAT_FAMILY_ORDER`, `LEGACY_MODEL_IDS`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/memory_graph/test_seats.py
import pytest
from golden_lattice.memory_graph.base import (
    LEGACY_MODEL_IDS, SEAT_FAMILY_ORDER, ModelId, seat_family, seat_label,
)
from golden_lattice.orchestrator import DEFAULT_INVITED_MODELS

def test_legacy_ids_are_members():
    assert ModelId("claude-opus-4-7") is ModelId.LEGACY_OPUS_4_7
    assert ModelId("claude-sonnet-4-6") is ModelId.LEGACY_SONNET_4_6
    assert ModelId("claude-haiku-4-5") is ModelId.LEGACY_HAIKU_4_5
    assert LEGACY_MODEL_IDS == frozenset(
        {ModelId.LEGACY_OPUS_4_7, ModelId.LEGACY_SONNET_4_6, ModelId.LEGACY_HAIKU_4_5}
    )

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
```

Add to `tests/synthesis/test_attribution.py`:

```python
def test_marker_for_legacy_ids_follows_family():
    from golden_lattice.synthesis.attribution import _attribution_marker
    assert _attribution_marker(ModelId.LEGACY_OPUS_4_7) == "[O]"
    assert _attribution_marker(ModelId.LEGACY_HAIKU_4_5) == "[H]"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/memory_graph/test_seats.py tests/synthesis/test_attribution.py -q`
Expected: FAIL with `AttributeError: LEGACY_OPUS_4_7` / `ImportError: seat_family`

- [ ] **Step 3: Add the members and helpers to `base.py`**

Add the three legacy members after `HAIKU` with a comment that they exist so persisted sessions validate and are never invited. Implement `seat_family` by checking the four family names against `str(model.value if isinstance(model, ModelId) else model)` in `SEAT_FAMILY_ORDER` order.

- [ ] **Step 4: Key attribution and TUI lookups on family**

In `attribution.py` replace `_MARKER_BY_MODEL` with `_MARKER_BY_FAMILY: dict[SeatFamily, str] = {"fable": "[F]", "opus": "[O]", "sonnet": "[S]", "haiku": "[H]"}` and make `_attribution_marker` return `_MARKER_BY_FAMILY[seat_family(model)]`. In `tui/colors.py` define `FAMILY_COLOR: dict[SeatFamily, str]` with the four existing colors and rebuild `MODEL_COLOR = {m: FAMILY_COLOR[seat_family(m)] for m in ModelId}` so existing imports keep working; `model_label` uses `seat_label`. In `renderer.py` lines 128 and 436 replace the inline dicts with `seat_label(model)`.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all prior tests pass plus the new ones; `test_latest_models_and_streaming.py` still passes (it checks the four current ids, which are unchanged).

- [ ] **Step 6: Legacy fixture**

The on-disk legacy fixture needs the session builder from Task 4, so it is generated and tested in Task 4 step 6. Nothing to do here; the real-sessions test in the next step covers loading until then.

- [ ] **Step 7: Add the real-sessions loading test**

Append to `tests/memory_graph/test_store.py`:

```python
def test_every_persisted_session_in_repo_sessions_dir_loads():
    sessions_dir = Path(__file__).resolve().parents[2] / "sessions"
    store = JsonFileSessionStore(sessions_dir)
    ids = store.list_sessions() if sessions_dir.exists() else ()
    if not ids:
        pytest.skip("no persisted sessions on this machine")
    for session_id in ids:
        store.load(session_id)
```

Run: `.venv/bin/python -m pytest tests/memory_graph/test_store.py -q` from the worktree (skips) and with `sessions/` symlinked or copied from the main checkout (passes 12 of 12).

- [ ] **Step 8: Commit**

```bash
git add src/golden_lattice/memory_graph/base.py src/golden_lattice/synthesis/attribution.py src/golden_lattice/tui/colors.py src/golden_lattice/tui/renderer.py tests/memory_graph/test_seats.py tests/synthesis/test_attribution.py tests/memory_graph/test_store.py
git commit -m "Seats: legacy ids load, identity keys on family"
```

---

### Task 2: Reading glosses in the canonical layer

**Files:**
- Modify: `src/golden_lattice/memory_graph/metrics.py` (after `FlagInterpretation`)
- Modify: `src/golden_lattice/tui/colors.py:38-45`
- Test: `tests/memory_graph/test_metrics.py`

**Interfaces:**
- Produces: `READING_GLOSS: dict[FlagReading, str]` in `metrics.py` with the five strings currently in `tui/colors.py` verbatim; `tui/colors.py` re-exports it (`from golden_lattice.memory_graph.metrics import READING_GLOSS`).

- [ ] **Step 1: Write the failing test**

```python
def test_reading_gloss_covers_every_reading():
    from typing import get_args
    from golden_lattice.memory_graph.metrics import READING_GLOSS, FlagReading
    assert set(READING_GLOSS) == set(get_args(FlagReading))
    assert "not a parity verdict" in READING_GLOSS["low_claim_volume"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/memory_graph/test_metrics.py::test_reading_gloss_covers_every_reading -q`
Expected: FAIL with `ImportError: READING_GLOSS`

- [ ] **Step 3: Move the dict**

Cut the five-entry dict from `tui/colors.py` into `metrics.py`, typed `dict[FlagReading, str]`; in `tui/colors.py` import it under the same name.

- [ ] **Step 4: Run the suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/golden_lattice/memory_graph/metrics.py src/golden_lattice/tui/colors.py tests/memory_graph/test_metrics.py
git commit -m "Metrics: reading glosses live beside the readings"
```

---

### Task 3: Vendor the palette validator and pin the seat palette

**Files:**
- Create: `scripts/validate_palette.js` (copy of the bundled dataviz skill script, with a two-line header naming its origin)
- Create: `src/golden_lattice/dashboard/static/app.css` (tokens only for now)

**Interfaces:**
- Produces, in `app.css` `:root`: `--seat-fable: #d76549; --seat-opus: #8c42aa; --seat-sonnet: #01a2c5; --seat-haiku: #348f4f; --critique: #e35d4c; --augment: #6f94e0; --converge: #e0b64a; --present: #45c28a; --bg: #0c0d11; --panel: #0f1117; --panel-2: #14161d; --border: #242832; --border-soft: #1c1f27; --text: #d7dbe4; --text-bright: #eef0f4; --text-mid: #aab0bc; --dim: #707682; --dimmer: #4b505b;` and the two font stacks from the current `index.html`.
- These seat values were found by the planner on 2026-11-03 and pass the validator (deutan ΔE 14.2, tritan 10.3 on the worst adjacent pair; normal-vision floor 16.5). The builder may re-step them but must re-run the check.

- [ ] **Step 1: Copy the validator**

Source on this machine: `/private/tmp/claude-501/bundled-skills/2.1.309/f7075526e8600bc026b43a0ab00c28df/dataviz/scripts/validate_palette.js`. Copy to `scripts/validate_palette.js` and prepend a comment naming the origin and the command below.

- [ ] **Step 2: Run the validator on the pinned palette**

Run: `node scripts/validate_palette.js "#d76549,#8c42aa,#01a2c5,#348f4f" --mode dark --surface "#0c0d11"`
Expected: no `[FAIL]` line; the summary line does not say FAILED. Paste the report into the commit message body.

- [ ] **Step 3: Write `app.css` tokens**

Only `:root` tokens, `body` background and font stacks, and the scrollbar rules from the current `index.html` lines 44-52. Everything else arrives in Tasks 7 and 8.

- [ ] **Step 4: Commit**

```bash
git add scripts/validate_palette.js src/golden_lattice/dashboard/static/app.css
git commit -m "Dashboard: vendored palette validator and validated seat tokens"
```

---

### Task 4: The normalized session shape

**Files:**
- Create: `tests/dashboard/conftest.py`
- Modify: `src/golden_lattice/dashboard/normalize.py`
- Test: `tests/dashboard/test_normalize.py`
- Create: `tests/fixtures/session_20260504_000000_legacy01.session.json`

**Interfaces:**
- Consumes: `seat_family`, `seat_label`, `LEGACY_MODEL_IDS` (Task 1); `interpret_parity_flags`, `compute_parity_shares`, `READING_GLOSS` (Task 2).
- Produces, in `normalize.py`:
  - `normalize_session(session: Session) -> dict[str, Any]` returning the existing keys (`id, title, prompt, models, phase0, phase1, reflections, crossreadings, turns, synthesis, metrics, elevations`) plus:
    - `seats: list[{"id": str, "family": str, "label": str, "legacy": bool, "focus": str, "conf": float, "claim_count": int}]` in `models_invited` order
    - `claims: list[dict]` in Phase 1 order (per seat in `models_invited` order, then claim order) with keys `id, author, author_family, text, index, agreed_by: list[str], disagreements: list[{"reader","reason"}], critiques: list[{"speaker","turn_id","content"}], disputed_by: list[str], disposition, modified_text, omission_reason, synthesis_line: int|None, disputed_hedge: str|None, surfaced_note: str|None, elevated: bool`
    - `synthesis_lines: list[{"index": int, "tag": str, "text": str, "hedge": str|None, "claim_id": str|None}]`
    - `stats: {"claims","disputed","both","elevated","omitted","modified","critiques","augments","converges","missing"}`
    - `pairs: list[{"reader","target","disputed","endorsed","missing"}]` one per `CrossReading`
    - `interpretations: list[{"model","family","dimension","reading","gloss","share","histogram_n_zero","histogram_n_one","histogram_n_two","other_only_entries","total_claims"}]`
    - `metrics_recomputed: bool`, `created_at: str` (ISO)
  - `@dataclass NormalizedBundle: sessions: list[dict]; unloadable: list[{"id": str, "error": str}]`
  - `load_normalized(sessions_dir: Path, *, limit: int | None = None) -> NormalizedBundle`; `normalized_sessions` keeps its signature and returns `load_normalized(...).sessions`.
- Produces, in `tests/dashboard/session_builder.py`: `make_full_session(*, legacy: bool = False, persist_metrics: bool = True, dyad: bool = False, session_id: str = "session_20260504_120000_abcd1234", created_offset_days: int = 0) -> Session`; and in `tests/dashboard/conftest.py` a pytest fixture named `make_full_session` that returns that function, so tests take it as an argument and the one-off in step 6 imports it as a plain function.

- [ ] **Step 1: Write the builder in `tests/dashboard/session_builder.py` and the fixture in `conftest.py`**

A triad with `OPUS, SONNET, HAIKU` (the legacy trio when `legacy=True`), `NOW = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)`, `prompt_hash="h"` everywhere, `synthesis_rules_applied=tuple(SynthesisRule)`, and exactly this content, so the tests below have known values:

- Phase 1 claims, texts `"opus one"`, `"opus two"`, `"sonnet one"`, `"sonnet two"`, `"haiku one"`, `"haiku two"`, ids via `claim_id_for(model, Phase.INDEPENDENT, text)`; one self-reflection per seat with strongest = first claim, weakest = second, justification `"why this tag"`; focus `FocusTag.CORRECTNESS`, confidence `0.8`; `generation_started_at = NOW`, completed `NOW + 1s`.
- Phase 2 (six readings): SONNET→OPUS agrees `opus two`, disagrees `opus one` with reason `"sonnet disagrees with opus one"`, missing one Claim by SONNET at `Phase.CROSS_READING` with text `"sonnet says opus missed this"`; HAIKU→OPUS agrees `opus one`, `opus two`; OPUS→SONNET disagrees `sonnet one` reason `"opus disagrees with sonnet one"`; HAIKU→SONNET disagrees `sonnet one` reason `"haiku disagrees with sonnet one"`; OPUS→HAIKU agrees `haiku one`; SONNET→HAIKU agrees `haiku one`, `haiku two`.
- Phase 3: `t1` OPUS critique → SONNET, targets `(sonnet one,)`, content `"opus critiques sonnet one in dialogue"`; `t2` HAIKU critique → OPUS, targets `(opus two,)`, content `"haiku critiques opus two"`; `t3` SONNET converge → OPUS targets `(opus two,)` content `"agree"`; `t4` HAIKU converge → OPUS targets `(opus two,)` content `"agree too"`; `t5` SONNET augment, no target, content `"sonnet adds"`.
- Phase 4: `output` is five lines joined by `\n`: `"[O] opus one"`, `"[O] opus two"`, `"[S] sonnet one [DISPUTED — claude-opus-5: 'opus disagrees with sonnet one'; claude-haiku-4-5-20251001: 'haiku disagrees with sonnet one']"` (legacy ids in the hedge when `legacy=True`), `"[H] haiku one"`, `"[H] haiku two"`; `claim_trace`: opus one present, opus two present, sonnet one modified with `modified_text` equal to that third line without its `[S] ` prefix, sonnet two omitted with `omission_reason="redundant with opus two"`, haiku one present, haiku two present; `elevations=(Elevation(claim_ids=(opus two,), converge_turn_ids=("t3","t4")),)`; `surfaced_disagreements=(SurfacedDisagreement(claim_ids=(sonnet one,), note="opus and haiku both push back on sonnet one"),)`; `synthesis_rules_applied` the engine's full tuple; `output_mode` default.
- `metrics` when `persist_metrics`: `SessionMetrics(distinct_claim_share={O: 0.34, S: 0.33, H: 0.33}, edge_case_coverage_share={O: 0.5, S: 0.1, H: 0.4}, structural_pattern_share={O: 0.4, S: 0.3, H: 0.3})`; otherwise `None`.
- `created_at = NOW + timedelta(days=created_offset_days)`.
- When `dyad=True`: invite only OPUS and SONNET; keep the readings SONNET→OPUS and OPUS→SONNET, the turns `t1` and `t5`, no elevation, no surfaced disagreement; `sonnet one` is `present` with no hedge; the output is the four lines for the four remaining claims; `metrics=None` regardless of `persist_metrics`.

- [ ] **Step 2: Write the failing tests**

```python
# tests/dashboard/test_normalize.py
from golden_lattice.dashboard.normalize import normalize_session, load_normalized
from golden_lattice.memory_graph.base import ModelId

def _by_text(n): return {c["text"]: c for c in n["claims"]}

def test_claims_carry_readers_and_fate(make_full_session):
    n = normalize_session(make_full_session())
    c = _by_text(n)
    assert c["sonnet one"]["disputed_by"] == ["claude-opus-5", "claude-haiku-4-5-20251001"]
    assert [d["reason"] for d in c["sonnet one"]["disagreements"]] == [
        "opus disagrees with sonnet one", "haiku disagrees with sonnet one"]
    assert c["sonnet one"]["critiques"] == [
        {"speaker": "claude-opus-5", "turn_id": "t1", "content": "opus critiques sonnet one in dialogue"}]
    assert c["sonnet one"]["disposition"] == "modified"
    assert c["sonnet one"]["disputed_hedge"].startswith("[DISPUTED")
    assert c["sonnet one"]["surfaced_note"] == "opus and haiku both push back on sonnet one"
    assert c["opus two"]["disputed_by"] == ["claude-haiku-4-5-20251001"]   # critique only
    assert c["opus two"]["agreed_by"] == ["claude-sonnet-5", "claude-haiku-4-5-20251001"]
    assert c["opus two"]["elevated"] is True
    assert c["sonnet two"]["disposition"] == "omitted"
    assert c["sonnet two"]["omission_reason"] == "redundant with opus two"

def test_stats_count_claims_not_events(make_full_session):
    s = normalize_session(make_full_session())["stats"]
    assert s == {"claims": 6, "disputed": 3, "both": 1, "elevated": 1, "omitted": 1,
                 "modified": 1, "critiques": 2, "augments": 1, "converges": 2, "missing": 1}

def test_synthesis_lines_link_back(make_full_session):
    n = normalize_session(make_full_session()); c = _by_text(n)
    assert c["opus one"]["synthesis_line"] == 0
    assert c["sonnet one"]["synthesis_line"] == 2          # matched through modified_text
    assert c["sonnet two"]["synthesis_line"] is None
    assert n["synthesis_lines"][2]["tag"] == "S" and n["synthesis_lines"][2]["hedge"].startswith("[DISPUTED")

def test_synthesis_lines_unmatched_and_duplicate(make_full_session):
    session = make_full_session()
    out = session.phase_4.output + "\n[O] a line the engine added"
    session = session.model_copy(update={"phase_4": session.phase_4.model_copy(update={"output": out})})
    n = normalize_session(session)
    assert n["synthesis_lines"][5]["claim_id"] is None

def test_pairs_one_per_cross_reading(make_full_session):
    pairs = {(p["reader"], p["target"]): p for p in normalize_session(make_full_session())["pairs"]}
    assert len(pairs) == 6
    assert pairs[("claude-haiku-4-5-20251001", "claude-opus-5")] == {
        "reader": "claude-haiku-4-5-20251001", "target": "claude-opus-5", "disputed": 0, "endorsed": 2, "missing": 0}
    assert pairs[("claude-sonnet-5", "claude-opus-5")]["missing"] == 1

def test_interpretations_carry_reading_and_gloss(make_full_session):
    n = normalize_session(make_full_session())
    assert n["metrics_recomputed"] is False
    assert len(n["interpretations"]) == 1
    i = n["interpretations"][0]
    assert (i["family"], i["dimension"], i["reading"], i["share"]) == ("sonnet", "edge_case_coverage_share", "not_recognized", 0.1)
    assert i["gloss"].startswith("Genuine recognition asymmetry")

def test_metrics_recomputed_when_absent(make_full_session):
    n = normalize_session(make_full_session(persist_metrics=False))
    assert n["metrics_recomputed"] is True and n["metrics"] is not None

def test_normalize_dyad_metrics_none_not_recomputed(make_full_session):
    n = normalize_session(make_full_session(dyad=True))
    assert n["metrics"] is None and n["metrics_recomputed"] is False
    assert n["interpretations"] == [] and n["stats"]["claims"] == 4

def test_seats_in_invited_order_with_family_and_legacy(make_full_session):
    seats = normalize_session(make_full_session(legacy=True))["seats"]
    assert [s["family"] for s in seats] == ["opus", "sonnet", "haiku"]
    assert seats[0]["id"] == "claude-opus-4-7" and seats[0]["legacy"] is True and seats[0]["label"] == "OPUS"

def test_normalize_phase0_none_passes_through(make_full_session):
    assert normalize_session(make_full_session())["phase0"] is None

def test_normalize_zero_disputes_stats(make_full_session):
    session = make_full_session()
    session = session.model_copy(update={"phase_2": tuple(cr.model_copy(update={"disagreements": ()}) for cr in session.phase_2),
                                         "phase_3": tuple(t for t in session.phase_3 if t.channel != "critique")})
    assert normalize_session(session)["stats"]["disputed"] == 0

def test_load_normalized_reports_unloadable(tmp_path, make_full_session):
    from golden_lattice.memory_graph.store import JsonFileSessionStore
    JsonFileSessionStore(tmp_path).save(make_full_session())
    (tmp_path / "session_bad.session.json").write_text("{}")
    bundle = load_normalized(tmp_path)
    assert [s["id"] for s in bundle.sessions] == ["session_20260504_120000_abcd1234"]
    assert bundle.unloadable[0]["id"] == "session_bad" and bundle.unloadable[0]["error"]
```

`compute_parity_shares` returns `None` for a dyad, so `metrics_recomputed` stays `False` and `interpretations` is empty.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/dashboard/test_normalize.py -q`
Expected: FAIL (KeyError `claims` / ImportError `load_normalized`)

- [ ] **Step 4: Implement the shape in `normalize.py`**

Add private builders `_claims(session)`, `_synthesis_lines(output)`, `_stats(claims, session)`, `_pairs(session)`, `_interpretations(session, metrics_model)`, `_seats(session)` and compose them in `normalize_session`. Rules the signature does not determine:
- `disputed_by` is the ordered union of Phase 2 disagreeing readers and Phase 3 critique speakers, distinct, in `models_invited` order.
- `synthesis_lines`: split output on `\n`, drop blank lines, parse a leading `[X] ` tag with `re.match(r"^\[([A-Z])\]\s*", line)`, split a trailing `[DISPUTED` … `]` into `hedge`. Match each line's text to a claim by exact equality against `claim.text` first, then against `modified_text` with the same hedge stripped; a claim matches at most once; a line matches at most once; unmatched stays `None`.
- `metrics_recomputed = session.metrics is None and recomputed is not None`.
- `interpretations` come from `interpret_parity_flags(session)` when `session.metrics` is set; when metrics were recomputed, build a copy of the session with the recomputed metrics (`session.model_copy(update={"metrics": recomputed})`) and interpret that.
- `load_normalized` catches `Exception` per file, records `{"id", "error": str(exc).splitlines()[0]}`, and sorts sessions newest first by id.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/dashboard -q`
Expected: PASS

- [ ] **Step 6: Generate and commit the legacy fixture (closes Task 1 step 6)**

Run from the repo root:
```bash
.venv/bin/python -c "from tests.dashboard.session_builder import make_full_session as m; from pathlib import Path; Path('tests/fixtures').mkdir(exist_ok=True); Path('tests/fixtures/session_20260504_000000_legacy01.session.json').write_text(m(legacy=True, session_id='session_20260504_000000_legacy01').model_dump_json(indent=2))"
```
Then add to `tests/memory_graph/test_store.py`:

```python
def test_legacy_fixture_loads_and_keeps_its_ids():
    store = JsonFileSessionStore(Path(__file__).resolve().parents[1] / "fixtures")
    session = store.load("session_20260504_000000_legacy01")
    assert ModelId.LEGACY_OPUS_4_7 in session.models_invited
```

Run: `.venv/bin/python -m pytest tests/memory_graph/test_store.py -q` → PASS

- [ ] **Step 7: Commit**

```bash
git add src/golden_lattice/dashboard/normalize.py tests/dashboard/conftest.py tests/dashboard/test_normalize.py tests/fixtures tests/memory_graph/test_store.py
git commit -m "Dashboard: one normalized shape with readers, fates, pairs, readings"
```

---

### Task 5: The Record's cross-session sums

**Files:**
- Create: `src/golden_lattice/dashboard/record.py`
- Test: `tests/dashboard/test_record.py`

**Interfaces:**
- Consumes: normalized session dicts (Task 4), `seat_family`, `SEAT_FAMILY_ORDER`.
- Produces: `build_record(sessions: list[dict]) -> dict` with:
  - `runs: list[{"id","created_at","title","prompt","claims","disputed","both","elevated","parity": "holds"|"flag"|"undefined", "flag_reading": str|None, "recomputed": bool, "seats": [family...]}]` newest first
  - `series: {family: [{"run_id","share": float|None,"model_id","id_changed": bool}]}` one entry per run in chronological (oldest first) order; `share` is `None` for runs where the family was not seated or metrics are undefined
  - `threshold: float` (the max `parity_threshold` across runs with metrics; `PARITY_THRESHOLD` when there are none)
  - `pairs: list[{"reader","target","disputed","endorsed","highlight": bool}]` summed by family pair over every run's `pairs`, sorted by disputed descending; `highlight = endorsed >= HIGHLIGHT_RATIO * disputed and disputed + endorsed > 0`
  - `flags: list[{"run_id","created_at","family","dimension","reading","gloss","share","recomputed"}]`
  - `persistent: {"present": bool, "family": str|None, "rule": "same family flagged in 3 or more of the last 5 runs"}`
  - Constants `HIGHLIGHT_RATIO = 8`, `PERSISTENT_WINDOW = 5`, `PERSISTENT_MIN = 3`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/dashboard/test_record.py
from golden_lattice.dashboard.normalize import normalize_session
from golden_lattice.dashboard.record import build_record, HIGHLIGHT_RATIO

def _runs(make_full_session, n, **kw):
    return [normalize_session(make_full_session(session_id=f"session_202605{10 + i:02d}_000000_r{i:07d}", created_offset_days=i, **kw)) for i in range(n)]

def test_runs_newest_first_with_claim_counts(make_full_session):
    r = build_record(_runs(make_full_session, 2))["runs"]
    assert [x["id"][:16] for x in r] == ["session_20260511", "session_20260510"]
    assert (r[0]["claims"], r[0]["disputed"], r[0]["both"], r[0]["elevated"]) == (6, 3, 1, 1)
    assert r[0]["parity"] == "flag" and r[0]["flag_reading"] == "not_recognized" and r[0]["recomputed"] is False

def test_series_by_family_oldest_first_with_id_change_marker(make_full_session):
    legacy = normalize_session(make_full_session(legacy=True, session_id="session_20260501_000000_legacy00", created_offset_days=0))
    current = normalize_session(make_full_session(session_id="session_20260502_000000_curr0000", created_offset_days=1))
    series = build_record([current, legacy])["series"]
    assert [p["run_id"] for p in series["opus"]] == ["session_20260501_000000_legacy00", "session_20260502_000000_curr0000"]
    assert series["opus"][0]["id_changed"] is False and series["opus"][1]["id_changed"] is True
    assert series["opus"][1]["share"] == 0.34
    assert "fable" not in series

def test_pairs_sum_by_family_and_highlight(make_full_session):
    pairs = {(p["reader"], p["target"]): p for p in build_record(_runs(make_full_session, 3))["pairs"]}
    assert pairs[("opus", "sonnet")] == {"reader": "opus", "target": "sonnet", "disputed": 3, "endorsed": 0, "highlight": False}
    assert pairs[("sonnet", "haiku")]["endorsed"] == 6 and pairs[("sonnet", "haiku")]["highlight"] is True
    assert HIGHLIGHT_RATIO == 8

def test_flags_list_and_persistent_rule(make_full_session):
    rec = build_record(_runs(make_full_session, 3))
    assert [f["family"] for f in rec["flags"]] == ["sonnet", "sonnet", "sonnet"]
    assert rec["flags"][0]["gloss"].startswith("Genuine recognition asymmetry")
    assert rec["persistent"] == {"present": True, "family": "sonnet", "rule": "same family flagged in 3 or more of the last 5 runs"}
    assert build_record(_runs(make_full_session, 2))["persistent"]["present"] is False

def test_recomputed_flags_count_and_are_marked(make_full_session):
    rec = build_record(_runs(make_full_session, 1, persist_metrics=False))
    assert rec["runs"][0]["recomputed"] is True
    assert all(f["recomputed"] for f in rec["flags"])

def test_record_dyad_excluded_from_series_and_flags(make_full_session):
    rec = build_record([normalize_session(make_full_session(dyad=True))])
    assert rec["runs"][0]["parity"] == "undefined"
    assert rec["flags"] == [] and all(p["share"] is None for fam in rec["series"].values() for p in fam)

def test_empty_record():
    assert build_record([]) == {"runs": [], "series": {}, "threshold": 0.15, "pairs": [], "flags": [],
                                "persistent": {"present": False, "family": None, "rule": "same family flagged in 3 or more of the last 5 runs"}}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/dashboard/test_record.py -q`
Expected: FAIL with `ModuleNotFoundError: record`

- [ ] **Step 3: Implement `build_record` in `record.py`**

Chronological order for `series` is ascending `id`. `id_changed` compares the family's `model_id` with the family's previous seated run. The persistent rule looks at the last `PERSISTENT_WINDOW` runs (newest first) and reports the first family with at least `PERSISTENT_MIN` flagged runs in that window, in `SEAT_FAMILY_ORDER`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/dashboard -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/golden_lattice/dashboard/record.py tests/dashboard/test_record.py
git commit -m "Dashboard: the Record sums shares, pairs, and flags across runs"
```

---

### Task 6: Server routes, bundle cache, static files

**Files:**
- Modify: `src/golden_lattice/dashboard/server.py`
- Modify: `src/golden_lattice/dashboard/static/index.html` (minimal shell; Task 7 fills it)
- Test: `tests/dashboard/test_server.py`

**Interfaces:**
- Consumes: `load_normalized` (Task 4), `build_record` (Task 5).
- Produces, on `DashboardServer`:
  - `GET /` and `GET /session/{session_id}` → `index.html` (text/html)
  - `GET /static/{path}` → files under `dashboard/static/` (aiohttp `add_static`, `show_index=False`)
  - `GET /sessions` → JSON `{"sessions": [...], "unloadable": [...], "record": {...}}`
  - `GET /sessions/{session_id}` → one normalized dict; 404 `{"error": "not found"}`; 400 `{"error": "invalid session id"}` when the id contains `/`, `..`, or starts with `.`
  - `bundle() -> NormalizedBundle` with cache key `tuple(sorted((p.name, p.stat().st_mtime_ns) for p in self.sessions_dir.glob("*.session.json")))`; a changed key rebuilds
  - `/explainer` unchanged.

- [ ] **Step 1: Write the failing tests** (append to `tests/dashboard/test_server.py`, same `_start_server` helper)

```python
def test_sessions_endpoint_returns_bundle_with_unloadable(tmp_path, make_full_session):
    JsonFileSessionStore(tmp_path).save(make_full_session())
    (tmp_path / "session_bad.session.json").write_text("{}")
    async def _run():
        server = DashboardServer(sessions_dir=tmp_path); runner, port = await _start_server(server)
        try:
            async with ClientSession() as http:
                async with http.get(f"http://127.0.0.1:{port}/sessions") as resp:
                    data = await resp.json()
                    assert set(data) == {"sessions", "unloadable", "record"}
                    assert data["unloadable"][0]["id"] == "session_bad"
                    assert data["record"]["runs"][0]["id"] == "session_20260504_120000_abcd1234"
        finally: await runner.cleanup()
    asyncio.run(_run())

def test_sessions_cache_invalidates_when_a_file_appears(tmp_path, make_full_session):
    # GET /sessions (0 runs) → save one → GET again returns 1 run

def test_session_endpoint_returns_one_and_404_and_400(tmp_path, make_full_session):
    # /sessions/session_20260504_120000_abcd1234 → 200 with "claims" key
    # /sessions/nope → 404 and body {"error": "not found"}
    # /sessions/..%2Fx → 400 and body {"error": "invalid session id"}

def test_session_page_and_static_served():
    # GET /session/anything → 200, "app.js" in body; GET /static/app.css → 200
```

Write the three sketched tests in full in the same style as the first.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/dashboard/test_server.py -q`
Expected: the four new tests FAIL (bare list from `/sessions`, 404 on `/static/app.css`)

- [ ] **Step 3: Implement the routes and cache in `server.py`**

Keep `_handle_index` for `/` and register it for `/session/{session_id}` too. Replace `_sessions_cache` with `(key, bundle)` and `bundle()`. `_handle_sessions` returns `{"sessions": b.sessions, "unloadable": b.unloadable, "record": build_record(b.sessions)}`. `_handle_session` validates the id with the same three rules the store uses, then looks it up in `bundle().sessions`.

- [ ] **Step 4: Write the minimal `index.html` shell**

`<!DOCTYPE html>`, the font `<link>` tags from the current file, `<link rel="stylesheet" href="/static/app.css">`, `<div id="app"></div>`, `<script type="module" src="/static/app.js"></script>`. Create an empty `static/app.js` exporting nothing yet so the static test passes.

- [ ] **Step 5: Run the suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (the old `test_server_serves_index_html_at_root` still finds "Golden Lattice" in the `<title>`)

- [ ] **Step 6: Commit**

```bash
git add src/golden_lattice/dashboard/server.py src/golden_lattice/dashboard/static/index.html src/golden_lattice/dashboard/static/app.js tests/dashboard/test_server.py
git commit -m "Dashboard server: session route, static files, bundle with unloadable and record"
```

---

### Task 7: The Record page

**Files:**
- Modify: `src/golden_lattice/dashboard/static/app.js`, `app.css`
- Create: `static/js/seats.js`, `static/js/record.js`

**Interfaces:**
- Produces, in `seats.js`: `familyOf(modelId) -> "fable"|"opus"|"sonnet"|"haiku"` (substring test in that order, throws on none); `labelOf(modelId)`; `colorOf(modelId)` returning `var(--seat-<family>)`; `FAMILY_ORDER`.
- Produces, in `app.js`: `boot()` reading `location.pathname`; `/` renders the Record; `/session/<id>` renders the Session (Task 8); a `fetchBundle()` that GETs `/sessions` once and caches it for the page's life; `esc(s)` HTML escape.
- Produces, in `record.js`: `renderRecord(root, bundle)`; the strip uses `bundle.record` only; the list uses `bundle.record.runs`; search filters rows by `prompt` substring, case-insensitive; the in-flight row is a placeholder element `#gl-live-row` that `live.js` (Task 9) fills.

- [ ] **Step 1: Write `seats.js` and the router**

`app.js` imports `seats.js`, `record.js`, and (later) `session.js`, `live.js`. Boot fetches the bundle, renders by path, and renders an error state (`"could not reach /sessions"`) on fetch failure.

- [ ] **Step 2: Write `record.js`**

Layout per spec §6.1: masthead (title with run count, search input, explainer link, go-live link), the three-cell strip in a CSS grid with `gap: 28px` and `min-width: 0` on every cell, then the list. The chart is an inline SVG with `viewBox="0 0 340 120"`, `width="100%"`, plot area x 30..286, y 10..100, threshold line at `y = 100 - 90 * threshold / 0.5`, one `<polyline>` per family in `FAMILY_ORDER` using `series[family]` (skipping `null` shares, breaking the line at a gap), a 4px-radius end marker, the family label inside the viewBox at x 294, a thin vertical rule at any point with `id_changed`, `role="img"` and an `aria-label` built from the caption text. Hover on a run column shows a tooltip `<div class="tip">` with each seated family's share and exact `model_id`. Caption: `"<FAMILY> has been the lowest seat in N of M runs, never below X. Nothing has crossed the line."` or `"... crossed the line in N runs."` from `series`. Pairs table: rows from `record.pairs`, highlighted rows get class `hi`. Flags: hero number `record.flags.length` with `"in M runs"`, one line per flag with `recomputed` marked, and the closing sentence from `record.persistent` plus the rule text. Unloadable: `"N files could not be loaded"` toggling a list of `id · error`. Rows link to `/session/<id>`.

- [ ] **Step 3: Style in `app.css`**

Serif for prompt text and captions; mono for labels, numbers, dates; one mark per row; no borders between strip cells; row grid `92px 1fr 64px 76px 60px 60px 120px`; `.hi` uses `--critique`; parity verdict `holds` in `--present`, `flag · <reading>` in `--converge`, `undefined` in `--dim`.

- [ ] **Step 4: Verify in the browser**

Run the server against the real sessions: edit `.claude/launch.json` to point `--sessions-dir` at `/Users/vaquez/golden-lattice/sessions` (legacy ids load natively after Task 1), start it, open `http://127.0.0.1:8765/`.
Expected: 12 rows newest first; the strip shows three lines all above the dashed 0.15 line, the pair table with both Haiku rows highlighted, flags `2 in 12 runs` with one marked recomputed, no cell overflow at 760px and 1280px (check with the browser's responsive mode or `resize_window`), and `0 files could not be loaded`.

- [ ] **Step 5: Run the suite and commit**

Run: `.venv/bin/python -m pytest -q` → PASS

```bash
git add src/golden_lattice/dashboard/static
git commit -m "Dashboard: the Record page"
```

---

### Task 8: The Session page: rail, pin, five sections, replay

**Files:**
- Create: `static/js/session.js`, `static/js/beats.js`
- Modify: `static/app.js`, `app.css`

**Interfaces:**
- Produces, in `session.js`: `renderSession(root, session, {mode: "replay"|"live"})` building the masthead (label "Replay" or "Live", the prompt on one line with the full text on click, each seat as `LABEL · exact-id` with the id in mono and dimmed, the session id), rail, pane area, and transport; `state = {active: "02", pinned: "04", open: Set<claimId>, filter: "disputed"|"all"|"elevated"|"omitted", scroll: {section: number}}`; `selectSection(id)`, `togglePin(id)`, `openClaim(id)`, `jumpToLine(index)`; URL hash `#<active>[+<pinned>]` written on change and read on load; keyboard `0`-`4`, `p`, `Escape`.
- Produces, in `beats.js`: `buildBeats(session) -> Beat[]` in the order of spec §6.3 (00 entries, Phase 1 claims and reflections, Phase 2 readings, Phase 3 turns, artifact, metrics, interpretations), each beat a `{section, apply(state)}`; `transport` with play, pause, restart, scrub, speed, step label, and `PLAY_PERIOD_MS = 360`; persisted step in `localStorage` key `gl-replay-step-<sessionId>`.
- Sections render from the normalized shape only. 02 row mark: `×N` in `--critique` when `disputed_by.length`, `✦` in `--converge` when `elevated`, `—` in `--dim` when omitted; the dispute mark takes precedence. Expanded row: one `.voice` block per entry in `disputed_by`, in that order; a block shows the Phase 2 reason, then `"In dialogue:"` plus each critique by that speaker; a critique-only block starts with `"In dialogue:"`; an endorsed-then-critiqued block starts with `"endorsed in cross-reading, then in dialogue:"`; the endorsed line lists `agreed_by` minus any reader with a block; the fate line shows disposition, `synthesis_line + 1` as a link, `disputed_hedge`, `surfaced_note`, `modified_text` or `omission_reason` when present. 02 header: filters with counts from `stats`; when `stats.disputed === 0`, the header says `"no reader disputed anything in this run"` and the filter defaults to `all`; the quiet line `"<stats.missing> claims readers said were missing are not shown"` when `stats.missing > 0`. 03: augment and converge turns; top line `"<stats.critiques> critiques are on their claims in 02"`. 04: ledger line, parity line (`holds` / `flag · <reading> · <gloss>` / `parity is undefined at two seats`, plus lowest share and threshold), output lines with tag and `×N` link marks, omitted claims listed with reasons under the output. 00: `skipped` state when `phase0` is null.

- [ ] **Step 1: Write `beats.js`**

Pure functions over the normalized session; a beat never touches the DOM, it flips flags in a `revealed` set that `session.js` reads when rendering each section.

- [ ] **Step 2: Write `session.js`**

Render each section into its own `.pane .bd` with `overflow-y: auto`; keep per-section scroll offsets in `state.scroll` and restore them on re-render. Rail entries dim (`.pending`) until the transport reaches that section's first beat. `jumpToLine` pins 04 if a pane is free, else replaces the pinned pane, then scrolls the line into view and flashes it.

- [ ] **Step 3: Style in `app.css`**

Rail 150px; pane grid `1.2fr 1fr` when two are visible, single column when one; row grid `56px 1fr 44px`; `.voice p + p` dimmer; transport fixed at the bottom with the current scrubber styles.

- [ ] **Step 4: Wire the router**

`/session/<id>` fetches the bundle, finds the session by id (or renders `"session not found"` with the unloadable error if the id is in `unloadable`), and calls `renderSession`.

- [ ] **Step 5: Verify every section against the t2helix run**

Open `http://127.0.0.1:8765/session/session_20260516_204119_ed014de7`.
Expected: 02 active with 04 pinned; filter `disputed · 17`; the Haiku "five enhancement vectors" row expands to an OPUS block (Phase 2 reason then "In dialogue:"), a SONNET block (same), fate `present · line 9`; the "hybrid BM25" row shows an OPUS block and `HAIKU` on the endorsed line; 04's line 9 carries `×2` and clicking it opens the row in 02; the parity line reads `holds · threshold 0.15 · lowest share HAIKU 0.28`.

- [ ] **Step 6: Verify replay**

Press restart; scrub to the first Phase 2 beat.
Expected: rail entries 03 and 04 dimmed, 02 rows present without marks until each cross-reading beat lands; play reaches the end and the step label reads `N / N`.

- [ ] **Step 7: Verify the four-seat case and the disputed hedge**

Open the two 05-17 runs.
Expected: `[DISPUTED` hedges render on their lines and in the fate lines; the 01 grid has three columns here and will have four for a Fable run (confirm the grid is `repeat(auto-fit, minmax(220px, 1fr))`, not a fixed count).

- [ ] **Step 8: Verify the rail and pin**

Press `3`, then `p` on 04, then `Escape`.
Expected: 03 fills the window, then 03 and 04 side by side, then 03 alone; the hash reads `#03+04` then `#03`; reloading restores it.

- [ ] **Step 9: Verify a dyad and a Phase 0 run**

Open a run with Phase 0 (`session_20260516_190624_77b1da30`) and, via the fixture dir, the dyad from the builder if one is saved.
Expected: 00 lists proposals per seat and the frozen feed; the dyad's 04 parity line says parity is undefined at two seats.

- [ ] **Step 10: Verify the empty-disputes state**

Temporarily point `--sessions-dir` at a folder holding only a session saved from `make_full_session` with the zero-disputes edit from Task 4.
Expected: 02 header reads `no reader disputed anything in this run` and the `all` filter is active.

- [ ] **Step 11: Run the suite and commit**

```bash
git add src/golden_lattice/dashboard/static
git commit -m "Dashboard: the Session page with rail, pin, and the disputes table"
```

---

### Task 9: Live mode and the live script fixes

**Files:**
- Create: `static/js/live.js`
- Modify: `static/app.js`, `static/js/record.js`, `static/js/session.js`
- Modify: `scripts/run_lattice_live.py:194-210,215-236`
- Test: `tests/dashboard/test_server.py` (cache test from Task 6 already covers the server side)

**Interfaces:**
- Produces, in `live.js`: `connectLive({onEvent})` opening `ws(s)://<host>/ws` with the existing reconnect loop; `applyEvent(state, ev)` mutating a live session object shaped like the normalized shape (`claims` grow from `phase_1_claim`, `seats` from `session_started`, `stream` per seat from `model_stream_delta`, `pairs` from `phase_2_cross_reading` with the event's three counts, `critiques` attached from `phase_3_turn` by `target_claim_ids`, `synthesis_lines` from `phase_4_artifact`, `interpretations` from `phase_4_flag_interpretations`); it returns the list of sections that changed so `session.js` re-renders only those. On `session_completed`, `hydrate(sessionId)` GETs `/sessions/<id>` up to 5 times, 1 second apart, and on success replaces `claims`, `pairs`, `stats`, `interpretations`, `elevations`; on exhaustion 02 shows `"the persisted file for this run was not found; reasons are unavailable"`.
- Producers in the script: `DashboardServer(sessions_dir=sessions_dir)`; in `run_once`, call `_persist_and_print(session, sessions_dir, console)` before `await asyncio.gather(*pending, return_exceptions=True)` and return the session; the callers no longer persist a second time.
- The Record's `#gl-live-row` shows `now · <phase>` and the prompt while a session is in flight, and is removed when the Record refetches the bundle after completion.

- [ ] **Step 1: Write `live.js` and wire "go live"**

The Record's `go live` link and the Session's `exit live` button toggle `mode`; in live mode the Session page renders with the live object and the live transport (dot, status, prompt input, send, exit), keeping the current WebSocket messages `{"type":"prompt","text"}` and `{"type":"quit"}`.

- [ ] **Step 2: Stream deltas append, never rebuild**

In `session.js`, the 01 pane keeps one `<pre data-stream="<model>">` per seat and `applyEvent` appends `ev.delta` as a text node; every other event re-renders only the sections it returns.

- [ ] **Step 3: Fix the script**

Apply the three changes in the Interfaces block. Confirm with:

Run: `grep -n "DashboardServer(sessions_dir=sessions_dir)" scripts/run_lattice_live.py && grep -n "_persist_and_print" scripts/run_lattice_live.py`
Expected: the constructor line is found; inside `_run_dashboard`, `_persist_and_print` is called once, before the `gather`, and not in the two call sites below it.

- [ ] **Step 4: Verify live without an API key**

Run: `.venv/bin/python scripts/run_lattice_live.py --dashboard --sessions-dir /tmp/gl-live-check` with no key, then in the tab press `go live`.
Expected: the live shell shows `connected · waiting for a run`; submitting a prompt produces a `session_error` banner (no key) and the dashboard stays up; the Record shows no in-flight row afterward.

If a key is available, run one real prompt: the in-flight row appears, 02 shows pair lines during cross-reading, and after completion the rows gain marks and reasons and the Record lists the new run.

- [ ] **Step 5: Run the suite and commit**

```bash
git add src/golden_lattice/dashboard/static scripts/run_lattice_live.py
git commit -m "Dashboard: live mode on the same page, hydrates from disk on completion"
```

---

### Task 10: Docs, cleanup, whole-branch check

**Files:**
- Modify: `README.md` (Running it: `/` is the Record, `/session/<id>` the Session; the replay command unchanged; node optional for the validator)
- Modify: `src/golden_lattice/dashboard/__init__.py` docstring if it names the old single file
- Delete: nothing else; the old `index.html` body was replaced in Task 6

- [ ] **Step 1: Update the README running section and the explainer link text if it says "session replay"**

- [ ] **Step 2: Full verification**

Run: `.venv/bin/python -m pytest -q`
Expected: every prior test passes; new tests add at least 30; 11 skips remain or fewer.

Run: `node scripts/validate_palette.js "$(grep -o '#[0-9a-f]\{6\}' src/golden_lattice/dashboard/static/app.css | sed -n '1,4p' | paste -sd, -)" --mode dark --surface "#0c0d11"`
Expected: no FAIL line.

Open `/` and one session in the browser one last time at the pane's narrow width and at 1280px.
Expected: no horizontal overflow inside any pane or strip cell.

- [ ] **Step 3: Commit and open the PR**

```bash
git add README.md src/golden_lattice/dashboard/__init__.py
git commit -m "Docs: dashboard routes and running notes"
```

The PR description carries the palette validator report and the before/after of the twelve-run Record. End it with the Claude Code attribution line.
