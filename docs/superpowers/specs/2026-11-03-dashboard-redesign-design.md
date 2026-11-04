# Dashboard redesign: the Record and the Session

Date: 2026-11-03
Status: approved by Anthony, 2026-11-03
Author: MacBook seat (claude-fable-5-1), in conversation with Anthony Vasquez Sr.

## 1. Intent

The web dashboard exists so Anthony can replay an old lattice run and find
where the seats disagreed and why. That is the use. Everything else the page
does is in service of it.

Today the dashboard cannot do that job:

- None of the 12 persisted sessions load. They carry the May 2026 seat ids
  (`claude-opus-4-7`, `claude-sonnet-4-6`, `claude-haiku-4-5`); the
  `ModelId` enum moved to the Claude 5 family on 2026-08-15 (commit 336e7da)
  and the sessions no longer validate. `/sessions` swallows the errors and
  returns `[]`, so the page opens to "no completed sessions to replay yet."
- The disagreement reasons are collected but never rendered. Every session
  holds between 6 and 21 Phase 2 disagreements, each a full-paragraph reason,
  plus 5 to 13 critique turns. The page shows a "contested ×N" count and
  nothing else; critiques are buried in a flat list of 28 dialogue cards.
- The Phase 4 trace ledger and parity panel sit at 15% opacity forever (the
  container never received a reveal beat). The parity verdict reads the wrong
  field and the footer hardcodes "all dimensions clear". The five-reading
  honesty label from `interpret_parity_flags` is emitted by both the
  orchestrator and replay and never rendered.
- Phase 0 and Phase 2 use hardcoded three-column grids; the fourth seat has
  nowhere to go.
- Live mode rebuilds the entire DOM on every event, including each streamed
  token delta.
- There is no cross-session surface. The README's third falsification
  criterion ("parity stays above 15% per model across sessions") and the
  architecture's collapse detector ("persistent flagging across sessions means
  the protocol is collapsing toward routing") have nowhere to appear.

The redesign is two pages built around disagreement: the **Record** (the
library of runs with a cross-session strip) and the **Session** (a windowed
replay of one run whose center is a table of every claim and what the readers
did with it).

## 2. Goals, non-goals, anti-goals

Goals, in priority order:

1. Every persisted session loads and replays, including the 12 legacy ones.
2. The Session page answers "where did they disagree, and why" without
   summarizing anything: the Phase 2 reasons and Phase 3 critiques appear in
   full, attached to the claim they target, with the claim's fate in synthesis.
3. Each section of the Session page is its own window: a rail selects one,
   a second can be pinned beside it, each scrolls independently.
4. The Record lists runs newest first with the contested-ness visible before
   opening, and a strip above the list reads across runs: distinct-claim share
   per seat against the 0.15 line, who pushes back on whom summed over every
   cross-reading, and the flags with their readings.
5. The six defects above are fixed: ledger lit, parity verdict from the right
   field with the five-reading label, per-seat grids, no full-DOM rebuild in
   live mode, no silent swallow of unloadable sessions.
6. Live mode keeps working through the same page, with no new live features.

Non-goals for this pass:

- The live-first loom (channel-colored edges speaker to target, cap tallies,
  lived-time animation). It was option 3 in the brainstorm and waits.
- A light theme. The dashboard is dark only.
- Changes to the TUI, the event protocol, the orchestrator, or synthesis.
  `scripts/run_lattice_live.py` is in scope only for the three live-mode
  fixes in §6.4.
- A JavaScript build step, framework, or package manager. The frontend stays
  vanilla and served by aiohttp as static files.
- Editing sessions or writing anything to `sessions/` from the dashboard.

Anti-goals (what would make this a failure even if it works):

- Summarizing, truncating, or paraphrasing a disagreement reason, critique,
  omission reason, or modified text anywhere on the Session page. The reasons
  are the record.
- Rendering a parity flag as a bare red "FLAGGED" without its reading. The
  label is the project's honesty made visible; a scary flag with no reading is
  what the M1 contract was written to prevent.
- Hiding an unloadable session. The lattice's rule is no silent failures; the
  Record must say how many files it could not read and why.
- Any element that quietly assumes three seats.
- Clutter. Anthony's first reaction to the merged mockup was "a little
  cluttered"; the streamlined version he approved has one mark per row, no
  badge strips, serif for reading and mono only for labels.

## 3. Constraints

