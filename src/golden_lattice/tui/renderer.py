"""Rich-based layout + event-paced renderer for the Lattice TUI.

Composes five panels — header, three model columns, loom (Phase 3), trace
ledger (Phase 4), parity panel (Phase 4) — and updates them as LatticeEvents
arrive. Replay paces by each event's timestamp_offset_ms so the asymmetric
Phase 1 latency renders as lived time.
"""

from __future__ import annotations

import time
from typing import Iterable, Optional

from rich.console import Console, Group, RenderableType
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from golden_lattice.events import LatticeEvent
from golden_lattice.memory_graph.base import ModelId
from golden_lattice.tui.colors import (
    CHANNEL_COLOR,
    DISPOSITION_COLOR,
    MODEL_COLOR,
    READING_COLOR,
    READING_GLOSS,
)
from golden_lattice.tui.state import TuiState, apply_event, converge_pairs_per_claim


# --- individual panels ----------------------------------------------------


def render_header(state: TuiState) -> Panel:
    if state.session_id is None:
        body = Text("waiting for session_started…", style="dim italic")
    else:
        prompt_preview = (state.prompt or "")[:90].replace("\n", " ")
        if state.prompt and len(state.prompt) > 90:
            prompt_preview += "…"
        t_seconds = state.current_offset_ms / 1000.0
        status = (
            "[bright_green]session_completed[/]"
            if state.session_complete
            else "[dim]in flight[/]"
        )
        body = Text.from_markup(
            f"[bold]{state.session_id}[/]    "
            f"t+{t_seconds:6.2f}s    {status}\n"
            f"[dim]{prompt_preview}[/]"
        )
    return Panel(body, title="Lattice session", border_style="bright_black")


def render_model_column(state: TuiState, model: ModelId) -> Panel:
    color = MODEL_COLOR.get(model, "white")
    parts: list[RenderableType] = []

    # Header line: started/completed status + focus/confidence.
    completed = state.phase_1_completed.get(model)
    if completed is None:
        if model in state.phase_1_started_ms:
            t_start = state.phase_1_started_ms[model] / 1000.0
            parts.append(Text.from_markup(
                f"[{color}]generating…[/]  [dim]started t+{t_start:.2f}s[/]"
            ))
        else:
            parts.append(Text("waiting…", style="dim italic"))
    else:
        elapsed = (
            completed.timestamp_offset_ms
            - state.phase_1_started_ms.get(model, 0)
        ) / 1000.0
        conf_bar = _bar(completed.confidence, width=10)
        parts.append(Text.from_markup(
            f"[{color}]focus:[/] {completed.focus_tag.value}  "
            f"[{color}]conf:[/] {conf_bar} {completed.confidence:.2f}  "
            f"[dim]({elapsed:.1f}s, {completed.claim_count} claims)[/]"
        ))

    # Claims.
    reflection = state.self_reflections.get(model)
    strong_id = reflection.strongest_claim_id if reflection else None
    weak_id = reflection.weakest_claim_id if reflection else None
    claims = state.phase_1_claims.get(model, [])
    if claims:
        parts.append(Text(""))  # spacer
        for c in claims:
            marker = "  "
            if c.claim_id == strong_id:
                marker = "[bright_yellow]★[/] "
            elif c.claim_id == weak_id:
                marker = "[dim]·[/] "
            preview = c.text[:80] + ("…" if len(c.text) > 80 else "")
            parts.append(Text.from_markup(
                f"{marker}[{color}]·[/] {preview}",
            ))

    # Self-reflection justification.
    if reflection is not None:
        parts.append(Text(""))
        just_preview = reflection.tag_justification[:120]
        if len(reflection.tag_justification) > 120:
            just_preview += "…"
        parts.append(Text.from_markup(
            f"[dim italic]reflection: {just_preview}[/]"
        ))

    # Phase 2 tagging marker.
    own_tagging = next(
        (t for t in state.taggings if t.tagger_model is model),
        None,
    )
    if own_tagging is not None:
        parts.append(Text(""))
        parts.append(Text.from_markup(
            f"[dim]tagged {own_tagging.peer_tags_count} peer + "
            f"{own_tagging.self_tags_count} self claims[/]"
        ))

    short_name = {ModelId.OPUS: "OPUS", ModelId.SONNET: "SONNET", ModelId.HAIKU: "HAIKU"}[model]
    return Panel(
        Group(*parts) if parts else Text("(empty)", style="dim"),
        title=f"[{color} bold]{short_name}[/]",
        border_style=color,
    )


