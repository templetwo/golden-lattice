# Enable Tavily-Grounded Golden Lattice Experiments Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Enable Golden Lattice's Phase 0 investigation/search path using the newly available `TAVILY_API_KEY`, while preserving explicit evidence provenance, async resource lifetimes, honest failure states, and fair interpretation of baseline results.

**Architecture:** Keep Anthropic responsible for peer/model wire calls and Tavily responsible for non-model search/extraction. The Golden Lattice adapter will create both clients for a grounded run, pass both into the existing `run_lattice_session_async` entrypoint, and close both transports in the same event loop. Grounded and ungrounded runs will be explicitly labeled because giving only Golden Lattice model-proposed external research is a capability comparison, not an isolated architecture comparison.

**Tech Stack:** Python 3.11, `anthropic`, `httpx`, Pydantic models, existing async orchestrator, TOML corpus, JSON run artifacts, pytest.

---

## Success Criteria / Definition of Done

The plan is complete only when all **build**, **evidence**, and **interpretation** gates below are satisfied. A provider timeout may block the evidence gates, but it must not be mislabeled as an integration success or a quality result.

### Build and safety gates

- [ ] `--grounding none` and `--grounding tavily` are explicit, serialized in the run manifest, and deterministic.
- [ ] A Tavily-grounded live run refuses to start when either `ANTHROPIC_API_KEY` or `TAVILY_API_KEY` is absent, with only the missing variable name reported.
- [ ] Dry-run mode makes zero Anthropic or Tavily network calls and does not require credentials.
- [ ] No credential value, authorization header, query-body key, or connection string appears in logs, JSON artifacts, reports, tracked files, or exception summaries.

### Phase 0 integration gates

- [ ] Grounded Golden Lattice sessions pass both the Anthropic Phase 0 proposal client and `TavilySearchClient` into `run_lattice_session_async`.
- [ ] Ungrounded sessions pass neither Phase 0 client and retain the documented `phase_0=None` behavior.
- [ ] A grounded smoke artifact contains a Phase 0 feed with at least one typed `SearchResult` or `FailedSearch` entry, rather than an absent or fabricated feed.
- [ ] Search failures preserve query, attempted time, and typed failure reason; they are never silently discarded or converted into synthetic evidence.
- [ ] Phase 0 evidence retains source URLs and is available to downstream Phase 1 responses through the existing feed path.

### Lifecycle and regression gates

- [ ] Anthropic and Tavily transports are closed on normal completion, provider error, timeout, cancellation, and Phase 0 failure.
- [ ] Longitudinal steps execute within the established single-event-loop boundary; no cross-loop client reuse or unclosed-client warnings remain.
- [ ] Existing project tests remain green, including the Phase 0 and lifecycle suites.
- [ ] Constitutional validation remains clean: no hidden judge, no asymmetric peer treatment, no silent search failure, and no fabricated provider output.

### Experimental evidence gates

- [ ] At least one bounded grounded Golden Lattice smoke run completes structurally, or produces an explicit structured timeout/unavailable artifact.
- [ ] A matched ungrounded control is generated with the same task corpus and perturbation sequence.
- [ ] Grounded and ungrounded manifests are distinguishable by `grounding_mode`; reports do not merge them into one unexplained score.
- [ ] Full benchmark claims are made only from artifacts containing the declared SUT/task set; a single-SUT smoke is never treated as a four-SUT decision-gate result.
- [ ] The decision gate returns `INSUFFICIENT_EVIDENCE` when completed structured evidence is inadequate; it never upgrades timeout, dry-run, or provider-unavailable data into a quality pass.

### Scientific interpretation gates

- [ ] Reports distinguish retrieval capability from lattice-architecture effects.
- [ ] No conclusion claims that Tavily evidence alone demonstrates better reasoning, cognition, or commitment.
- [ ] Any claim that Golden Lattice outperforms a baseline identifies the condition (`none` or `tavily`), task set, completion rate, and preserved disagreement/transition evidence.
- [ ] If the provider endpoint continues timing out, the final result is reported as an empirical blocker—not as evidence for or against the lattice hypothesis.

