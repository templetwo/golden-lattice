"""Live terminal renderer for Lattice sessions — built against the replay
event stream so the same renderer works for live and for persisted Sessions.

The architecture's epistemics are the interface. The non-flattening made
visible. Three columns for Phase 1 and 2, a loom for Phase 3, a trace ledger
and parity panel for Phase 4. The constraints are on screen, filling and
stopping. The flag readings are explicit, including the ones that say
"I do not know yet" as a designed state.
"""
