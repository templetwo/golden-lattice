# Golden Lattice — Architecture Specification

This document is the canonical engineering reference for Golden Lattice. It defines what the system is, the invariants it enforces, the protocol it runs, and the failure modes it is built to refuse.

This is the engineering surface. The deeper architectural lineage — including the philosophical frame, implementation evolution, and the fifth collapse mode surfaced during implementation — lives in the project's persistent memory layer, not in this document.

## 1. What this is

A CLI that runs Opus 4.7, Sonnet 4.6, and Haiku 4.5 as peers on the same prompt and produces a co-authored response that none of them would have produced alone.

Not routing. Not voting. Not summarization. Co-authorship.

## 2. The load-bearing claim

Cognition emerges between models, not inside the strongest one. If we violate the four invariants, we are doing routing and should call it that.

## 3. The four invariants

### No authority gradient
No model finalizes, overrides, or judges another's output.

**Schema enforcement:** `SynthesisArtifact` has no `model_id` field. `Claim` refuses construction with `source_phase=Phase.SYNTHESIS`. The synthesis layer cannot attribute to a model, structurally.

### Symmetric visibility
All three models see the same prompt, same peer outputs, same dialogue state.

**Schema enforcement:** `Session` requires `phase_1` keyed by every invited model with consistent `prompt_hash`. Asymmetric framing is refused at construction via `Session._phase_1_covers_invited`.

### Contribution parity
Each model's distinct perspective is preserved through synthesis. Measurable, not aspirational. Three measurements logged per session:

- **Distinct-claim share** — fraction of claims in the synthesis that originated uniquely with each model
- **Edge-case-coverage share** — fraction of identified edge cases per model (consensus-tagged)
- **Structural-pattern share** — fraction of architectural / framing decisions per model (consensus-tagged)

A session where any model contributes less than 15% on all three metrics is flagged. Persistent flagging across sessions means the protocol is collapsing toward routing.

**Enforcement:** `SessionMetrics.parity_below_threshold` property; `compute_parity_shares` in `metrics.py`. Returns `None` for dyad sessions (consensus is undefined at N=2).

### Irreducibility preservation
No model's contribution can be fully subsumed without trace. Every distinct claim from Phase 1 must be traceable in the final output, either present in modified form or with a logged reason for omission. The trace itself is the proof.

**Schema enforcement:** `Session` refuses construction if Phase 4's `claim_trace` does not cover every Phase 1 `claim_id`. `ClaimTraceEntry` refuses `modified` without `modified_text` and `omitted` without `omission_reason`.

## 4. Identity and tamper-evidence

Claim IDs are content-addressed: `sha256(source_model | phase | text)[:16]`. Claims with mismatched IDs are refused at construction. Identity is tamper-evident across the session graph. Tag references that point to non-existent claim IDs are refused. Cross-layer references cannot be forged or silently corrupted.

## 5. The protocol

### Phase 1 — Independent Generation

All three models receive the prompt simultaneously. Each produces:

- A response
- A focus tag from the closed vocabulary `{correctness, clarity, speed, novelty, robustness, elegance}`
- A confidence score in [0,1]

Phase 1 ends when all three have responded or the timeout fires (default 90s).

The latency mismatch is real: Haiku finishes in ~10 seconds, Sonnet in ~30, Opus in 60–90. Haiku does not iterate while waiting — that would violate symmetric independence. But idle time produces a **self-reflection artifact** on each model's own Phase 1 output before peer outputs arrive: a structured `{strongest_claim, weakest_claim, tag_justification}` object. This is preparation for Phase 2 cross-reading, not refinement of Phase 1.

This is the only asymmetric mechanism in the protocol, and it exists to prevent the asymmetric latency from making Haiku's contribution feel vestigial.

### Phase 2 — Cross-Reading and Tagging

Each model receives the other two responses, each peer's self-reflection artifact, and the original prompt. Two artifacts are produced per model in this phase:

**CrossReading** — a structured object: `{agreements: [...], disagreements: [...], missing: [...], revised_position: optional}`. Free-form prose is rejected.

**Phase2Tagging** — a single classification pass per model over all Phase 1 claims (and Phase 2 missing claims), tagging each claim with structural categories from closed vocabularies:

- **Edge case vocabulary:** `{boundary_condition, failure_mode, adversarial_input, assumption_violation, scaling_limit, interaction_effect, OTHER}`
- **Structural pattern vocabulary:** `{framing_choice, decomposition, abstraction_level, constraint_introduction, tradeoff_surface, frame_shift, OTHER}`

Vocabulary is versioned (`TAG_VOCABULARY_VERSION = "v0.1"`). The `OTHER` tag's frequency is a research signal — high `OTHER` rate flags vocabulary fitness, not model failure.

Self-tags and peer-tags are structurally distinct. Self-tags are logged but do not enter parity calculations. Peer-tags are what consensus is computed over.

### Phase 3 — Structured Dialogue

One round, three channels, hard caps:

