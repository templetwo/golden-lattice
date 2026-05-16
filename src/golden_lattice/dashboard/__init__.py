"""Web dashboard for Golden Lattice — a second renderer over the same
LatticeEvent protocol the terminal TUI consumes.

The terminal TUI is for fast feedback (did the lattice complete, did
parity hold, what shape is the dialogue taking). The dashboard is for
careful reading: scrollable panels show full claim text, full Phase 3
turns, full synthesis output, full feed entries — no truncation.

Same event protocol, two renderers. Live runs and replay both flow
through identical event streams.
"""