## Current Context and Assumptions

- `/Users/vaquez/.hermes/.env` now contains both `ANTHROPIC_API_KEY` and `TAVILY_API_KEY`; values must never be printed or committed.
- `experiments/baselines/golden_lattice.py` already uses one event loop per longitudinal session and closes the underlying Anthropic client in that loop.
- `src/golden_lattice/exchange/tavily_search_client.py` already implements `SearchClient`, supports `/search` and `/extract`, returns typed `SearchResult` / `FailedSearch`, and exposes `aclose()`.
- `src/golden_lattice/orchestrator/anthropic_client.py` already implements the Phase 0 proposal interface.
- `run_lattice_session_async` runs Phase 0 only when both `phase_0_client` and `search_client` are supplied; otherwise it intentionally skips Phase 0.
- The current live smoke reached Phase 3 but timed out at the configured 60-second Phase 3 limit. Tavily integration must not be conflated with that timeout; first prove Phase 0 independently, then rerun the live path.
- Preserve the existing uncommitted working tree. Do not reset, commit, push, or rewrite unrelated changes.

## Decisions to Preserve

1. **No silent grounding:** A grounded run must record `grounding_mode=tavily` in its manifest/session metadata. A missing Tavily key must produce an explicit `unavailable` result, never silently fall back to ungrounded execution.
2. **Typed search failure is evidence:** A Tavily HTTP/network/parse failure remains a `FailedSearch` feed entry and is preserved in the session artifact.
3. **No hidden judge:** Tavily is retrieval only. It must not summarize, rank peer answers, or alter canonical synthesis authority.
4. **Fairness labeling:** A run where only Golden Lattice receives model-proposed external investigation is a grounded-capability comparison, not proof that the architecture alone caused an improvement. Produce a matched ungrounded control as well.
5. **Explicit opt-in:** Live mode still requires `GOLDEN_LATTICE_EXPERIMENT_LIVE=1`; dry-run never needs either credential and never makes network calls.

---

## Phase 1: Add Grounding Configuration and Availability Gates

### Task 1: Define an explicit experiment grounding mode

**Objective:** Make grounded versus ungrounded execution visible and deterministic at the runner boundary.

**Files:**
- Modify: `experiments/run_experiment.py`
- Modify: `experiments/runner_lib.py`
- Modify: `experiments/baselines/protocol.py` if the SUT context requires a typed mode
- Test: `tests/experiments/test_runner.py` or the existing experiment runner test module

**Steps:**
1. Add a CLI option such as `--grounding {none,tavily}`. Keep `none` as the backward-compatible dry-run default; require `tavily` explicitly for grounded live runs.
2. Thread the selected mode through `run_batch()` into each SUT without changing the existing prompt-bundle schema.
3. Record `grounding_mode` in the run manifest and each session's metadata.
4. Add unit tests proving the mode is parsed, propagated, and serialized.

**Verification:**
```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /Users/vaquez/.hermes/hermes-agent/venv/bin/pytest -q tests/experiments
```

### Task 2: Gate Golden Lattice grounded availability on both credentials

**Objective:** Fail closed before any model or search call when a grounded run lacks either provider credential.

**Files:**
- Modify: `experiments/baselines/_common.py` only if shared availability helpers remain appropriate
- Modify: `experiments/baselines/golden_lattice.py`
- Test: `tests/experiments/test_golden_lattice_lifecycle.py`
- Test: `tests/experiments/test_runner.py`

**Steps:**
1. Keep Anthropic-only availability for ungrounded live runs.
2. Add grounded availability requiring both `ANTHROPIC_API_KEY` and `TAVILY_API_KEY`, with a reason naming the missing variable without exposing values.
3. Add tests for: both present, Anthropic missing, Tavily missing, live opt-in missing, and dry-run bypassing credentials.
4. Ensure other SUTs do not become falsely unavailable merely because Tavily is absent; their behavior should remain explicit in the manifest.

**Verification:**
```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /Users/vaquez/.hermes/hermes-agent/venv/bin/pytest -q tests/experiments/test_runner.py tests/experiments/test_golden_lattice_lifecycle.py
```

