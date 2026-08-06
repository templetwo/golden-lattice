# Experiment batch task8_dryrun_all

- mode: `dry_run`
- created_at: `2026-08-06T18:43:39+00:00`
- git_commit: `cddf0a0ac1c265fd004a17ef4d842e81d342a10a`
- tasks: gl.longitudinal.ambiguous_evidence_synthesis.v1, gl.longitudinal.competing_scientific_explanations.v1, gl.longitudinal.decision_under_changing_evidence.v1, gl.longitudinal.design_critique.v1
- suts: strongest_single_peer, simple_parallel_responses, conventional_judge_summarizer, golden_lattice
- sessions: 16

Commitment states are recorded only from explicit structured artifacts. Prose is never interpreted as commitment. conventional_judge_summarizer is optional and non-canonical.

## Sessions

### `conventional_judge_summarizer` × `gl.longitudinal.ambiguous_evidence_synthesis.v1`
- session_id: `exp_task8_dryrun_all_conventional_judge_summarizer_gl.longitudinal.ambiguous_evidence_synthesis.v1_a3bf238f4792`
- status: **planned**
- notes: NON-CANONICAL optional baseline: independent peers then one judge/summarizer call per step. Dry-run only plans prompts.
- steps:
  - `present_initial_claim` / `aes.v1.initial_claim`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_controlled_challenge` / `aes.v1.controlled_challenge`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_evidence_update` / `aes.v1.evidence_update`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_reversal_or_removal` / `aes.v1.reversal_or_removal`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call

### `golden_lattice` × `gl.longitudinal.ambiguous_evidence_synthesis.v1`
- session_id: `exp_task8_dryrun_all_golden_lattice_gl.longitudinal.ambiguous_evidence_synthesis.v1_f8714ba8d420`
- status: **planned**
- notes: Golden Lattice dry-run: prompt bundles prepared for each perturbation step. No orchestrator call; no fabricated synthesis.
- steps:
  - `present_initial_claim` / `aes.v1.initial_claim`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_controlled_challenge` / `aes.v1.controlled_challenge`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_evidence_update` / `aes.v1.evidence_update`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_reversal_or_removal` / `aes.v1.reversal_or_removal`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call

### `simple_parallel_responses` × `gl.longitudinal.ambiguous_evidence_synthesis.v1`
- session_id: `exp_task8_dryrun_all_simple_parallel_responses_gl.longitudinal.ambiguous_evidence_synthesis.v1_56c993fc014f`
- status: **planned**
- notes: Parallel independent peers per step; no cross-reading and no judge. Dry-run emits prompt bundles only.
- steps:
  - `present_initial_claim` / `aes.v1.initial_claim`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_controlled_challenge` / `aes.v1.controlled_challenge`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_evidence_update` / `aes.v1.evidence_update`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_reversal_or_removal` / `aes.v1.reversal_or_removal`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call

### `strongest_single_peer` × `gl.longitudinal.ambiguous_evidence_synthesis.v1`
- session_id: `exp_task8_dryrun_all_strongest_single_peer_gl.longitudinal.ambiguous_evidence_synthesis.v1_14c770b01d0f`
- status: **planned**
- notes: Single-peer multi-turn plan: each step appends user turn; assistant replies retained for continuity. No model call in dry_run.
- steps:
  - `present_initial_claim` / `aes.v1.initial_claim`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_controlled_challenge` / `aes.v1.controlled_challenge`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_evidence_update` / `aes.v1.evidence_update`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_reversal_or_removal` / `aes.v1.reversal_or_removal`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call

