# Golden Lattice — Architecture Specification

This document is the canonical engineering reference for Golden Lattice. It defines what the system is, the invariants it enforces, the protocol it runs, and the failure modes it is built to refuse.

This is the engineering surface. The deeper architectural lineage — including the philosophical frame, implementation evolution, and the fifth collapse mode surfaced during implementation — lives in the project's persistent memory layer, not in this document.

## 1. What this is

A CLI that runs Claude Fable 5, Claude Opus 5, Claude Sonnet 5, and Claude Haiku 4.5 as peers on the same prompt and produces a co-authored response that none of them would have produced alone.

Not routing. Not voting. Not summarization. Co-authorship.

## 2. The load-bearing claim

Cognition emerges between models, not inside the strongest one. If we violate the four invariants, we are doing routing and should call it that.

## 3. The four invariants

### No authority gradient
No model finalizes, overrides, or judges another's output.

**Schema enforcement:** `SynthesisArtifact` has no `model_id` field. `Claim` refuses construction with `source_phase=Phase.SYNTHESIS`. The synthesis layer cannot attribute to a model, structurally.

### Symmetric visibility
All invited models see the same prompt, same peer outputs, same dialogue state.

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

**Feed-grounded claims (Phase 0).** Claims that derive from Phase 0 investigation results carry `tool_provenance` references to feed entries. The irreducibility refusal covers feed-grounded claims identically to prior-grounded ones — the trace is the proof, and what got investigated is part of the trace. Tool-provenance references that do not resolve to a feed entry in `Session.phase_0.feed` are refused at construction.

## 4. Identity and tamper-evidence

Claim IDs are content-addressed: `sha256(source_model | phase | text)[:16]`. Claims with mismatched IDs are refused at construction. Identity is tamper-evident across the session graph. Tag references that point to non-existent claim IDs are refused. Cross-layer references cannot be forged or silently corrupted.

## 5. The protocol

The protocol has five phases. Phase 0 is the investigation phase added in this amendment; Phases 1–4 are the original specification. Phase 0 is optional: sessions that need no external evidence skip directly to Phase 1 with a minimal grounding-only feed.

### Phase 0 — Investigation

Some questions require evidence the lattice does not have. Phase 0 is the phase where the lattice acquires it, before independent generation begins. Added in this amendment; see chronicle entries `golden-lattice,phase-0,decisions-locked` for the design's lineage.

**Temporal grounding precondition.** Before any model proposes any investigation, the orchestrator deterministically reads local date/time (`INVESTIGATION_TIMEZONE = "America/New_York"`) and seeds it as the first entry of the shared evidence feed (`DateTimeGrounding`). This is not a tool call, not a model action, and not in the investigation cap accounting. It is a deterministic constant that preconditions the phase — same constitutional category as the prompt re-anchoring of §8 (no authority gradient because no model decides it). Traces to Anthony's standing principle: ground temporally before acting; session texture is not elapsed real time.

**Collective propose-and-union.** Each model independently receives the prompt and proposes up to `INVESTIGATION_CAP = 3` investigations (search queries it wants answered before generating). No peer visibility during proposal — structurally identical to Phase 1 independence. Proposals are combined by **rule-based exact/structural union**, deduplicated by canonical form, never by semantic-equivalence judgment. Semantic merge would require an adjudicator, reintroducing the authority gradient invariant 1 refuses. Redundant searches are a cost, not an invariant breach.

**Cap.** Each model proposes at most three investigations. Flat, per-model, not differentiated by model identity. Differentiated USE is permitted (Haiku may make three broad probes while Opus makes one deep one); differentiated BUDGET would rebuild the Haiku → Sonnet → Opus pipeline through resource allocation and is refused. The cap is a cost lever, not a contribution lever; the temporal grounding entry is excluded from this count per the locked design.

**Execution.** The orchestrator executes the deduplicated union of queries via a **non-model search executor**. Each successful result becomes a `SearchResult` feed entry; a failed search becomes a typed `FailedSearch` feed entry that all peers see. Failed evidence is itself shared evidence — §8 no-silent-failures. Precondition failures (credit balance, network unavailable at startup) remain orchestrator-layer aborts (`OrchestratorProviderError`), not feed entries.