- The four invariants in `ARCHITECTURE.md` bind the renderer: it computes no
  metrics and makes no judgments. Everything it shows is read from the
  persisted `Session` (via `normalize.py`) or from the canonical functions in
  `memory_graph/metrics.py` (`compute_parity_shares`,
  `interpret_parity_flags`). The pair table and the per-run chart are sums and
  reads over persisted fields, not new analysis.
- The event protocol in `events.py` is unchanged. Replay and live feed one
  render layer; the live `phase_2_cross_reading` event carries counts only,
  which is a protocol limit the page must handle (see §6.4).
- Claim ids are content-addressed with the seat id string inside the hash, so
  legacy sessions can only load unchanged if the enum accepts the legacy
  strings. See §5.1.
- Another seat is working on `eval-harness-steps-1-3` and has an unpushed
  local commit that swaps the Haiku id inside `index.html`'s color and label
  maps. It has agreed to leave the dashboard alone and to rebase after this
  redesign merges. The redesign must make that swap unnecessary by deriving
  seat labels and colors from the family name in the id (§5.2).
- Tests: `pytest` must stay green (441 passed, 11 skipped on a fresh clone at
  the base commit 4a5962c). New behavior gets tests; see §9.
- Fonts stay as they are: Spectral and JetBrains Mono via Google Fonts as
  progressive enhancement, system serif and mono as fallback, never a hard
  dependency.
- The seat palette must pass the dataviz validator for dark mode (§7).

## 4. Decisions Anthony made

These are his, in the order he made them:

1. The dashboard is for replaying old runs.
2. What he looks for is where the seats disagreed, and why.
3. The session page is the merge of "contest ledger inside the phase spine"
   and "claim fate table": the chronological 00 to 04 structure stays, Phase 2
   becomes a table of every claim with its fate, filtered to disputed by
   default, and a claim expands in place to show the reasons in full.
4. Streamlined, not decorated: he found the first merge cluttered and approved
   the version with one mark per row and no badge strips.
5. Windowed: each section navigable separately, and of the two cuts he chose
   **rail and pin**: a rail on the left selects one section that fills the
   window; a second section can be pinned beside it.
6. The front page is **the Record**: the library list with the cross-session
   strip above it.
7. The chart in the strip must stay inside its own box and never run into
   the neighboring text.
8. The Phase 2 "missing" claims stay off the Session page in this pass
   (§5.8).

## 5. Decisions I made (my calls, open to override)

### 5.1 Legacy sessions load through legacy enum members

Add to `ModelId` in `memory_graph/base.py`:

    LEGACY_OPUS_4_7 = "claude-opus-4-7"
    LEGACY_SONNET_4_6 = "claude-sonnet-4-6"
    LEGACY_HAIKU_4_5 = "claude-haiku-4-5"

They are never in `DEFAULT_INVITED_MODELS` (an explicit tuple in the
orchestrator; verify it stays explicit) and never offered as live seats. Every
place that maps a `ModelId` to something (attribution tags in
`synthesis/attribution.py`, `tui/colors.py`, the `_DIMENSION_BY_LABEL` and
metrics code) must handle them, which §5.2 makes mechanical.

Rejected alternative: rewriting legacy ids at load time. Claim ids embed the
old string in their hash, so any rewrite invalidates every claim. The scratch
tool I used to view the sessions had to recompute 30 to 67 claim ids per file
and rewrite every reference structurally; that is a migration, not a loader,
and the chronicle's rule is that nothing is edited in place.

The `improve/vision-aligned-lattice` branch (unmerged, at 7a83305) already
sketches a `LEGACY_HAIKU_4_5` member. This design generalizes that idea; it
does not depend on that branch.

### 5.2 Seat identity derives from the family name

Add `seat_family(model_id) -> Literal["fable","opus","sonnet","haiku"]` in
`memory_graph/base.py`, derived from the id string (the family name is a
substring of every current and legacy id). Attribution letters, TUI colors,
and the dashboard's labels and colors key on the family, not the literal id.
Then a future id move touches `ModelId` only, and the other seat's Haiku swap
has nothing left to carry across.