---

## Phase 2: Wire Tavily into the Single-Loop Golden Lattice Adapter

### Task 3: Construct and inject the Phase 0 clients

**Objective:** Run canonical Phase 0 investigation and retrieval for grounded Golden Lattice sessions.

**Files:**
- Modify: `experiments/baselines/golden_lattice.py`
- Review only: `src/golden_lattice/orchestrator/orchestrator.py`
- Review only: `src/golden_lattice/orchestrator/anthropic_client.py`
- Review only: `src/golden_lattice/exchange/tavily_search_client.py`

**Steps:**
1. Read `TAVILY_API_KEY` only from the process environment.
2. Construct `TavilySearchClient(api_key=...)` only when `grounding_mode=tavily`.
3. Pass `phase_0_client=client` and `search_client=tavily_client` to every `run_lattice_session_async` call.
4. Keep the existing prior-step transcript continuity and explicit transition extraction unchanged.
5. Keep all steps in the already-established single event loop.
6. Close both the underlying Anthropic client and Tavily's `httpx.AsyncClient` in a `finally` block on success, provider failure, timeout, and cancellation.
7. Preserve search failures inside the Phase 0 feed; do not convert them into fabricated evidence or abort the session unless the canonical orchestrator itself raises a hard error.

**Verification:**
```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /Users/vaquez/.hermes/hermes-agent/venv/bin/pytest -q tests/experiments/test_golden_lattice_lifecycle.py tests/orchestrator/test_phase_0_e2e.py
```

### Task 4: Add lifecycle and Phase 0 injection regression tests

**Objective:** Prove Tavily is actually used in grounded mode and closed correctly without network calls.

**Files:**
- Modify: `tests/experiments/test_golden_lattice_lifecycle.py`
- Create or modify: `tests/experiments/test_tavily_grounding.py`

**Tests:**
- Grounded mode passes both clients to the async orchestrator.
- Ungrounded mode passes neither Phase 0 client.
- All longitudinal steps share one event loop.
- Anthropic and Tavily clients close on normal completion.
- Both clients close when Phase 0, Phase 1, Phase 2, or Phase 3 raises.
- A `FailedSearch` result is retained in the resulting session rather than silently dropped.
- No API key or connection string appears in serialized metadata, error text, or test output.

Use mocked async clients and `httpx.MockTransport`; do not call Anthropic or Tavily from unit tests.

**Verification:**
```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /Users/vaquez/.hermes/hermes-agent/venv/bin/pytest -q tests/experiments/test_tavily_grounding.py tests/experiments/test_golden_lattice_lifecycle.py
```

---

## Phase 3: Make the Experimental Comparison Honest

### Task 5: Document the two experimental conditions

**Objective:** Prevent readers from interpreting a search-enabled Golden Lattice run as an apples-to-apples architecture win over ungrounded baselines.

**Files:**
- Modify: `experiments/protocol.md`
- Modify: `experiments/README.md`
- Modify: `experiments/reports/generate_report.py`
- Test: `tests/test_documentation_invariants.py`

**Documentation requirements:**
- `grounding_mode=none`: all systems run without Phase 0 external retrieval.
- `grounding_mode=tavily`: Golden Lattice runs canonical Phase 0; the report labels the result as a grounded capability condition.
- Reports must show grounding mode beside every comparison row.
- Reports must distinguish missing/failed search evidence from absent search attempts.
- No report may claim that Tavily evidence proves model quality or consciousness-like reasoning.

### Task 6: Add a matched ungrounded control run

**Objective:** Establish the architecture comparison that does not give one SUT an undisclosed evidence advantage.

**Files:**
- No production code expected unless report metadata requires it.
- Create run artifacts under `/tmp` or the existing ignored run directory; do not commit credentials or live outputs.

**Steps:**
1. Run all four SUTs on all four tasks with `--grounding none` and live opt-in.
2. Preserve unavailable/timeout/aborted states exactly.
3. Do not call the decision gate until the artifact contains the complete declared SUT set.
4. Run the grounded Golden Lattice condition separately with `--grounding tavily`.
5. Compare the two conditions descriptively; do not merge them into one quality score without an explicit protocol change.

