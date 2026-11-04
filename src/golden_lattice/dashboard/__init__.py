"""Web dashboard for Golden Lattice — a second renderer over the same
LatticeEvent protocol the terminal TUI consumes.

The terminal TUI is for fast feedback (did the lattice complete, did
parity hold, what shape is the dialogue taking). The dashboard is for
careful reading: scrollable panels show full claim text, full Phase 3
turns, full synthesis output, full feed entries — no truncation.

Same event protocol, two renderers. Live runs and replay both flow
through identical event streams.

Pages: `/` is the Record (every run plus cross-session sums, built by
record.py); `/session/<id>` replays one run around where the seats
disagreed and why; `/session/live` is that page fed by /ws. Both read the
single shape normalize.py builds from a persisted Session.
"""