A **seat is a family** everywhere the Record aggregates: the share chart has
one line per family, the pair table one row per family pair, and the
persistent-flag rule compares families. This follows the comment on
`ModelId` ("protocol seat identities; use an explicit provider mapping when a
deployment routes a seat elsewhere"). The exact id is provenance, never
hidden: the Session masthead prints it in mono beside the family label
(a legacy replay reads "OPUS · claude-opus-4-7"), the chart tooltip for a run
names the exact ids, and the chart draws a thin vertical rule at any run
where a family's underlying id changed from the previous run, so a shift in
the line at a model change is readable as a model change and not as drift.

### 5.3 Frontend structure

Split `static/index.html` into `index.html`, `app.js`, and `app.css`, served
by aiohttp's static route. No bundler. Modern-browser JavaScript (modules,
`const`/`let`, template literals) is fine; the page runs on Anthony's own
machine. The explainer page is untouched and linked from the Record masthead.

Routes: `/` is the Record; `/session/{id}` is the Session page (the server
serves `index.html` for both and the page reads the path). Links to a session,
a section, and a pinned section are shareable: `/session/{id}#02+04`.

### 5.4 The `/sessions` endpoint grows and gets a sibling

- `GET /sessions` returns `{"sessions": [...], "unloadable": [{"id", "error"}]}`.
  `sessions` is the normalized shape (§6.5), newest first. `unloadable` names
  every file the store listed that failed to load, with the first line of the
  validation error. No test depends on the old bare-array shape.
- `GET /sessions/{id}` returns one normalized session. Live mode uses it to
  hydrate the details the event stream does not carry once a run completes.

### 5.5 Replay transport stays beat-based

The scrubber steps through events in time order as it does today (play,
pause, restart, speed). Lived-time pacing through `timestamp_offset_ms` stays
available through `python -m golden_lattice.dashboard replay`, which drives the
same page over the WebSocket. No new transport.

### 5.6 Critiques move onto their claims, and a critique is a dispute

Phase 3 critique turns render under the claim they targeted (each has
`target_claim_ids`). The Dialogue section shows augment and converge turns
only, with one line at its top saying how many critiques are on their claims.
A critique that targets several claims appears under each.

A claim is **disputed** when any reader disagreed with it in Phase 2 or
critiqued it in Phase 3. This follows the synthesis engine's own rule:
commit cddf0a0 (Rule 1 v2) already consumes Phase 3 critique as dispute
signal alongside Phase 2 disagreement. The `×N` mark counts distinct readers
who disputed through either channel. On the real sessions, every run has 1 to
7 claims that were critiqued without a Phase 2 disagreement; they belong in
the default filter.

Inside an expanded row, each disputing reader gets one block. When the reader
did both, the Phase 2 reason comes first and the critique follows as "In
dialogue:". When the reader only critiqued, the block opens with "In
dialogue:". When the reader endorsed the claim in Phase 2 and then critiqued
it in Phase 3 (1 to 8 such critiques per run), the block says so in its first
line ("endorsed in cross-reading, then in dialogue:") and the reader is not
repeated on the endorsed line.

### 5.8 Phase 2 "missing" claims

Each `CrossReading` carries `missing`: claims the reader wrote to say the
target left something out. Every run has 16 to 29 of them, about as many as
Phase 1 claims. They are not in `claim_trace` (synthesis never traces them)
but `compute_parity_shares` counts them through `Session.all_claims()`, so the
Record's share chart includes claims a Phase-1-only Session page would never
show. `normalize.py` drops them today.

**Anthony's decision (2026-11-03): leave them out of this pass.** The
Session page shows Phase 1 claims only. So that the omission is never read
as a bug, the 02 header carries one quiet line, "N claims readers said were
missing are not shown", and the Record's chart caption notes that shares
count every claim including those. `normalize.py` keeps dropping them from
the per-claim shape; `stats.missing` carries the count so the header can
print it. Showing them is a candidate for a later pass and is recorded in
§10.

### 5.7 Surfaced disagreements and omissions are not optional

`SynthesisArtifact.surfaced_disagreements` (never normalized today) joins the
shape. A claim named in one shows the note in its fate line. Omitted claims
list under the synthesis output with their `omission_reason`; modified claims
show `modified_text` beside the original on expansion.

## 6. The pages

### 6.1 The Record (`/`)

Masthead: "Golden Lattice · the record · N runs", a search box that filters
rows by prompt text, a link to the explainer, and "go live".

The strip, three cells in a grid with gutters, each cell `min-width: 0`:

- **Distinct-claim share by seat, per run.** One line per seat across runs in
  chronological order, x is run order (labelled with first and last dates),
  y is share from 0 to 0.5 with a dashed line at the parity threshold read
  from the data (0.15). Seat lines end in a marker and a direct label inside
  the SVG. The SVG scales to its cell (`viewBox`, `width: 100%`), with every
  axis label and the legend inside the `viewBox`. A caption states the lowest
  seat and whether anything crossed the line, computed from the same rows.
  Hover on a run column shows the three shares in a tooltip.
