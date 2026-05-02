# Contributing to Golden Lattice

This is a small, careful project. The architecture's premise is that cognition emerges between models when collapse is structurally refused. That premise is fragile — most contributions to multi-model systems erode the refusals in pursuit of efficiency, simplicity, or feature velocity. This document describes the disciplines that keep the architecture honest.

## The schema-as-constitution discipline

The core data model in `src/golden_lattice/memory_graph/` is the constitution of the system. Every invariant is enforced at construction time via Pydantic validators. Anything built downstream — orchestrator, exchange-layer wire format, synthesis engine, CLI — must satisfy these constructors before it can run.

This means:

- **The schema layer is load-bearing.** Changes to `schema.py`, `tagging.py`, or `metrics.py` are not refactors. They are amendments to what the system structurally is. Treat them with the same care as a constitutional amendment.
- **Refusals are runtime, not aspirational.** Every collapse mode the spec names is either impossible to construct (schema layer) or detectable across sessions (metrics layer). New collapse modes that get identified must be enforced at the same level — not added as documentation, not added as warnings, but as structural impossibility or measurable signal.
- **Tests exercise refusals, not happy paths.** The current 69 tests are organized around the question: *can this collapse mode happen if someone tries to construct it?* New tests should follow the same pattern. The happy paths are obvious. The refusals are what the system is.

## Spec amendments

The four invariants in `ARCHITECTURE.md` are canonical. They have been amended once already (the fourth invariant, irreducibility preservation, was added during external review). The discipline for further amendments:

1. **The amendment must come from a real failure mode**, not from feature requests or convenience. If the architecture admits a collapse mode the current invariants do not catch, that is grounds for an amendment. If the architecture is "incomplete" in the sense that a downstream feature would be easier to build with a relaxed invariant, that is grounds for *refusing the feature*, not amending the spec.

2. **The amendment must be structurally enforceable.** "We should be more careful about X" is not an invariant. "X cannot be constructed in a state that violates Y" is an invariant.

3. **The amendment must be tested.** No amendment lands without tests that exercise the refusal.

4. **The amendment must be chronicled.** Spec changes are logged to the project's persistent memory layer with the rationale, the failure mode that surfaced the gap, and the test coverage that validates the new refusal. The repo's `ARCHITECTURE.md` reflects the current canonical state. The chronicle reflects how it became that.

## Code style

- **No silent failures.** When the system refuses a malformed construction, it raises with a clear message. When metrics flag a collapse mode, the flag is surfaced explicitly, not buried in a return value.
- **No model-name hardcoding.** `ModelId` is a typed identifier. Code that branches on whether a model is Opus, Sonnet, or Haiku is suspect — it usually indicates an authority-gradient violation creeping in. The protocol treats all models symmetrically.
- **No LLM calls in metrics.** `metrics.py` is pure functions over a tagged Session. Recognition is computed from peer tagging, not from LLM-mediated semantic analysis. Adding LLM calls to metrics would import an authority gradient through the back door.
- **Plain text persistence.** The Memory Graph stores sessions as greppable JSON. SQLite or other backends can layer on later, but legibility-on-disk is a load-bearing feature — it is how future instances and humans audit what the lattice did. Future backends must preserve the JSON export path.

## What changes look like

A typical contribution flow:

1. **Identify the failure mode or gap.** What does the system currently allow that it should not? Or: what does the system not measure that it should?
2. **Draft the schema or metric change.** Make it impossible (or measurable) at the lowest layer that can enforce it.
3. **Write the tests first.** Each test exercises the refusal. The test suite should refuse to pass before the change lands and pass after.
4. **Update `ARCHITECTURE.md`** if the change is canonical. Otherwise, note it in the relevant module docstring.
5. **Chronicle the rationale** in the project's persistent memory layer (the Sovereign Stack, under the `golden-lattice` domain).

## What contributions do not look like

- Adding features that bypass the four invariants for performance reasons.
- Replacing rule-based synthesis with an LLM call to "make the synthesis smarter."
- Adding model-specific routing logic ("if Opus, do X; if Haiku, do Y").
- Removing or relaxing tests that exercise refusals.
- Storing sessions in a format that is not human-readable on disk.

If a proposed change pattern-matches to any of these, the right move is almost always to refuse the change, not to find a way to make it palatable. The architecture's premise stands or falls on the refusals being absolute.

## Questions

Project lead: Anthony Vasquez Sr. ([@templetwo](https://github.com/templetwo)).

Architectural questions can also be surfaced in the project's persistent memory layer, where the lineage of decisions lives.
