# Case reports (Phase 2 Task 9)

Human-readable case reports over **Task 8** machine-readable run JSON
(`manifest` + `sessions`).

This package is **read-only formatting**. It does not:

- call models or a hidden generative judge
- infer commitment states from free-form prose
- fabricate answers for `planned` / `unavailable` / `error` steps
- modify canonical Phase 4 synthesis, attribution, or seat authority

## Layout

```text
experiments/reports/
  README.md              # this file
  __init__.py            # re-exports
  generator.py           # load_run_json, generate_case_report, write_case_report
  generate_report.py     # CLI
```

## Required report sections

Every report emits these sections, clearly separated:

| # | Section | Label / rule |
| --- | --- | --- |
| 1 | **Canonical annotated lattice artifact** | Labeled **canonical**. Shown only when the run JSON supplies a structured artifact (`canonical_annotated_lattice` and aliases on session/step.structured). Otherwise **not present / unavailable**. |
| 2 | **Reader-facing interpretation** | Labeled **non-canonical**. Optional operator/human reading from `reader_facing_interpretation` (and aliases). Otherwise **unavailable**. |
| 3 | **Commitment timeline** | **Explicit `commitment_transitions` records only**. Never scraped from `raw_output` prose. Empty → **unavailable**. |
| 4 | **Preserved disagreement list** | Shown when `preserved_disagreements` / `disagreement_list` (session or step.structured) is supplied. Otherwise **unavailable**. |
| 5 | **Baseline comparison** | Table across all four required SUTs: `strongest_single_peer`, `simple_parallel_responses`, `conventional_judge_summarizer`, `golden_lattice`. Statuses and null outputs are preserved honestly. |

## Callable API

```python
from pathlib import Path
from experiments.reports import (
    load_run_json,
    generate_case_report,
    write_case_report,
)

payload = load_run_json("experiments/baselines/runs/task8_dryrun_all.json")
md = generate_case_report(
    payload,
    task_id="gl.longitudinal.design_critique.v1",
)
write_case_report(
    payload,
    Path("experiments/reports/out/design_critique.md"),
    task_id="gl.longitudinal.design_critique.v1",
)
```

## CLI

```bash
# Full run (all tasks in the JSON), print to stdout
python experiments/reports/generate_report.py \
  --run experiments/baselines/runs/task8_dryrun_all.json

# One task → file
python experiments/reports/generate_report.py \
  --run experiments/baselines/runs/task8_dryrun_all.json \
  --task gl.longitudinal.design_critique.v1 \
  --out experiments/reports/out/design_critique.md
```

## Input contract (from Task 8)

The generator expects the Task 8 batch shape:

- `manifest`: `run_id`, `mode`, `created_at`, `sut_ids`, `task_ids`, …
- `sessions[]`: `session_id`, `task_id`, `sut_id`, `status`, `steps[]`, optional `notes` / `metadata` / `unavailable_reason`
- `steps[]`: `step_id`, `perturbation_id`, `status`, `prompt_bundle`, optional `raw_output`, `structured`, `commitment_transitions`, `latency_ms`, `cost_usd`, `reason`

Status vocabulary: `planned` | `completed` | `unavailable` | `skipped` | `aborted` | `error`.

### Optional structured fields the report surfaces

| Field | Where | Section |
| --- | --- | --- |
| `canonical_annotated_lattice` (aliases: `annotated_lattice`, …) | session or `step.structured` | §1 canonical |
| `reader_facing_interpretation` (aliases) | session, `metadata`, or `step.structured` | §2 non-canonical |
| `commitment_transitions` | session or step (list of dicts) | §3 timeline |
| `preserved_disagreements` / `disagreement_list` | session or `step.structured` | §4 disagreements |

## Cognition / honesty boundary

- Commitment states are **structural labels** from explicit artifacts only.
- Dry-run (`planned`) and live-without-config (`unavailable`) reports must remain legible without inventing model answers.
- `conventional_judge_summarizer` remains optional and non-canonical as a **baseline comparator**; this report never feeds it into Golden Lattice synthesis.

## Tests

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q tests/experiments/test_case_reports.py
```

## Out of scope

- Changes to `src/golden_lattice/synthesis/*`
- Hidden judges or quality scorers
- Replacing Task 8 JSON schema