- **Who pushes back on whom.** A table of reader → target family pairs with
  disputed and endorsed counts summed across every loaded run's `phase_2`
  (disagreements and agreements; Phase 3 critiques are not in this table,
  which is a cross-reading table and says so in its caption). Sorted by
  disputed descending. A pair is marked in the dispute color when it endorsed
  at least eight times as often as it disputed (on today's data that marks
  both Haiku rows, 13/109 and 11/110, and nothing else). With Fable seated
  this is twelve rows; the table does not assume three seats.
- **Flags.** The count of parity flags across runs as a hero number, then each
  flag: run date, seat, dimension, reading in words, and the reading's gloss
  (the strings in `tui/colors.py` `READING_GLOSS`, moved somewhere both
  renderers can import). Flags from recomputed metrics count and carry the
  same quiet "recomputed" marker the list uses; `interpret_parity_flags` is
  the canonical function either way, and leaving them out would be hiding.
  On today's twelve runs that makes two flags, not the one the mockup showed
  from persisted metrics alone: 05-16 20:15 Sonnet edge-case not_recognized
  and 05-04 07:18 Opus edge-case peer_divergence (recomputed). A closing line
  states plainly whether persistent flagging is present, by a rule the builder
  states in the code and in the cell's caption (my suggestion: the same
  family flagged in three or more of the last five runs).