**Expected artifact checks:**
- Manifest contains the selected grounding mode.
- Every session records its SUT, task, grounding mode, and status.
- Phase 0 feed is present only in grounded Golden Lattice sessions.
- Search failures, if any, are structured `FailedSearch` entries.

---

## Phase 4: Live Verification and Decision Gate

### Task 7: Run a bounded Tavily smoke test

**Objective:** Prove the new path reaches Phase 0 and preserves evidence before spending on the full corpus.

**Command:**
```bash
set -a; . /Users/vaquez/.hermes/.env; set +a
GOLDEN_LATTICE_EXPERIMENT_LIVE=1 \
/Users/vaquez/.hermes/hermes-agent/venv/bin/python \
experiments/run_experiment.py \
  --mode live \
  --grounding tavily \
  --sut golden_lattice \
  --task gl.longitudinal.design_critique.v1 \
  --out /tmp/golden-lattice-grounded-smoke
```

Inspect only manifest/status/feed metadata; never print raw credentials or unfiltered provider payloads.

**Pass conditions:**
- The run reaches Phase 0.
- The JSON contains a Phase 0 feed with either `SearchResult` or typed `FailedSearch` entries.
- The process exits cleanly, including client cleanup on any later timeout.
- If Phase 3 still times out, the artifact must say so explicitly and the timeout remains a separate issue.

### Task 8: Run full conditions and gate only complete artifacts

**Objective:** Produce decision-gate inputs that satisfy structural requirements before interpreting quality.

**Commands:**
```bash
# Matched ungrounded condition
set -a; . /Users/vaquez/.hermes/.env; set +a
GOLDEN_LATTICE_EXPERIMENT_LIVE=1 \
/Users/vaquez/.hermes/hermes-agent/venv/bin/python \
experiments/run_experiment.py --mode live --grounding none \
  --out /tmp/golden-lattice-live-ungrounded

# Grounded Golden Lattice condition
set -a; . /Users/vaquez/.hermes/.env; set +a
GOLDEN_LATTICE_EXPERIMENT_LIVE=1 \
/Users/vaquez/.hermes/hermes-agent/venv/bin/python \
experiments/run_experiment.py --mode live --grounding tavily \
  --sut golden_lattice \
  --out /tmp/golden-lattice-live-grounded
```

Run `experiments/decision_gate.py` only against a complete four-SUT artifact, or explicitly extend the gate schema for condition-specific partial runs first. A single-SUT artifact must not be presented as a full benchmark verdict.

### Task 9: Final verification

Run:
```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /Users/vaquez/.hermes/hermes-agent/venv/bin/pytest -q
PYTHONPATH=src:. /Users/vaquez/.hermes/hermes-agent/venv/bin/python -m experiments.validation
git diff --check
```

Verify:
- No credential values appear in tracked files, JSON metadata, reports, or terminal summaries.
- All existing tests remain passing.
- Grounded and ungrounded run manifests are distinguishable.
- Phase 0 provenance is replayable and typed.
- No hidden judge or synthesis fork was introduced.
- Timeout/unavailable outcomes remain honest and prevent a false `PASS`.

## Risks, Tradeoffs, and Open Questions

- **Provider cost/time:** Full Golden Lattice sessions already have four seats and multiple phases; adding Phase 0 adds proposal and search calls. Start with one task and one SUT.
- **Model endpoint timeout:** Tavily will not fix the existing Phase 3 timeout. If it persists, investigate endpoint mapping and timeout policy separately rather than masking it with retries.
- **Comparison confound:** Giving only Golden Lattice model-proposed search is a capability demonstration, not a clean architecture comparison. Keep the ungrounded control and label claims accordingly.
- **Tavily failure semantics:** A failed search should remain visible as evidence of failed retrieval, but the session may continue if the canonical Phase 0 contract permits it.
- **Credential handling:** Source `~/.hermes/.env` only inside the child process. Never read, print, serialize, or commit secret values.
- **Working tree:** Do not commit automatically. Preserve all existing user changes and review the final diff before any later commit decision.