def render_loom(state: TuiState) -> Panel:
    """Phase 3 weave — channel-colored directed lines between speakers and
    targets, in arrival order. Doubled-converge claims (Rule 2 elevation
    condition) marked with a star and elevated style on every contributing
    converge line."""
    if not state.turns:
        body: RenderableType = Text("Phase 3 not yet started…", style="dim italic")
        return Panel(body, title="loom (Phase 3)", border_style="bright_black")

    elevated_turn_ids: set[str] = set()
    elevations = converge_pairs_per_claim(state)
    for turns in elevations.values():
        for t in turns:
            elevated_turn_ids.add(t.turn_id)

    # Show the most recent turns that fit; signal earlier ones with a count.
    MAX_VISIBLE = 14
    visible_turns = state.turns[-MAX_VISIBLE:]
    earlier_count = len(state.turns) - len(visible_turns)
    lines: list[RenderableType] = []
    if earlier_count > 0:
        lines.append(Text(f"(+ {earlier_count} earlier turns)", style="dim italic"))

    for turn in visible_turns:
        channel_color = CHANNEL_COLOR.get(turn.channel, "white")
        speaker_color = MODEL_COLOR.get(turn.speaker_model, "white")
        speaker_short = _short_model(turn.speaker_model)
        target_text = ""
        if turn.target_model is not None:
            target_color = MODEL_COLOR.get(turn.target_model, "white")
            target_short = _short_model(turn.target_model)
            target_text = f"[{target_color}]{target_short}[/]"
        else:
            target_text = "[dim]all peers[/]"

        elev_marker = (
            "[bright_yellow]★[/] " if turn.turn_id in elevated_turn_ids else "  "
        )
        t_seconds = turn.timestamp_offset_ms / 1000.0
        content_preview = turn.content[:100].replace("\n", " ")
        if len(turn.content) > 100:
            content_preview += "…"

        arrow = f"[{channel_color}]──{turn.channel}──▶[/]"
        line = Text.from_markup(
            f"{elev_marker}[dim]{t_seconds:5.1f}s[/]  "
            f"[{speaker_color}]{speaker_short}[/] {arrow} {target_text}  "
            f"[white]{content_preview}[/]"
        )
        lines.append(line)

    # Cap meters as a single summary line at the bottom.
    cap_summary = _render_cap_summary(state)
    if cap_summary:
        lines.append(Text(""))
        lines.append(cap_summary)

    return Panel(Group(*lines), title="loom (Phase 3)", border_style="yellow")


def render_trace_ledger(state: TuiState) -> Panel:
    """Per-claim disposition table. Modified rows show original→modified,
    omitted rows show omission_reason. Coverage line at top."""
    if state.artifact is None:
        body: RenderableType = Text(
            "Phase 4 trace not yet composed…", style="dim italic"
        )
        return Panel(body, title="trace ledger (Phase 4)", border_style="bright_black")

    # Build author index for color and short-text lookup.
    claim_index = {}
    for model, claims in state.phase_1_claims.items():
        for c in claims:
            claim_index[c.claim_id] = (model, c.text)

    artifact = state.artifact
    n_total = len(artifact.claim_trace)
    n_present = sum(1 for e in artifact.claim_trace if e.disposition == "present")
    n_modified = sum(1 for e in artifact.claim_trace if e.disposition == "modified")
    n_omitted = sum(1 for e in artifact.claim_trace if e.disposition == "omitted")
    coverage = 1.0 if n_total == 0 else (n_present + n_modified + n_omitted) / n_total
    cov_bar = _bar(coverage, width=20)

    header = Text.from_markup(
        f"Traced [bright_green]{n_present}[/]/[yellow]{n_modified}[/]/[bright_red]{n_omitted}[/] "
        f"present/modified/omitted of {n_total} claims  {cov_bar} {coverage:.0%}"
    )

    table = Table(show_header=True, header_style="bold", expand=True, pad_edge=False)
    table.add_column("Author", no_wrap=True, width=8)
    table.add_column("Claim", overflow="ellipsis", ratio=4)
    table.add_column("Disp.", no_wrap=True, width=10)
    table.add_column("Detail", overflow="ellipsis", ratio=3)

    for entry in artifact.claim_trace:
        author, text = claim_index.get(entry.claim_id, (None, entry.claim_id))
        author_label = (
            f"[{MODEL_COLOR.get(author, 'white')}]{_short_model(author)}[/]"
            if author
            else "[dim]?[/]"
        )
        disp_color = DISPOSITION_COLOR.get(entry.disposition, "white")
        disp_cell = f"[{disp_color}]{entry.disposition}[/]"
        if entry.disposition == "modified":
            detail = (entry.modified_text or "")[:80]
        elif entry.disposition == "omitted":
            detail = f"[italic]{(entry.omission_reason or '')[:80]}[/]"
        else:
            detail = ""
        text_preview = text[:80].replace("\n", " ") if text else entry.claim_id
        table.add_row(author_label, text_preview, disp_cell, detail)

    return Panel(
        Group(header, Text(""), table),
        title="trace ledger (Phase 4)",
        border_style="bright_black",
    )


