# Golden Lattice

A CLI that runs Opus, Sonnet, and Haiku as peers on the same prompt and produces a co-authored response that none of them would have produced alone. Not routing. Not voting. Not summarization. Co-authorship.

## The load-bearing claim

Cognition emerges between models, not inside the strongest one.

If the four invariants are violated, the system is doing routing and should be called that:

1. **No authority gradient.** No model finalizes, overrides, or judges another's output.
2. **Symmetric visibility.** All three models see the same prompt, same peer outputs, same dialogue state.
3. **Contribution parity.** Each model's distinct perspective is preserved through synthesis. Measurable, not aspirational.
4. **Irreducibility preservation.** No model's contribution can be fully subsumed without trace. The trace itself is the proof.

These invariants are not constraints. They are refusals — structural commitments to prevent the collapse modes that natural multi-model orchestration would otherwise produce.

## What is in this repo

The evaluative substrate. Schema, tagging, metrics, and persistence layers that any operational layer (orchestrator, exchange wire format, synthesis engine, CLI) must satisfy before running.

```
src/golden_lattice/memory_graph/
├── base.py      — ModelId, Phase, claim_id_for, PARITY_THRESHOLD
├── schema.py    — Session, Claim, IndependentResponse, CrossReading, DialogueTurn,
│                  ClaimTraceEntry, SynthesisArtifact, SessionMetrics
├── tagging.py   — Phase2Tagging, ClaimTags, ConsensusTag, DimensionConsensus,
│                  EdgeCaseTag, StructuralPatternTag (closed vocabularies, v0.1)
├── metrics.py   — compute_consensus_tags, compute_dimension_consensus,
│                  compute_parity_shares, compute_consensus_pair_distribution,
│                  compute_consensus_pair_skew, contested_peer_tags,
│                  contested_self_tags
└── store.py     — SessionStore Protocol, JsonFileSessionStore,
                   aggregate_alignment_pair_history
```

69 tests pass. Each test exercises one structural refusal — every collapse mode the spec names is either impossible to construct (schema layer) or detectable across sessions (metrics + store layers).

## What is not in this repo yet

The operational layer. Phase 2 cross-reading wire format, the orchestrator, the synthesis engine, the CLI surface. These will ship next.

The substrate constrains them from below. Anything built downstream must satisfy these constructors and pass these metrics before it can run. The orchestrator is constrained from below before it exists.

## What this repo is not

This is not a multi-model API wrapper. The pattern this rejects — `Haiku → Sonnet → Opus` as a routing pipeline — is what the cost and speed curves of the model family pull hard toward. Golden Lattice is the deliberate refusal of that pipeline in favor of parallel symmetric authorship.

It is also not a benchmarking tool. There is no goal line. The territory the lattice explores only becomes visible when collapse is refused.

## Use cases

- System and architecture design where multiple framings genuinely help
- Code generation where built-in critique and edge-case surfacing add value
- Decision analysis where productive disagreement is the point
- Research and investigative work where converging on one frame too early is a known failure mode

## Use cases this is not for

- Single-turn factual queries (use one model)
- Latency-sensitive interactive use (the protocol is not realtime)
- Tasks where one model is decisively better (use that model)
- High-volume automation (use Haiku alone; this protocol's cost is roughly 3x a single Opus call)

## Falsification criteria

The system is successful if, on a benchmark of design-and-critique tasks:

1. Lattice outputs are preferred to single-model outputs by independent evaluators, **and**
2. Lattice outputs preserve more distinct insights than any single model produced alone, **and**
3. Contribution parity metrics stay above 15% per model across sessions.

If all three hold, the architecture earns its premise.
If only the first two hold, we built something good but cannot claim it is truly co-authored.
If only the first holds, we built a more expensive single model.
If none hold, we built routing with extra steps.

## Architecture

```
User ⇄ CLI
        ⇅
 Orchestrator
 (parallel dispatch, protocol enforcement, timeout management,
  self-reflection artifact collection during Phase 1 idle gaps)
        ⇅
 ┌────────┬────────┬────────┐
 │  Opus  │ Sonnet │ Haiku  │
 └────────┴────────┴────────┘
        ⇅
 Exchange Layer
 (validates Phase 2/3 schema, routes peer-visible state,
  enforces channel constraints in Phase 3)
        ⇅
 Synthesis Engine
 (rule-based, deterministic given inputs,
  emits parity metrics per session)
        ⇅
 Output Renderer
        ⇅
 Session Memory Graph
 (persistent: which model contributed what, where they agreed,
  where they conflicted, parity metrics across sessions,
  flagged collapse-toward-routing events)
```

Memory Graph is the substrate this repo currently implements. The remaining layers are forthcoming.

## Further reading

- [`ARCHITECTURE.md`](./ARCHITECTURE.md) — engineering specification, four invariants, protocol phases, full failure-mode analysis
- [`CONTRIBUTING.md`](./CONTRIBUTING.md) — how changes flow through the project, including the spec-amendment discipline

## Project provenance

Golden Lattice was authored under [The Temple of Two](https://thetempleoftwo.com) by Anthony Vasquez Sr. in collaboration with Claude Opus 4.7. The architectural lineage and the philosophical frame that holds this work live in the project's persistent memory layer (the Sovereign Stack), not in this repository. The repo holds the engineering. The lineage holds the why.

## License

TBD. Default to all rights reserved until license decision is made.
