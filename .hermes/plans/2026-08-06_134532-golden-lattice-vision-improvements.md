# Golden Lattice Vision-Aligned Improvements Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Evolve Golden Lattice from a structurally rigorous multi-model orchestrator into an experimental instrument for observing commitment, disagreement, and state change under perturbation.

**Architecture:** Preserve the existing symmetric peer protocol and deterministic canonical synthesis. Add a first-class commitment-transition layer around claims, then validate the larger vision through a small longitudinal corpus rather than adding more seats or phases. Keep any smoother human-facing interpretation explicitly separate from the authoritative lattice record.

**Tech Stack:** Python 3.11, Pydantic frozen models, existing async orchestrator, deterministic synthesis engine, JSON session store, pytest.

---

## Success Criteria

The work is successful when:

1. The four-seat roster does not silently disable any existing synthesis behavior.
2. A claim can be followed through challenge, revision, withdrawal, reaffirmation, or unresolved state with a traceable reason.
3. Repeated perturbation sessions produce inspectable state transitions and persistence/reversal observations.
4. Golden Lattice is compared against a strong single-model baseline and a simple ensemble baseline on real tasks.
5. The canonical record remains symmetric, attributable, replayable, and free of hidden judge authority.
6. Documentation describes the actual four-seat implementation and current test state.

Consensus alone is not a success criterion. Parity is a guardrail, not proof of value.

---

## Phase 0: Restore Four-Seat Behavioral Coherence

### Task 1: Generalize dispute surfacing from triadic to N-peer sessions

**Objective:** Make the implementation match ARCHITECTURE.md's rule that a claim disputed by at least two non-author peers receives a deterministic dispute hedge.

**Files:**
- Modify: `src/golden_lattice/synthesis/claim_trace.py`
- Test: `tests/synthesis/test_claim_trace.py`
- Review: `ARCHITECTURE.md` §6 and the existing `_two_peer_disputers` tests

**Implementation notes:**
- Remove the `len(non_author_peers) != 2` early return.
- Continue requiring at least two distinct disputing peers.
- Preserve deterministic peer ordering and Phase 2-over-Phase 3 reason priority.
- Rename internal documentation if needed so "two-peer" describes the minimum signal rather than a triadic-only rule.

**Tests:**
- Add an N=4 session where two of three non-author peers dispute an author's claim and assert a `[DISPUTED]` hedge.
- Add an N=4 session where only one peer disputes and assert no hedge.
- Preserve existing N=2 and N=3 behavior.

**Verification:**
```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /Users/vaquez/.hermes/hermes-agent/venv/bin/pytest -q tests/synthesis/test_claim_trace.py
```

### Task 2: Add four-seat end-to-end dispute coverage

**Objective:** Ensure the default roster exercises the generalized behavior rather than only isolated triadic fixtures.

**Files:**
- Modify: `tests/orchestrator/test_orchestrator.py`
- Modify: `tests/orchestrator/test_orchestrator_phase_0.py` if the live/stub integration path is shared
- Modify: `tests/synthesis/test_claim_trace.py`

**Verification:**
```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /Users/vaquez/.hermes/hermes-agent/venv/bin/pytest -q tests/orchestrator tests/synthesis
```

### Task 3: Make provider/model availability explicit

**Objective:** Separate a protocol seat identity from the provider endpoint used to serve it.

**Files:**
- Review/modify: `src/golden_lattice/orchestrator/anthropic_client.py`
- Review/modify: `src/golden_lattice/orchestrator/config.py`
- Review/modify: `src/golden_lattice/memory_graph/base.py`
- Test: `tests/orchestrator/test_orchestrator.py`
- Documentation: `README.md`

**Implementation notes:**
- Keep Fable as an equal peer seat.
- Add a preflight/capability validation path or configurable seat-to-provider-model mapping.
- Fail before Phase 1 with a clear message when a configured endpoint is unavailable.
- Do not claim Fable is live merely because `ModelId.FABLE` exists.

---

## Phase 1: Make Commitment Dynamics First-Class

### Task 4: Define commitment states and transitions

**Objective:** Represent how a claim's status changes under evidence and peer pressure.

**Files:**
- Modify: `src/golden_lattice/memory_graph/base.py` for closed-vocabulary enums
- Modify: `src/golden_lattice/memory_graph/schema.py` for frozen transition artifacts
- Test: `tests/memory_graph/test_schema.py`

**Initial vocabulary:**

```text
proposed, defended, challenged, revised, withdrawn,
reaffirmed, unresolved
```

Each transition should contain:

- claim ID,
- source model,
- prior state,
- next state,
- perturbation/source event,
- reason or supporting artifact reference,
- timestamp or deterministic session ordering.

Do not infer a transition merely from a changed sentence. Require an explicit structured artifact or deterministic rule.

### Task 5: Attach transitions to the session/replay model

**Objective:** Make commitment history available in live sessions, persisted sessions, and replay.

**Files:**
- Modify: `src/golden_lattice/memory_graph/schema.py`
- Modify: `src/golden_lattice/orchestrator/orchestrator.py`
- Modify: `src/golden_lattice/memory_graph/store.py` if serialization needs explicit handling
- Modify: event/replay modules discovered during implementation
- Tests: `tests/orchestrator`, `tests/memory_graph`, and relevant replay tests