**Freeze.** The feed is frozen before Phase 1 begins, mapping onto §8's existing freeze discipline. Phase 1 then proceeds: independent generation over prompt PLUS frozen shared feed, identical for all four default models.

**Empty union is valid.** If no model proposes investigations, the feed contains only the `DateTimeGrounding` entry. The session proceeds directly to Phase 1 with the minimal feed. Some questions need no investigation; the architecture does not force one (a degenerate-but-clean path, the same way dyad parity returning `None` is a clean state).

**Tool-provenance on `Claim`.** Claims that ground in feed entries carry `tool_provenance` references to those entries. Invariant 4 covers feed-grounded claims identically to prior-grounded — the trace itself is the proof, and what got investigated is part of the trace.

**Tool web extensibility.** Internet search is node 0 of an extensible tool graph. Future tools (file read, code execution, MCP, etc.) are added as new feed-entry types with their own typed schema, not as branches in a fixed list. Any tool's failure mode must produce a typed feed entry; any tool's results must be exact/structural dedup-able.

### Phase 1 — Independent Generation

All invited models receive the prompt simultaneously. Each produces:

- A response
- A focus tag from the closed vocabulary `{correctness, clarity, speed, novelty, robustness, elegance}`
- A confidence score in [0,1]

Phase 1 ends when all invited models have responded or the timeout fires (default 90s).

The latency mismatch is real: Haiku finishes in ~10 seconds, Sonnet in ~30, Opus in 60–90. Haiku does not iterate while waiting — that would violate symmetric independence. But idle time produces a **self-reflection artifact** on each model's own Phase 1 output before peer outputs arrive: a structured `{strongest_claim, weakest_claim, tag_justification}` object. This is preparation for Phase 2 cross-reading, not refinement of Phase 1.

This is the only asymmetric mechanism in the protocol, and it exists to prevent the asymmetric latency from making Haiku's contribution feel vestigial.

### Phase 2 — Cross-Reading and Tagging

Each model receives every other invited peer's response, each peer's self-reflection artifact, and the original prompt. Two artifacts are produced per model in this phase:

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

Synthesis is **rule-based, not model-based**. Running synthesis through an additional model call would create authority gradient and break invariant 1. The closed `SynthesisRule` vocabulary in `src/golden_lattice/memory_graph/base.py` is the only legal set of rule ids a `SynthesisArtifact.synthesis_rules_applied` tuple may carry:

| `SynthesisRule` value | Role |
| --- | --- |
| `irreducibility_preservation` | Every distinct Phase 1 claim is preserved or has a logged reason for omission |
| `agreement_elevation` | Cross-model agreement (appearing in 2+ converge channels) is elevated |
| `disagreement_surfacing` | Productive disagreement (high-confidence conflict) is surfaced, not hidden |
| `attribution_preservation` | Output preserves attribution by default; user can request unified mode |

Operational detail under those rules:

- Every distinct Phase 1 claim (per the peer-utility test in the Phase 1 prompt) is preserved or has a logged reason for omission (`irreducibility_preservation`)
- A claim disagreed-with by at least two non-author peers through any disagreement channel (Phase 2 cross-reading or Phase 3 critique turn) is preserved with an inline `[DISPUTED]` hedge naming the disputers and excerpting their reasons — the symmetric counter to §6's consensus rule, so the cross-reading + dialogue audit can't be dropped silently at the synthesis seam. Phase 2 reasons take priority over Phase 3 when the same peer disputes through both channels
- Cross-model agreement (appearing in 2+ converge channels) is elevated (`agreement_elevation`)
- Productive disagreement (high-confidence conflict) is surfaced, not hidden (`disagreement_surfacing`)
- Output preserves attribution by default (`attribution_preservation`); user can request unified mode

## 6. Recognition-from-within

Parity metrics are computed via multi-model consensus tagging, not LLM-mediated semantic analysis or pre-defined rule matching.

