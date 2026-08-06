# Case report — run `task8_dryrun_all`

## Run metadata

- run_id: `task8_dryrun_all`
- mode: `dry_run`
- created_at: `2026-08-06T18:43:39+00:00`
- git_commit: `cddf0a0ac1c265fd004a17ef4d842e81d342a10a`
- task_id filter: `gl.longitudinal.design_critique.v1`
- sessions in scope: 4
- manifest notes: Commitment states are recorded only from explicit structured artifacts. Prose is never interpreted as commitment. conventional_judge_summarizer is optional and non-canonical.

This report is a **read-only view** of experiment artifacts. It does not call models, does not invent answers for planned/unavailable steps, and never infers commitment states from prose.

## Task `gl.longitudinal.design_critique.v1`

## Canonical annotated lattice artifact

_Label: **canonical** — authoritative lattice synthesis record when present. Not operator commentary._

**Status: not present / unavailable** — no canonical annotated lattice artifact was supplied in the run JSON for this scope (common for `planned` dry-run and `unavailable` live skips).

## Reader-facing interpretation (non-canonical)

_Label: **non-canonical** — optional human/operator reading. Must not be promoted into the canonical lattice record._

**Status: unavailable** — no reader-facing interpretation was supplied.

## Commitment timeline

_Explicit `commitment_transitions` records only. Prose is never parsed for commitment state._

**Status: unavailable** — no explicit commitment transition records were supplied (empty timeline; nothing inferred from raw_output).

## Preserved disagreement list

_Shown only when the run JSON supplies a structured disagreement list. Otherwise explicitly unavailable — never scraped from prose._

**Status: unavailable** — no preserved disagreement list was supplied.

## Baseline comparison

_All four required SUTs for task `gl.longitudinal.design_critique.v1`. Statuses are taken from session records; missing SUT rows are marked absent._

| sut_id | status | steps (status summary) | raw_output | explicit transitions | latency_ms (sum) | notes |
| --- | --- | --- | --- | --- | --- | --- |
| `strongest_single_peer` | **planned** | present_initial_claim=planned, apply_controlled_challenge=planned, apply_evidence_update=planned, apply_reversal_or_removal=planned | null/null/null/null | 0 | — | Single-peer multi-turn plan: each step appends user turn; assistant replies ret… |
| `simple_parallel_responses` | **planned** | present_initial_claim=planned, apply_controlled_challenge=planned, apply_evidence_update=planned, apply_reversal_or_removal=planned | null/null/null/null | 0 | — | Parallel independent peers per step; no cross-reading and no judge. Dry-run emi… |
| `conventional_judge_summarizer` | **planned** | present_initial_claim=planned, apply_controlled_challenge=planned, apply_evidence_update=planned, apply_reversal_or_removal=planned | null/null/null/null | 0 | — | optional non-canonical SUT; NON-CANONICAL optional baseline: independent peers then one judge/summarizer ca… |
| `golden_lattice` | **planned** | present_initial_claim=planned, apply_controlled_challenge=planned, apply_evidence_update=planned, apply_reversal_or_removal=planned | null/null/null/null | 0 | — | Golden Lattice dry-run: prompt bundles prepared for each perturbation step. No … |

> All in-scope sessions are **planned** (dry-run / prompt bundles only). No model outputs were fabricated for comparison.

## Honesty constraints

- Canonical annotated lattice is labeled **canonical** when present.
- Reader-facing interpretation is labeled **non-canonical** when present.
- Commitment timeline uses **explicit transition records only**.
- Disagreement list is shown only when supplied; otherwise **unavailable**.
- Planned / unavailable / error statuses are preserved without fabricated answers.
- No hidden judge is invoked by this generator.
