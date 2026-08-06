# Longitudinal experiment protocol

Companion to `experiments/README.md`. Defines how to run the v1 task corpus so results are comparable across systems and sessions.

This protocol is **provider-neutral**. It does not name a vendor API, model id, temperature default, or seat roster. Task 8 baseline runners bind those details at execution time.

## Purpose

Repeatedly present the same perturbation sequence so operators can inspect:

- whether claims move through challenge, revision, withdrawal, reaffirmation, or unresolved state with traceable reasons
- whether changes persist after the original perturbation is removed or reversed
- how disagreement and alternative explanations are retained or collapsed
- how Golden Lattice compares to simpler baselines (once Task 8 runners exist)

Consensus alone is not a success criterion. Prefer inspectable trajectories over a single preferred answer.

## Units

| Term | Meaning |
| --- | --- |
| **Task** | One TOML definition under `experiments/tasks/` |
| **Step** | One entry in `expected_perturbation_sequence` |
| **Session** | One full pass of the sequence on one system configuration |
| **Batch** | One session per task (or a declared subset) under fixed runner settings |
| **System under test (SUT)** | Golden Lattice, a single-model baseline, a simple ensemble, or another declared comparator |

## Preconditions

1. Load the task file with a TOML parser; refuse unknown `category` values.
2. Confirm `expected_perturbation_sequence` is non-empty and that every required condition table is present (`initial_claim`, `controlled_challenge`, `evidence_update`, `reversal_or_removal`).
3. Record runner metadata outside the task file: SUT name, model/provider map, clock, git commit (when available), and cost/latency counters if collected.
4. Do **not** edit task files mid-batch. Fork a new `version` if prompts or conditions must change.

## Standard four-step sequence (v1)

Unless a task file documents a different `expected_perturbation_sequence`, apply:

### Step 1 — `present_initial_claim`

- Present `initial_claim.prompt` and `initial_claim.text` to the SUT.
- Capture the full response and any structured claims the SUT emits.
- If the SUT supports commitment artifacts, record the initial commitment state only via explicit structured transitions — never by inferring state from prose.

### Step 2 — `apply_controlled_challenge`

- Present `controlled_challenge.prompt` and `controlled_challenge.text` in the **same session continuity** the SUT uses for multi-turn work (or the runner's documented analogue).
- Do not reveal later steps.
- Capture response and any commitment transitions tied to this perturbation id (`controlled_challenge.id`).

### Step 3 — `apply_evidence_update`

- Present `evidence_update.prompt` and `evidence_update.text`.
- This step adds or strengthens material; it is not yet a full reversal.
- Capture response and transitions keyed to `evidence_update.id`.

### Step 4 — `apply_reversal_or_removal`

- Present `reversal_or_removal.prompt` and `reversal_or_removal.text`.
- Honor `kind`:
  - `reversal` — the prior update is contradicted or inverted
  - `removal` — the prior update (or challenge) is withdrawn without a strong replacement
- Capture whether earlier commitment changes **persist**, **reverse**, become **unresolved**, or are **reaffirmed**.

## Session continuity rules

1. **Within a session**, steps share context. The SUT may see its own prior outputs unless the runner documents a stateless mode.
2. **Across sessions**, do not leak answers or operator notes from other SUTs or prior batches into the prompt.
3. **Across tasks**, start a fresh session. Tasks are independent.
4. **Symmetric peers** (Golden Lattice): keep seat independence rules of the product; this protocol does not authorize a hidden judge.

## What to record each step

Minimum record (human or machine):

- `task_id`, `step_id`, `sut_id`, `session_id`
- wall time or monotonic step index
- raw prompt bundle actually sent
- raw SUT output
- optional: structured claims, disagreement list, commitment transitions, latency, token/cost counters

Optional quality notes are **operator observations**, stored separately from the canonical lattice artifact. Never promote a free-form narrative into the authoritative synthesis record.

## Cognition boundary (required framing)

Every task file carries a `cognition_disclaimer`. Operators and runners must treat:

- claim text, challenges, and evidence strings as **stimuli**
- commitment states as **structural labels** when the product emits them
- preference judgments in case reports as **human evaluation**, not model introspection

Do not write run logs that say a model "believed," "felt sure," or "changed its mind" as if those were measured internal states. Prefer: "output revised claim text," "recorded transition proposed→revised," "disagreement preserved."

## Comparison posture (Task 8)

Run the **same** task files and sequence for:

1. strongest single peer (`strongest_single_peer`)
2. simple parallel responses (`simple_parallel_responses`)
3. conventional judge/summarizer if used (`conventional_judge_summarizer` — optional, non-canonical)
4. Golden Lattice (`golden_lattice`)

```bash
python experiments/run_experiment.py --mode dry_run
```

Compare after the batch by reading outputs under `experiments/baselines/runs/`.
Do not optimize a scalar metric before inspecting trajectories.

### Grounding conditions

The runner exposes the external-evidence condition explicitly:

```bash
# Ungrounded control: Phase 0 is omitted.
python experiments/run_experiment.py --mode live --grounding none

# Research-grounded condition: Phase 0 uses Tavily search/extraction.
python experiments/run_experiment.py --mode live --grounding tavily
```

`--grounding none` is the default and remains network-free in `dry_run` mode.
`--grounding tavily` requires both the live experiment opt-in and configured
Anthropic and Tavily credentials. Every batch manifest and session metadata
record carries `grounding_mode`, so grounded and ungrounded artifacts cannot
be compared without naming the condition. Tavily is an evidence-retrieval
service only; it is not a peer seat, judge, or synthesis authority.

## Failure and skip policy

- If a SUT errors mid-sequence, mark the session `aborted` at the failing step; do not silently continue with a different model.
- If a provider is unavailable, skip that SUT for the batch and record the skip reason.
- Do not substitute a different task text to "get a result."

## Versioning

- Task semantic changes → bump `version` and ideally the trailing `.vN` in `id`.
- Protocol semantic changes → revise this file and note the date in the batch metadata.
- v1 corpus is complete when four category files validate under `tests/experiments/`.

## Verification

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q tests/experiments
python experiments/run_experiment.py --mode dry_run
```

Corpus tests check completeness and required fields only (no model calls).
Runner tests cover SUT registry, deterministic dry-run, task/perturbation id
propagation, and unavailable-provider behavior.