def render_parity_panel(state: TuiState) -> Panel:
    """Per-dimension shares + per-flag interpretation with reading + histogram."""
    if state.metrics_event is None:
        return Panel(
            Text("Parity not yet computed…", style="dim italic"),
            title="parity",
            border_style="bright_black",
        )

    metrics = state.metrics_event.metrics
    parts: list[RenderableType] = []

    if metrics is None:
        parts.append(Text.from_markup(
            "[dim]parity undefined (N<3 — recognition-from-within requires a "
            "third presence in the room)[/]"
        ))
    else:
        threshold = metrics.parity_threshold
        for dim_label, share_dict in (
            ("distinct_claim_share    ", metrics.distinct_claim_share),
            ("edge_case_coverage      ", metrics.edge_case_coverage_share),
            ("structural_pattern      ", metrics.structural_pattern_share),
        ):
            parts.append(Text(""))
            parts.append(Text.from_markup(f"[bold]{dim_label}[/]"))
            for m in state.invited_models:
                share = share_dict.get(m, 0.0)
                bar = _bar(share, width=20)
                model_c = MODEL_COLOR.get(m, "white")
                marker = "" if share >= threshold else "  [bright_red]<[/]"
                parts.append(Text.from_markup(
                    f"  [{model_c}]{_short_model(m):8s}[/]  {bar} {share:.3f}{marker}"
                ))

    # Flag interpretations.
    if state.flag_event is not None and state.flag_event.interpretations:
        parts.append(Text(""))
        parts.append(Text.from_markup("[bold]flagged readings[/]"))
        for interp in state.flag_event.interpretations:
            reading = interp.reading
            color = READING_COLOR.get(reading, "white")
            model_c = MODEL_COLOR.get(interp.source_model, "white")
            parts.append(Text(""))
            parts.append(Text.from_markup(
                f"  [{model_c}]{_short_model(interp.source_model)}[/]  "
                f"[dim]{interp.dimension_label}[/]  share={interp.share:.3f}  "
                f"reading: [{color}]{reading}[/]"
            ))
            parts.append(Text.from_markup(
                f"    [dim italic]{READING_GLOSS[reading]}[/]"
            ))
            if reading not in ("low_claim_volume",):
                parts.append(Text.from_markup(
                    f"    [dim]histogram[/]  "
                    f"n=0: [bright_red]{interp.histogram_n_zero}[/]  "
                    f"n=1: [yellow]{interp.histogram_n_one}[/]  "
                    f"n=2: [bright_green]{interp.histogram_n_two}[/]  "
                    f"[dim](OTHER-only entries: {interp.other_only_entries}, "
                    f"claims: {interp.total_claims})[/]"
                ))
    elif state.flag_event is not None:
        parts.append(Text(""))
        parts.append(Text.from_markup(
            "[bright_green]parity holds across all dimensions and models.[/]"
        ))

    return Panel(Group(*parts), title="parity", border_style="bright_black")


# --- helpers --------------------------------------------------------------


def _bar(value: float, width: int = 10) -> str:
    """ASCII bar for a value in [0,1]."""
    value = max(0.0, min(1.0, value))
    filled = int(round(value * width))
    return "[" + "▮" * filled + "▯" * (width - filled) + "]"


def _short_model(model: Optional[ModelId]) -> str:
    if model is None:
        return "?"
    return {ModelId.OPUS: "OPUS", ModelId.SONNET: "SONNET", ModelId.HAIKU: "HAIKU"}[model]


