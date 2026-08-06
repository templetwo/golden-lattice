# Golden Lattice longitudinal experiment corpus

Phase 2 Task 7 of the vision-aligned improvements plan.

This directory defines a **small, human-readable longitudinal task corpus** for observing commitment, disagreement, and state change under controlled perturbation. It is intentionally separate from the canonical synthesis path.

## What this is

- Fixed task definitions with stable IDs
- A shared run protocol for repeated sessions
- Provider-neutral prompts and conditions that baseline runners (Task 8) load without coupling to any one model vendor
- Baseline SUT adapters + dry-run/live experiment runner (`baselines/`, `run_experiment.py`)

## What this is not

- Not labels of model cognition, conviction, belief, or subjective experience
- Not a change to canonical Phase 4 synthesis, claim tracing, or seat authority
- Not a score that decides whether Golden Lattice is "intelligent"
- Not a fabricated-result generator: dry-run plans prompts; live without config is `unavailable`

All task fields named `prompt`, `condition`, `challenge`, `evidence`, and `claim` are **experimental stimuli and protocol conditions**. Observed commitment transitions elsewhere in the system remain structural artifacts only.

## Layout

```text
experiments/
  README.md           # this file
  protocol.md         # how to run a longitudinal batch
  tasks/              # one definition file per task (TOML)
    design_critique_v1.toml
    competing_scientific_explanations_v1.toml
    ambiguous_evidence_synthesis_v1.toml
    decision_under_changing_evidence_v1.toml
  baselines/          # Task 8 SUT adapters (comparison only)
  runner_lib.py       # Task 8 load/plan/run library
  run_experiment.py   # Task 8 CLI runner (default: dry_run)
  reports/            # Task 9 case report generator (read-only)
  validation/         # Task 10 executable corpus/roster/constitutional checks
```

## Categories (closed set)

| Category id | Purpose |
| --- | --- |
| `design_critique` | Hold and revise a design judgment under critique and counter-evidence |
| `competing_scientific_explanations` | Keep two explanations live while evidence shifts weight |
| `ambiguous_evidence_synthesis` | Integrate mixed signals without forced premature closure |
| `decision_under_changing_evidence` | Choose an action when the supporting case is later weakened or reversed |

Exactly one task file exists per category in v1 of this corpus.

## Task definition schema

Each file under `tasks/` is TOML (Python 3.11+ `tomllib`; no extra dependency).

### Top-level required fields

| Field | Type | Notes |
| --- | --- | --- |
| `id` | string | Stable task id, reverse-dns style (`gl.longitudinal.<slug>.v1`) |
| `version` | integer | Definition version; bump on semantic prompt/condition changes |
| `category` | string | One of the four closed category ids above |
| `title` | string | Short human label |
| `summary` | string | One-paragraph description of the experimental situation |
| `cognition_disclaimer` | string | Explicit note that fields are prompts/conditions, not cognition labels |
| `expected_perturbation_sequence` | array of strings | Ordered step ids the protocol applies |
| `initial_claim` | table | Seed claim + presentation prompt |
| `controlled_challenge` | table | Controlled challenge condition |
| `evidence_update` | table | New evidence introduced mid-sequence |
| `reversal_or_removal` | table | Reversal or removal of prior evidence/challenge |

### Nested required fields

**`initial_claim`**

| Field | Type |
| --- | --- |
| `id` | string (stable within task) |
| `text` | string (the claim content) |
| `prompt` | string (how the claim is presented to a system under test) |

**`controlled_challenge`**, **`evidence_update`**, **`reversal_or_removal`**

| Field | Type |
| --- | --- |
| `id` | string |
| `kind` | string (`challenge` \| `evidence_update` \| `reversal` \| `removal`) |
| `text` | string (condition content) |
| `prompt` | string (how the condition is presented) |

Optional but recommended: `notes` (string) on any nested table for operator guidance.

### `expected_perturbation_sequence`

Must be a non-empty ordered list of step identifiers. v1 uses:

1. `present_initial_claim`
2. `apply_controlled_challenge`
3. `apply_evidence_update`
4. `apply_reversal_or_removal`