The list: one row per loaded run, newest first, columns when, prompt, claims,
disputed, both, elevated, parity. The counts are **claims, not events**, and
the same counts drive the 02 filters: `disputed` is Phase 1 claims disputed by
at least one reader through either channel (§5.6); `both` is claims disputed
by two or more distinct readers; `elevated` is claims named in any elevation.
(The approved mockup showed event totals in two of those columns; the numbers
change, the layout does not.) Parity reads holds, or "flag · <reading>" in
the flag color; runs whose metrics were recomputed because they predate
persistence get a quiet marker beside the verdict. A run in flight sits at
the top in the live color with its current phase and becomes a row when it
completes. Unloadable files appear as one line under the list ("3 files could
not be loaded") that expands to the ids and errors.

Clicking a row opens `/session/{id}`.

### 6.2 The Session (`/session/{id}`)

Masthead: "Replay", the prompt (one line, ellipsis, full on hover or click),
the seats with their claim counts, and the session id.

Rail on the left with five entries: `00 · feed`, `01 · seats`,
`02 · disputes`, `03 · dialogue`, `04 · synthesis`. One entry is active and
fills the window; a second may be pinned (`⌖`) and splits the window beside
it. At most two sections are visible. Keyboard: digits select, `p` pins the
hovered or active section, `Esc` unpins. Each section keeps its own scroll
position across selection changes. The default on open is 02 active with 04
pinned, which is the reading Anthony does. During replay a section the
transport has not reached yet is dimmed in the rail.

Sections:

- **00 · feed.** Grounding line, proposals per seat (grid is per seat), the
  union and freeze line, results and failed searches. "skipped" state when
  Phase 0 did not run. Collapsed to one line at the top of 01 when 00 is not
  selected is not required; it is its own section.
- **01 · seats.** One column per invited seat: family label, focus tag,
  confidence, claim count, and the self-reflection justification in full with
  the strongest and weakest claims named. No claim list here; the claims are
  the table.
- **02 · disputes.** Filters on one row: `disputed · N` (default), `all · N`,
  `elevated · N`, `omitted · N`. Sort: most disputed first, then author order,
  then claim order. One row per Phase 1 claim: author family label, the claim
  text, and one mark on the right: `×N` in the dispute color for N
  disagreeing readers, `✦` in gold when elevated, `—` when omitted. Clicking a
  row expands it in place; several can be open. The expansion shows, for each
  reader that disagreed: the reader's label, the Phase 2 reason in full, and,
  when that reader also critiqued the claim in Phase 3, "In dialogue:" with
  the critique content in full. Readers that endorsed the claim appear on one
  line. The fate line: disposition, the synthesis line number it became (a
  link that scrolls 04 to that line, pinning 04 if a pane is free), the
  `[DISPUTED]` hedge text when the output carries one, the surfaced
  disagreement note when one names this claim, `modified_text` when modified,
  `omission_reason` when omitted.
- **03 · dialogue.** Augment and converge turns in order, each with speaker,
  channel, target, content in full, and `✦` when the turn is cited by an
  elevation. One line at the top: "N critiques are on their claims in 02".
- **04 · synthesis.** One line of ledger (present, modified, omitted counts
  and traced percentage, lit at full opacity), one line of parity (holds, or
  the flag with its reading and gloss; the lowest share and the threshold),
  then the co-authored output with each line's author tag. Lines whose claim
  was disputed carry a `×N` mark that opens the row in 02. Omitted claims list
  under the output with their reasons. When metrics are `None` (dyad), the
  parity line says parity is undefined at two seats.

Transport at the bottom in replay: restart, play/pause, phase label, scrubber,
step counter, speed. In live: the live dot, status, the prompt input and send,
exit live.

### 6.3 Replay behavior

The page builds the full session shape once, then reveals by beat. The beat
order is the event order from `replay.py`: 00 entries, Phase 1 claims and
reflections, Phase 2 cross-readings, Phase 3 turns, the artifact, metrics,
interpretations. Rows in 02 appear as their claims arrive; dispute marks and
expansions become available as the cross-reading that produced them arrives;
synthesis lines reveal with the artifact.

### 6.4 Live behavior

Live mode reuses the Session page. The state model is updated per event and
each section re-renders only itself; stream deltas append to a text node.

The live Phase 2 event carries counts per reader → target pair and nothing
per claim, so during a live run 02 cannot mark rows. Instead, 02 shows the
rows as Phase 1 claims arrive, and above them one compact line per pair as
each cross-reading lands ("OPUS read SONNET · 8 endorsed · 5 disputed · 5
missing"). Phase 3 critiques carry `target_claim_ids`, so critique marks and
blocks do attach live. On `session_completed` the page fetches
`GET /sessions/{id}` and replaces the pair lines with per-claim marks,
reasons, agreements, surfaced disagreements, and interpretations from the
persisted file.

Hydration has a race today: the orchestrator emits `session_completed`
before `scripts/run_lattice_live.py` saves the file, the script constructs
`DashboardServer()` without its `--sessions-dir`, and `/sessions` is cached
once forever, so a finished run would never reach the Record. The script is
in scope for exactly these fixes: pass `sessions_dir` to the server, and
`save()` before the completion event's broadcast is awaited (the pending
broadcast tasks are already gathered after the run; order the persist before
that gather). The server's cache is keyed by the set of files and their
mtimes, so a new file invalidates it. The page retries `GET /sessions/{id}`
a bounded number of times (my suggestion: five tries over five seconds) and,
if the file never appears, says so in 02 rather than pretending.

The Record shows the in-flight row while a run is live. The prompt input and
quit signal keep their current WebSocket messages.

### 6.5 The normalized shape

`normalize.py` grows. Added to the per-session object:

    claims: [{id, author, text, index, agreed_by: [reader],
              disagreements: [{reader, reason}],
              critiques: [{speaker, turn_id, content}],
              disputed_by: [reader],            # union of the two, distinct
              disposition, modified_text, omission_reason,
              synthesis_line, disputed_hedge, surfaced_note,
              elevated: bool}]
    stats: {claims, disputed, both, elevated, omitted, modified,
            critiques, augments, converges, missing}   # missing is a count only
    pairs: [{reader, target, disputed, endorsed}]
    interpretations: [{model, dimension, reading, share, histogram...}]
    metrics_recomputed: bool
    seats: [{id, family, label}]

`synthesis_line` is found by matching each output line, after stripping its
`[X]` tag and any `[DISPUTED ...]` suffix, against claim text and
`modified_text`; unmatched lines are allowed and carry no link. The existing
`phase1`, `reflections`, `crossreadings`, `turns`, `synthesis`, `metrics`,
`elevations` keys stay for the beat logic.

The Record's strip is computed in Python in a new `record.py` beside
`normalize.py`: per-run shares, the summed pair table, and the flag list, so
the renderer stays a renderer.

## 7. Visual system

Dark only, the current surface `#0c0d11` and panel `#0f1117`/`#14161d`.
Spectral for anything read, JetBrains Mono for labels, ids, numbers. One mark
per row. Gutters, not borders, between the strip's cells.

Type sizes (Anthony, 2026-11-04: "the text sizes in the prompt area are way
too large"): the prompt is never display type. In the Session masthead it is
one line at body size, 15px, with the full text on click; in the Record's
list it is 13.5px. Body text is 13.5 to 15px, labels 9.5 to 11px mono. The
only number set larger than body size anywhere is the Record's flag count at
22px. Nothing on either page is set above 22px.

Seat colors: the four current hues fail the dataviz validator on the dark
surface (lightness band fail on all four; Sonnet and Opus at ΔE 7.5 deutan and
2.8 tritan). Re-step them until the validator passes all five checks with
`--mode dark --surface "#0c0d11"`, keeping the family hues recognizably where
they are (Fable warm, Opus violet, Sonnet blue, Haiku green) and the fixed
order Fable, Opus, Sonnet, Haiku. The validator is not in the repository; it
ships with the bundled dataviz skill at
`/private/tmp/claude-501/bundled-skills/2.1.309/f7075526e8600bc026b43a0ab00c28df/dataviz/scripts/validate_palette.js`
on this machine. Vendor a copy into `scripts/validate_palette.js` so the check
is reproducible from the repo. Seat names are always
printed beside any colored mark; color never carries identity alone. Channel
colors (critique, augment, converge) and disposition colors keep their
semantic hues and are not reused for seats. Text wears text tokens, never a
seat color, except the seat's own label.

Chart marks: 2px lines, markers at least 8px, a dashed threshold line, grid
and axes recessive, labels inside the `viewBox`, a tooltip on hover, a
`role="img"` with an `aria-label` that states the finding.

## 8. Coordination

- Branch: `claude/dashboard-refresh-redesign-b88d5a` off main at 4a5962c.
  Main has not moved. The remote `worktree-lattice-dashboard-redesign` branch
  is the merged August work and is not involved.
- The eval seat rebases after this merges. Its Haiku swap becomes moot via
  §5.2.
- A live pilot run from the eval seat may land a new session file with current
  ids in `sessions/`; it is the first native-id replay and a good manual
  check.
- `.superpowers/` (the brainstorm mockups) and `.claude/launch.json` (points at
  scratch paths) are not committed. Adding `.superpowers/` to `.gitignore` is
  Anthony's call.

## 9. Verification

- `pytest` green. New tests:
  - `tests/memory_graph`: legacy ids load; `seat_family` for every member;
    `DEFAULT_INVITED_MODELS` contains no legacy member.
  - `tests/dashboard/test_normalize.py`: the claims shape from a built
    session fixture (disagreements, critiques attached, dispositions, surfaced
    note, synthesis line matching, elevated flag); `stats`; `pairs`;
    `interpretations`; `metrics_recomputed`.
  - `tests/dashboard/test_record.py`: per-run shares, summed pairs, flag list,
    the persistent-flag rule.
  - `tests/dashboard/test_server.py`: `/sessions` object shape with
    `unloadable` populated from a deliberately broken file; `/sessions/{id}`;
    `/session/{id}` serves the page; static files served.
  - A legacy fixture: one small session JSON with the May ids checked into
    `tests/fixtures/`, built from a current session by the scratch remap tool
    in reverse or by hand, small enough to read.
- Browser check against the 12 real sessions (via `--sessions-dir` pointing at
  the main checkout's `sessions/`): every session opens, 02 defaults to
  disputed with reasons in full, pin and rail work, the Record strip renders
  with no cell overflow at 760px and 1280px, the chart stays inside its box.
- Palette validator passes for the final seat hexes; paste the report into
  the PR description.

## 10. Left to the builder

- Exact CSS, spacing, and type sizes, within §7.
- The rule for "persistent flagging" in the Record's flags cell (state it in
  code and in the cell's caption).
- How the synthesis-line match handles a claim whose text appears twice.
- Whether 00 shows a one-line summary in the Session masthead.
- The module layout inside `app.js` (state, beats, sections, live).
- Tooltip implementation for the chart and for truncated prompts.

Deferred to a later pass, by decision: showing the Phase 2 "missing" claims
(§5.8); the live-first loom (§2); a light theme (§2).

## 11. Appendix: what the mockups were

Kept under `.superpowers/brainstorm/84043-1793730434/content/` in the worktree,
uncommitted: `session-shape.html` (three centers), `session-shape-v2.html` (A
and B merged), `session-shape-v3.html` (streamlined, approved),
`windowed-v3.html` (tiled vs rail and pin; rail and pin chosen),
`library-v3.html` (the Record, chart fixed). All used real rows from
`session_20260516_204119_ed014de7` and the twelve-run record.