**Verification:**
- A generated session round-trips through `JsonFileSessionStore` with transitions intact.
- Replay emits the same transition sequence as the live session.
- Invalid transitions are rejected rather than silently normalized.

### Task 6: Add persistence and reversal observations

**Objective:** Measure whether a changed commitment persists after the original perturbation is removed or reversed.

**Files:**
- Modify: `src/golden_lattice/memory_graph/metrics.py`
- Test: `tests/memory_graph/test_metrics.py`
- Documentation: `ARCHITECTURE.md` §10

**Initial metrics:**

- transition count,
- reversal count,
- reaffirmation count,
- unresolved rate,
- persistence after reversal,
- per-model and session-level commitment trajectories.

Keep these as observations, not claims of cognition.

---

## Phase 2: Run the Actual Experiment

### Task 7: Define a small longitudinal task corpus

**Objective:** Test the vision with a manageable set of repeated perturbation sessions.

**Files:**
- Create: `experiments/README.md`
- Create: `experiments/tasks/` task definitions
- Create: `experiments/protocol.md`

**Task categories:**

- design critique,
- competing scientific explanations,
- ambiguous evidence synthesis,
- decision under changing evidence.

Each task should include an initial claim, a controlled challenge, an evidence update, and a reversal/removal condition.

### Task 8: Add baseline runners

**Objective:** Compare Golden Lattice against simpler systems.

**Files:**
- Create: `experiments/baselines/`
- Create: `experiments/run_experiment.py`
- Reuse: `scripts/run_lattice_live.py`
- Test: lightweight parsing/configuration tests under `tests/experiments/`

**Required comparisons:**

1. strongest single peer,
2. simple parallel responses,
3. conventional judge/summarizer if available,
4. Golden Lattice.

Record quality observations, insight retention, disagreement handling, latency, and cost. Do not optimize for a metric before inspecting the actual outputs.

### Task 9: Produce human-readable case reports

**Objective:** Make the experiment legible without weakening the canonical record.

**Files:**
- Create: `experiments/reports/README.md`
- Review/modify: `src/golden_lattice/synthesis/attribution.py`
- Review/modify: `src/golden_lattice/tui/renderer.py`

**Output separation:**

- canonical annotated lattice artifact,
- optional reader-facing interpretation clearly labeled as non-canonical,
- commitment timeline,
- preserved disagreement list,
- baseline comparison.

Do not add a hidden generative judge to the canonical synthesis path.

---

## Phase 3: Documentation and Constitutional Cleanup

### Task 10: Synchronize public documentation with implementation

**Files:**
- Modify: `README.md`
- Modify: `ARCHITECTURE.md`
- Modify: `src/golden_lattice/orchestrator/orchestrator.py` docstrings
- Modify: `src/golden_lattice/synthesis/claim_trace.py` docstrings
- Review: TUI attribution/color documentation

**Required cleanup:**

- replace stale “69 tests” text with a non-brittle statement or current verified status,
- remove claims that the operational layer is absent,
- replace triadic-only language where the protocol is now N=4/general-N,
- document Fable's peer status without implying provider availability,
- document commitment transitions and their limits.

### Task 11: Add invariant/documentation drift checks

**Objective:** Prevent the constitutional surface from becoming stale again.

**Files:**
- Create: `tests/test_documentation_invariants.py`
- Modify: `README.md` and `ARCHITECTURE.md` only when the specification changes

**Checks:**

- active roster names are documented,
- no stale triadic default claims remain,
- documented test command remains accurate,
- synthesis rules named in documentation match the closed enum vocabulary.

---

## Phase 4: Decision Gate

After the first experiment batch, make an explicit decision:

### Continue and deepen if:

- lattice outputs are repeatedly more useful than the best single baseline,
- claim transitions show reasoned rather than random movement,
- disagreement remains visible without destroying usability,
- at least some useful commitments emerge through interaction rather than simple duplication.

### Narrow the project if:

- the protocol is valuable mainly for auditability and provenance,
- the outputs are not better but are more inspectable,
- commitment dynamics are interesting even without quality gains.

### Stop expanding if:

- Golden Lattice is consistently worse than simpler baselines,
- parity can be increased without improving quality,
- the apparent transitions are mostly prompt-induced formatting behavior,
- the extra cost cannot be justified by insight or reliability.

---

## Full Verification Command

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /Users/vaquez/.hermes/hermes-agent/venv/bin/pytest -q
```

Expected baseline before implementation: `444 passed, 1 skipped` in the constrained environment. The live Anthropic API test remains skipped when credentials are unavailable.

## Main Risks and Tradeoffs

- **Overbuilding:** Commitment tracking should remain minimal until real sessions show it is useful.
- **Metric capture:** Do not confuse model self-report with external evidence of commitment.
- **Authority leakage:** A reader-facing narrative must never become the canonical synthesis.
- **Roster complexity:** More peer seats increase cross-reading cost quadratically.
- **Prompt effects:** Decomposition and reflection prompts may shape the behavior they measure.
- **Interpretive overreach:** State transitions can be measured without claiming subjective experience or human-like cognition.

## Recommended Order

1. Fix N=4 dispute behavior.
2. Make Fable/provider availability explicit.
3. Add minimal commitment transitions and replay support.
4. Run a small longitudinal experiment.
5. Only then decide whether more models, phases, or synthesis capability are justified.