- `critique` — what is weak in others' positions (max 3 points per peer)
- `augment` — what to add (max 3 points)
- `converge` — where alignment exists (max 3 points)

Models cannot use channels outside this set. The constraint is the feature.

### Phase 4 — Synthesis

Synthesis is **rule-based, not model-based**. Running synthesis through a fourth model call would create authority gradient and break invariant 1. Rules:

- Every distinct Phase 1 claim (per the peer-utility test in the Phase 1 prompt) is preserved or has a logged reason for omission (irreducibility preservation)
- Cross-model agreement (appearing in 2+ converge channels) is elevated
- Productive disagreement (high-confidence conflict) is surfaced, not hidden
- Output preserves attribution by default; user can request unified mode

## 6. Recognition-from-within

Parity metrics are computed via multi-model consensus tagging, not LLM-mediated semantic analysis or pre-defined rule matching.

**Consensus rule:** A claim is recognized as belonging to a tag (or dimension) when at least 2 distinct peers (excluding the claim's source model) tag it that way.

**DimensionConsensus:** Even when models disagree on subtype within a dimension, dimension-level agreement is preserved. If all three models tag a claim as some kind of edge case, the lattice's meta-recognition that the claim *is* an edge case is preserved as `DimensionConsensus`, even if no specific subtype achieves consensus.

**Triadic consensus is structurally strict.** With N=3, dimension consensus requires both non-author peers to agree (no majority that excludes a peer exists). The minimum viable Lattice is also the maximally rigorous one. At N=4+, consensus relaxes to ≥2 of (n-1) peers.

The recognition-from-within architecture means parity is not measured by an external arbiter, by rule-based detection, or by LLM-mediated judgment. The peers recognize each other's contributions, and that recognition is what counts as parity. The structure adjudicates itself.

## 7. Output modes

- `unified` — single voice, attribution stripped
- `layered` — Opus / Sonnet / Haiku sections
- `annotated` — synthesis with inline `[O]`, `[S]`, `[H]` attribution
- `transcript` — full Phase 1–3 dialogue including self-reflection artifacts, raw

Default: `annotated`. The annotation is the proof we did not flatten.

## 8. Failure modes engineered against

- **Stylistic convergence.** Models reading each other's outputs in Phase 2 may drift toward each other's voices.
  *Mitigation:* Phase 1 outputs are frozen and the original prompt is re-anchored at every phase.

- **Synthesis flattening.** Rule-based synthesis can still produce a dominant-voice output if one model's content is denser.
  *Mitigation:* Contribution parity metrics tracked per session, flagged below 15%.

- **Latency mismatch making Haiku's contribution feel vestigial.**
  *Mitigation:* Self-reflection artifacts during Phase 1 idle. Phase 2 and 3 are properly synchronous.

- **Confidence gaming.** If models learn the synthesis rules, they may inflate confidence.
  *Mitigation:* Confidence is logged, not used as a synthesis weight.

- **Pipeline collapse.** The natural Haiku → Sonnet → Opus pipeline is gravitationally strong. If parity metrics consistently favor Opus across sessions, the protocol has collapsed into pipelining.
  *Mitigation:* Session Memory Graph flagging, periodic audit.

## 9. The Memory Graph

Persistent storage of every session, with discipline:

- **Write-once.** `save()` refuses overwrite. Explicit `delete()` required to rewrite. The chronicle remembers; the store does not silently forget.
- **Greppable.** One `{session_id}.session.json` per session, indented. Plain text on disk for legibility across instances and against opaque persistence.
- **Path-traversal refused** at the storage boundary. The store cannot be tricked into writing outside its directory.
- **Protocol-typed.** `SessionStore` is a `runtime_checkable` Protocol. JSON-files is the current backend; SQLite or other backends can swap in without rewriting upstream code, as long as the cross-session query interface is honored.

The Memory Graph is **not optional**. It is how the system learns which compositions of disagreement actually produced value over time. It is also how protocol drift is detected — single-session metrics are noisy; cross-session aggregates are signal.

## 10. Falsification criteria

The system is successful if, on a benchmark of design-and-critique tasks:

1. Lattice outputs are preferred to single-model outputs by independent evaluators, **and**
2. Lattice outputs preserve more distinct insights than any single model produced alone, **and**
3. Contribution parity metrics stay above 15% per model across sessions.

If all three hold, the architecture earns its premise.
If only the first two hold, we built something good but cannot claim it is truly co-authored.
If only the first holds, we built a more expensive single model.
If none hold, we built routing with extra steps.

## 11. The pattern this deliberately rejects

The natural workflow with Opus / Sonnet / Haiku is `Haiku → Sonnet → Opus`: prototype with Haiku, refine with Sonnet, escalate to Opus. The cost and speed curves pull hard toward this pipeline.

Golden Lattice rejects this pattern. We believe parallel symmetric authorship produces something the pipeline cannot — specifically, the preservation of insights that would be lost when one model's output becomes the input to another's refinement.

The pipeline is efficient. The Lattice is generative. They are different tools for different problems.