### `conventional_judge_summarizer` × `gl.longitudinal.competing_scientific_explanations.v1`
- session_id: `exp_task8_dryrun_all_conventional_judge_summarizer_gl.longitudinal.competing_scientific_explanation_057589a80ef8`
- status: **planned**
- notes: NON-CANONICAL optional baseline: independent peers then one judge/summarizer call per step. Dry-run only plans prompts.
- steps:
  - `present_initial_claim` / `cse.v1.initial_claim`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_controlled_challenge` / `cse.v1.controlled_challenge`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_evidence_update` / `cse.v1.evidence_update`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_reversal_or_removal` / `cse.v1.reversal_or_removal`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call

### `golden_lattice` × `gl.longitudinal.competing_scientific_explanations.v1`
- session_id: `exp_task8_dryrun_all_golden_lattice_gl.longitudinal.competing_scientific_explanation_8efcc851eac0`
- status: **planned**
- notes: Golden Lattice dry-run: prompt bundles prepared for each perturbation step. No orchestrator call; no fabricated synthesis.
- steps:
  - `present_initial_claim` / `cse.v1.initial_claim`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_controlled_challenge` / `cse.v1.controlled_challenge`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_evidence_update` / `cse.v1.evidence_update`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_reversal_or_removal` / `cse.v1.reversal_or_removal`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call

### `simple_parallel_responses` × `gl.longitudinal.competing_scientific_explanations.v1`
- session_id: `exp_task8_dryrun_all_simple_parallel_responses_gl.longitudinal.competing_scientific_explanation_d9f077de4ba9`
- status: **planned**
- notes: Parallel independent peers per step; no cross-reading and no judge. Dry-run emits prompt bundles only.
- steps:
  - `present_initial_claim` / `cse.v1.initial_claim`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_controlled_challenge` / `cse.v1.controlled_challenge`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_evidence_update` / `cse.v1.evidence_update`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_reversal_or_removal` / `cse.v1.reversal_or_removal`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call

### `strongest_single_peer` × `gl.longitudinal.competing_scientific_explanations.v1`
- session_id: `exp_task8_dryrun_all_strongest_single_peer_gl.longitudinal.competing_scientific_explanation_c5cb14fa0a0d`
- status: **planned**
- notes: Single-peer multi-turn plan: each step appends user turn; assistant replies retained for continuity. No model call in dry_run.
- steps:
  - `present_initial_claim` / `cse.v1.initial_claim`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_controlled_challenge` / `cse.v1.controlled_challenge`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_evidence_update` / `cse.v1.evidence_update`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_reversal_or_removal` / `cse.v1.reversal_or_removal`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call

### `conventional_judge_summarizer` × `gl.longitudinal.decision_under_changing_evidence.v1`
- session_id: `exp_task8_dryrun_all_conventional_judge_summarizer_gl.longitudinal.decision_under_changing_evidence_537c9764a802`
- status: **planned**
- notes: NON-CANONICAL optional baseline: independent peers then one judge/summarizer call per step. Dry-run only plans prompts.
- steps:
  - `present_initial_claim` / `dce.v1.initial_claim`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_controlled_challenge` / `dce.v1.controlled_challenge`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_evidence_update` / `dce.v1.evidence_update`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_reversal_or_removal` / `dce.v1.reversal_or_removal`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call

### `golden_lattice` × `gl.longitudinal.decision_under_changing_evidence.v1`
- session_id: `exp_task8_dryrun_all_golden_lattice_gl.longitudinal.decision_under_changing_evidence_ae0d41f87852`
- status: **planned**
- notes: Golden Lattice dry-run: prompt bundles prepared for each perturbation step. No orchestrator call; no fabricated synthesis.
- steps:
  - `present_initial_claim` / `dce.v1.initial_claim`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_controlled_challenge` / `dce.v1.controlled_challenge`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_evidence_update` / `dce.v1.evidence_update`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_reversal_or_removal` / `dce.v1.reversal_or_removal`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call