**Consensus rule:** A claim is recognized as belonging to a tag (or dimension) when at least 2 distinct peers (excluding the claim's source model) tag it that way.

**DimensionConsensus:** Even when models disagree on subtype within a dimension, dimension-level agreement is preserved. If all invited models tag a claim as some kind of edge case, the lattice's meta-recognition that the claim *is* an edge case is preserved as `DimensionConsensus`, even if no specific subtype achieves consensus.

**Consensus scales by roster size.** With N=3, dimension consensus requires both non-author peers to agree. At N=4+, consensus requires ≥2 of (n-1) peers. The default four-seat roster therefore preserves strict peer recognition without making every additional seat a unanimity veto.

The recognition-from-within architecture means parity is not measured by an external arbiter, by rule-based detection, or by LLM-mediated judgment. The peers recognize each other's contributions, and that recognition is what counts as parity. The structure adjudicates itself.

## 7. Output modes

- `unified` — single voice, attribution stripped
- `layered` — Fable / Opus / Sonnet / Haiku sections
- `annotated` — synthesis with inline `[F]`, `[O]`, `[S]`, `[H]` attribution
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

- **Confabulation when evidence is missing.** A model asked a question that requires external evidence may fabricate plausible-sounding content rather than refuse.
  *Mitigation:* Phase 0 (Investigation) produces a shared evidence feed before independent generation. Models that need information they don't have can propose investigations; results land in the feed all peers see. Surfaced empirically: `session_20260516_065836_1f635cdf` (first live run) produced a co-authored refusal because the lattice could not investigate a GitHub URL — the architecture's integrity property fired, and Phase 0 is the structural remedy.

- **Semantic-merge adjudication of investigation proposals.** Two models proposing similar-but-not-identical searches could in principle be merged "intelligently." Doing so requires a model or rule to judge equivalence, which is the authority gradient invariant 1 refuses.
  *Mitigation:* Phase 0 union is exact/structural only. Redundant tool calls are a cost, not an invariant breach. The cost is bounded by the per-model cap (3).

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

### Commitment observations (deterministic, non-cognitive)

When a Session carries explicit `CommitmentTransition` artifacts, `compute_commitment_observations` (and, for N≥3, the nested `SessionMetrics.commitment_observations` field) records **observations only** — not claims of cognition, conviction, or intent. Changed claim prose never contributes; only structured transitions count. The function is pure and replay-safe.

Closed `CommitmentState` vocabulary (from `src/golden_lattice/memory_graph/base.py`):

- `proposed`
- `defended`
- `challenged`
- `revised`
- `withdrawn`
- `reaffirmed`
- `unresolved`

A state change is recorded solely via an explicit `CommitmentTransition` whose `prior_state` / `next_state` are members of that set — never inferred from changed claim prose.

Operational formulas:

| Field | Formula |
| --- | --- |
| `transition_count` | \|`commitment_transitions`\| |
| `reaffirmation_count` | \|{ t \| t.next_state = `reaffirmed` }\| |
| `unresolved_rate` | among claims with ≥1 transition, fraction whose **latest** `next_state` is `unresolved`; `0.0` when no such claims |
| `reversal` | a later transition whose `next_state` was already occupied earlier on that claim's path (first `prior_state` and every subsequent `next_state`) |
| `reversal_count` | number of reversal transitions across claims |
| `persistence_after_reversal` | among reversal transitions, fraction for which the restored `next_state` remains the claim's **final** observed state; denominator = `reversal_count`; **`None`** when `reversal_count = 0` (undefined, not silently zero) |
| trajectories | per-claim state path `(first prior_state, …each next_state)` ordered by first `sequence_index`; also grouped by `source_model` |

These measurements support longitudinal persistence/reversal studies. They do not adjudicate synthesis and introduce no judge model.

## 11. The pattern this deliberately rejects

The natural workflow with Opus / Sonnet / Haiku is `Haiku → Sonnet → Opus`: prototype with Haiku, refine with Sonnet, escalate to Opus. The cost and speed curves pull hard toward this pipeline.

Golden Lattice rejects this pattern. We believe parallel symmetric authorship produces something the pipeline cannot — specifically, the preservation of insights that would be lost when one model's output becomes the input to another's refinement.

The pipeline is efficient. The Lattice is generative. They are different tools for different problems.