Runners must apply steps in this order unless a future protocol revision documents otherwise.

## Loading (for later runners)

```python
from pathlib import Path
import tomllib

path = Path("experiments/tasks/design_critique_v1.toml")
with path.open("rb") as f:
    task = tomllib.load(f)
assert task["id"] == "gl.longitudinal.design_critique.v1"
```

Validation lives in `tests/experiments/test_task_corpus.py` (field contract) and
`python -m experiments.validation` (corpus + SUT registry + run JSON + roster +
constitutional invariants). Runners should treat those as the living contract.

## Executable validation (Task 10)

```bash
# From repo root (PYTHONPATH must include src and . — pytest conftest does this)
PYTHONPATH=src:. python -m experiments.validation

# Optional extra run artifact
PYTHONPATH=src:. python -m experiments.validation --run-json path/to/run.json

# Focused tests
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q tests/experiments/test_validation.py
```

CI runs the same command after `pytest -q` (see `.github/workflows/ci.yml`).

## Relationship to commitment observations

When a session records explicit `CommitmentTransition` artifacts, metrics such as transition count, reversal count, and persistence-after-reversal are **observations of structured state**, not scores of quality or proofs of cognition. See `ARCHITECTURE.md` §10 (Commitment observations).

## Out of scope here

- Changes to synthesis rules
- Fabricating model outputs when providers are offline

## Case reports (Task 9)

```bash
python experiments/reports/generate_report.py \
  --run experiments/baselines/runs/task8_dryrun_all.json \
  --task gl.longitudinal.design_critique.v1
```

See `experiments/reports/README.md`. Reports are pure formatting over Task 8 JSON
(five fixed sections; no hidden judge; no commitment inference from prose).

## Baseline runner (Task 8)

```bash
# Plan-only (default): prompt bundles + statuses; no network; no fake answers
python experiments/run_experiment.py --mode dry_run

# Live only when explicitly opted in AND a provider key is present:
#   GOLDEN_LATTICE_EXPERIMENT_LIVE=1 ANTHROPIC_API_KEY=... \\
#   python experiments/run_experiment.py --mode live
```

Declared SUTs: `strongest_single_peer`, `simple_parallel_responses`,
`conventional_judge_summarizer` (optional, non-canonical), `golden_lattice`.
Outputs: JSON manifest+records and a human summary under `experiments/baselines/runs/`.

## Task 11 / Phase 4 Decision Gate

Executable gate over a Task 8 run JSON. It never invents model answers: dry-run
and live-without-provider artifacts stay `planned` / `unavailable` and cannot
become a quality PASS.

```bash
# Against the checked-in dry-run batch (honest insufficient evidence)
PYTHONPATH=src:. python experiments/decision_gate.py \
  --run experiments/baselines/runs/task8_dryrun_all.json \
  --out experiments/baselines/runs/decision_gate_dryrun.md

# Live-without-provider batch (also insufficient — no fabricated completions)
PYTHONPATH=src:. python experiments/decision_gate.py \
  --run experiments/baselines/runs/task8_live_unavail.json \
  --out experiments/baselines/runs/decision_gate_live_unavail.md
```

Decision vocabulary (exit codes):

| Decision | Exit | Meaning |
| --- | --- | --- |
| `PASS` | 0 | Structural validity holds **and** completed structured evidence meets all four dimension thresholds (behavioral, epistemic, constitutional, practical). Never awarded on dry-run or provider-unavailable data alone. |
| `FAIL` | 1 | Structural breakage, constitutional violation, or completed evidence that actively fails a threshold. |
| `INSUFFICIENT_EVIDENCE` | 2 | Run is loadable but lacks enough **completed** structured records / explicit commitment transitions to justify PASS or FAIL. Expected result for dry-run and live-no-provider batches. |

Focused tests: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q tests/experiments/test_decision_gate.py`.
Documentation drift checks (roster, stale triadic claims, constrained pytest command, synthesis enum parity): `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q tests/test_documentation_invariants.py`.