def _render_cap_summary(state: TuiState) -> Optional[Text]:
    """Per-pair critique cap fill + per-speaker augment/converge fills.

    Critique: 3 per (speaker, target). Augment/converge: 3 per speaker (aggregate).
    """
    if not state.turns:
        return None
    critique_counts: dict[tuple[ModelId, Optional[ModelId]], int] = {}
    augment_counts: dict[ModelId, int] = {}
    converge_counts: dict[ModelId, int] = {}
    for t in state.turns:
        if t.channel == "critique":
            critique_counts[(t.speaker_model, t.target_model)] = (
                critique_counts.get((t.speaker_model, t.target_model), 0) + 1
            )
        elif t.channel == "augment":
            augment_counts[t.speaker_model] = augment_counts.get(t.speaker_model, 0) + 1
        else:
            converge_counts[t.speaker_model] = converge_counts.get(t.speaker_model, 0) + 1

    parts: list[str] = []
    parts.append("[dim]caps:[/] ")
    for (sp, tg), n in sorted(
        critique_counts.items(),
        key=lambda kv: (kv[0][0].value, kv[0][1].value if kv[0][1] is not None else ""),
    ):
        sp_c, tg_c = MODEL_COLOR[sp], MODEL_COLOR.get(tg, "white") if tg else "white"
        tg_short = _short_model(tg) if tg else "?"
        parts.append(
            f"[red]crit[/] [{sp_c}]{_short_model(sp)}[/]→[{tg_c}]{tg_short}[/]"
            f" [{'red' if n >= 3 else 'white'}]{n}/3[/]   "
        )
    for sp, n in sorted(augment_counts.items(), key=lambda kv: kv[0].value):
        sp_c = MODEL_COLOR[sp]
        parts.append(
            f"[blue]aug[/] [{sp_c}]{_short_model(sp)}[/] "
            f"[{'blue' if n >= 3 else 'white'}]{n}/3[/]   "
        )
    for sp, n in sorted(converge_counts.items(), key=lambda kv: kv[0].value):
        sp_c = MODEL_COLOR[sp]
        parts.append(
            f"[yellow]con[/] [{sp_c}]{_short_model(sp)}[/] "
            f"[{'yellow' if n >= 3 else 'white'}]{n}/3[/]   "
        )
    return Text.from_markup("".join(parts))


# --- layout assembly ------------------------------------------------------


def build_layout(state: TuiState) -> Layout:
    root = Layout()
    root.split_column(
        Layout(name="header", size=4),
        Layout(name="columns", ratio=4),
        Layout(name="loom", ratio=3),
        Layout(name="phase4", ratio=4),
    )
    root["columns"].split_row(
        *(Layout(name=m.value) for m in state.invited_models)
        if state.invited_models
        else [Layout(name="placeholder")]
    )
    root["phase4"].split_row(
        Layout(name="ledger", ratio=3),
        Layout(name="parity", ratio=2),
    )

    root["header"].update(render_header(state))
    if state.invited_models:
        for m in state.invited_models:
            root["columns"][m.value].update(render_model_column(state, m))
    else:
        root["columns"]["placeholder"].update(
            Panel(Text("waiting for session_started…", style="dim italic"),
                  border_style="bright_black")
        )
    root["loom"].update(render_loom(state))
    root["phase4"]["ledger"].update(render_trace_ledger(state))
    root["phase4"]["parity"].update(render_parity_panel(state))
    return root


# --- event-paced replay loop ---------------------------------------------


def play_events(
    events: Iterable[LatticeEvent],
    *,
    speed: float = 1.0,
    snapshot: bool = False,
    console: Optional[Console] = None,
) -> TuiState:
    """Drive the layout from an iterable of LatticeEvents.

    speed       — playback speed multiplier (1.0 = lived time).
    snapshot    — if True, skip the Live loop and just fold all events,
                  render the final layout once to stdout.
    """
    state = TuiState()
    console = console or Console()
    events = list(events)

    if snapshot:
        for e in events:
            apply_event(state, e)
        console.print(build_layout(state))
        return state

    with Live(
        build_layout(state),
        console=console,
        refresh_per_second=20,
        screen=False,
    ) as live:
        prev_offset = 0
        for e in events:
            delay_ms = e.timestamp_offset_ms - prev_offset
            if delay_ms > 0 and speed > 0:
                time.sleep(delay_ms / 1000.0 / speed)
            apply_event(state, e)
            live.update(build_layout(state))
            prev_offset = e.timestamp_offset_ms
    return state
