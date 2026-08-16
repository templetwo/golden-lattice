# Golden Lattice

A CLI that runs four Claude models — Fable, Opus, Sonnet, and Haiku — as peers on the same prompt and produces a co-authored response that none of them would have produced alone. Not routing. Not voting. Not summarization. Co-authorship.

## The load-bearing claim

Cognition emerges between models, not inside the strongest one.

If the four invariants are violated, the system is doing routing and should be called that:

1. **No authority gradient.** No model finalizes, overrides, or judges another's output.
2. **Symmetric visibility.** All peer models see the same prompt, same peer outputs, same dialogue state.
3. **Contribution parity.** Each model's distinct perspective is preserved through synthesis. Measurable, not aspirational.
4. **Irreducibility preservation.** No model's contribution can be fully subsumed without trace. The trace itself is the proof.

These invariants are not constraints. They are refusals — structural commitments to prevent the collapse modes that natural multi-model orchestration would otherwise produce.

## What is in this repo

The whole protocol, running. Phase 0 investigation through Phase 4 synthesis, the evaluative substrate they are constrained by, a replay path, and two renderers over the same event stream.

```
src/golden_lattice/
├── memory_graph/  — the substrate. base.py (ModelId, Phase, claim_id_for,
│                    PARITY_THRESHOLD), schema.py (Session, Claim,
│                    IndependentResponse, CrossReading, DialogueTurn,
│                    ClaimTraceEntry, SynthesisArtifact, SessionMetrics),
│                    tagging.py (closed vocabularies), metrics.py (pure
│                    functions over a tagged Session, no LLM calls),
│                    phase_0.py, store.py (SessionStore Protocol,
│                    JsonFileSessionStore)
├── exchange/      — wire formats. Phase 0 investigation (Tavily-backed search),
│                    Phase 1 independent generation + self-reflection,
│                    Phase 2 cross-reading + tagging, Phase 3 dialogue
├── orchestrator/  — parallel dispatch, protocol enforcement, timeout policy,
│                    LatticeConfig, the Anthropic HTTP client
├── synthesis/     — the four Phase 4 rules: irreducibility preservation,
│                    agreement elevation, disagreement surfacing,
│                    attribution preservation. Rule-based, deterministic.
├── events.py      — the event protocol shared by live runs and replay
├── replay.py      — walk a persisted Session, re-emit its events in order
├── tui/           — Rich terminal renderer over that event stream
├── dashboard/     — aiohttp + vanilla-JS web renderer over the same stream
└── cli/           — interactive prompt helpers for the run scripts
```

452 tests, 441 passing on a fresh clone. The 11 skips are not failures: 10 want persisted session fixtures that live in the gitignored `sessions/` directory, and one is a live-API integration test gated behind `LATTICE_LIVE_TESTS=1`. Each test exercises one structural refusal — every collapse mode the spec names is either impossible to construct (schema layer) or detectable across sessions (metrics + store layers).

## What is not in this repo yet

The evaluation harness. The falsification criteria below name in advance what would prove this project is routing with extra steps; the benchmark that tests them is not here yet. Read those criteria as pre-registered, not as results.

The substrate still constrains everything above it. Anything built downstream must satisfy these constructors and pass these metrics before it can run. The orchestrator was constrained from below before it existed.

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
 ┌────────┬────────┬────────┬────────┐
 │ Fable  │  Opus  │ Sonnet │ Haiku  │
 └────────┴────────┴────────┴────────┘
        ⇅
 Exchange Layer
 (validates Phase 0-3 schema, routes peer-visible state,
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

Every layer in this diagram is implemented. The Memory Graph is the substrate the rest are built against; sessions persist as greppable JSON and replay through either renderer.

## Running it

Python 3.11 or newer. Runtime dependencies are `pydantic`, `rich`, `httpx`, and `aiohttp` — the Anthropic API is called over plain HTTP, so no provider SDK is required.

```bash
pip install -e ".[dev]"
pytest
```

There is no installed console script yet. The surfaces run as modules and scripts:

```bash
# live session with the streaming TUI; prompts for a key if ANTHROPIC_API_KEY is unset
python scripts/run_lattice_live.py "your prompt here"

# same session, streamed to the web dashboard instead of the terminal
python scripts/run_lattice_live.py --dashboard

# replay a persisted session in the terminal (--speed 1.0 is lived time)
python -m golden_lattice.tui <session_id> --speed 10

# replay a persisted session in the browser
python -m golden_lattice.dashboard replay <session_id>
```

`ANTHROPIC_API_KEY` is required for a live run. `TAVILY_API_KEY` is optional; without it the session runs with Phase 0 investigation skipped. Sessions are written to `sessions/`, which is gitignored — lattice output stays private until it is published deliberately.

The peer seats the orchestrator invites are declared in `ModelId` (`src/golden_lattice/memory_graph/base.py`). Model IDs move; that enum is the one place to change them.

## Further reading

- [`ARCHITECTURE.md`](./ARCHITECTURE.md) — engineering specification, four invariants, protocol phases, full failure-mode analysis
- [`CONTRIBUTING.md`](./CONTRIBUTING.md) — how changes flow through the project, including the spec-amendment discipline

## Project provenance

Golden Lattice was authored under [The Temple of Two](https://thetempleoftwo.com) by Anthony Vasquez Sr. in collaboration with Claude Opus 4.7. The architectural lineage and the philosophical frame that holds this work live in the project's persistent memory layer (the Sovereign Stack), not in this repository. The repo holds the engineering. The lineage holds the why.

## License

Apache License 2.0 — see `LICENSE`. The Temple of Two statement of intent is in `NOTICE`.