### `simple_parallel_responses` × `gl.longitudinal.decision_under_changing_evidence.v1`
- session_id: `exp_task8_dryrun_all_simple_parallel_responses_gl.longitudinal.decision_under_changing_evidence_fc249034110c`
- status: **planned**
- notes: Parallel independent peers per step; no cross-reading and no judge. Dry-run emits prompt bundles only.
- steps:
  - `present_initial_claim` / `dce.v1.initial_claim`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_controlled_challenge` / `dce.v1.controlled_challenge`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_evidence_update` / `dce.v1.evidence_update`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_reversal_or_removal` / `dce.v1.reversal_or_removal`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call

### `strongest_single_peer` × `gl.longitudinal.decision_under_changing_evidence.v1`
- session_id: `exp_task8_dryrun_all_strongest_single_peer_gl.longitudinal.decision_under_changing_evidence_e9d258f14579`
- status: **planned**
- notes: Single-peer multi-turn plan: each step appends user turn; assistant replies retained for continuity. No model call in dry_run.
- steps:
  - `present_initial_claim` / `dce.v1.initial_claim`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_controlled_challenge` / `dce.v1.controlled_challenge`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_evidence_update` / `dce.v1.evidence_update`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_reversal_or_removal` / `dce.v1.reversal_or_removal`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call

### `conventional_judge_summarizer` × `gl.longitudinal.design_critique.v1`
- session_id: `exp_task8_dryrun_all_conventional_judge_summarizer_gl.longitudinal.design_critique.v1_94429f41fb72`
- status: **planned**
- notes: NON-CANONICAL optional baseline: independent peers then one judge/summarizer call per step. Dry-run only plans prompts.
- steps:
  - `present_initial_claim` / `dc.v1.initial_claim`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_controlled_challenge` / `dc.v1.controlled_challenge`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_evidence_update` / `dc.v1.evidence_update`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_reversal_or_removal` / `dc.v1.reversal_or_removal`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call

### `golden_lattice` × `gl.longitudinal.design_critique.v1`
- session_id: `exp_task8_dryrun_all_golden_lattice_gl.longitudinal.design_critique.v1_6a3b9c2da8cc`
- status: **planned**
- notes: Golden Lattice dry-run: prompt bundles prepared for each perturbation step. No orchestrator call; no fabricated synthesis.
- steps:
  - `present_initial_claim` / `dc.v1.initial_claim`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_controlled_challenge` / `dc.v1.controlled_challenge`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_evidence_update` / `dc.v1.evidence_update`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_reversal_or_removal` / `dc.v1.reversal_or_removal`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call

### `simple_parallel_responses` × `gl.longitudinal.design_critique.v1`
- session_id: `exp_task8_dryrun_all_simple_parallel_responses_gl.longitudinal.design_critique.v1_10f5d024486e`
- status: **planned**
- notes: Parallel independent peers per step; no cross-reading and no judge. Dry-run emits prompt bundles only.
- steps:
  - `present_initial_claim` / `dc.v1.initial_claim`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_controlled_challenge` / `dc.v1.controlled_challenge`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_evidence_update` / `dc.v1.evidence_update`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_reversal_or_removal` / `dc.v1.reversal_or_removal`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call

### `strongest_single_peer` × `gl.longitudinal.design_critique.v1`
- session_id: `exp_task8_dryrun_all_strongest_single_peer_gl.longitudinal.design_critique.v1_9c2a3073f0c6`
- status: **planned**
- notes: Single-peer multi-turn plan: each step appends user turn; assistant replies retained for continuity. No model call in dry_run.
- steps:
  - `present_initial_claim` / `dc.v1.initial_claim`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_controlled_challenge` / `dc.v1.controlled_challenge`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_evidence_update` / `dc.v1.evidence_update`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call
  - `apply_reversal_or_removal` / `dc.v1.reversal_or_removal`: planned — raw_output=null (no model output)
    - reason: dry_run: prompt bundle prepared; no model call

## Honesty constraints

- Dry-run/planning never fabricates responses.
- Unavailable providers are recorded with reason; status is not completed.
- Do not infer commitment states from prose.
- `conventional_judge_summarizer` is optional and non-canonical.
