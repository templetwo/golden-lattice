#!/usr/bin/env python3
"""Third live Lattice session — three Claudes on a code-review prompt.

This is the first live session that puts the wire layer on a code block.
The two prior runs (cache eviction policy, Lucumi practice) tested
analytical and spiritual domains; code is new territory for the wire
layer's tagging vocabulary.

The default prompt embeds `generate_emotional_code` from emo-lang's
`manifestation_loop.py` (Anthony Vasquez Sr.) verbatim and asks each
Claude for an open-ended read. No fixed brief, no leading frame: each
model surfaces what strikes it. The function has no known bugs, which
makes this a clean speed-vs-depth asymmetry test rather than a
bug-finding test.

Usage:
  ANTHROPIC_API_KEY=sk-... python scripts/run_code_session.py
  ANTHROPIC_API_KEY=sk-... python scripts/run_code_session.py path/to/other_prompt.txt
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from golden_lattice.memory_graph.metrics import compute_parity_shares
from golden_lattice.memory_graph.store import JsonFileSessionStore
from golden_lattice.orchestrator import (
    AnthropicClient,
    LatticeConfig,
    run_lattice_session,
)


SCRIPTS_DIR = Path(__file__).parent
DEFAULT_PROMPT_FILE = SCRIPTS_DIR / "prompts" / "code_review_generate_emotional_code.txt"


def _phase_banner(title: str) -> None:
    print("=" * 70)
    print(title)
    print("=" * 70)


def main() -> int:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set in environment.", file=sys.stderr)
        return 1

    prompt_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PROMPT_FILE
    if not prompt_path.exists():
        print(f"ERROR: prompt file not found: {prompt_path}", file=sys.stderr)
        return 1
    prompt = prompt_path.read_text(encoding="utf-8")

    config = LatticeConfig(
        api_key=api_key,
        timeout_phase_1_seconds=120.0,
        timeout_self_reflection_seconds=60.0,
        timeout_phase_2_seconds=120.0,
        timeout_phase_3_seconds=120.0,
        confidence_threshold=0.7,
    )
    client = AnthropicClient(api_key=api_key)

    _phase_banner("THIRD LIVE LATTICE SESSION (code domain)")
    print(f"Prompt source: {prompt_path}")
    print(f"Prompt length: {len(prompt)} chars")
    print("-" * 70)
    print("Dispatching to Opus, Sonnet, Haiku... (this takes 60-180 seconds)")
    print("Output will be silent until the full pipeline completes.")
    print()
    sys.stdout.flush()

    started = time.time()
    session = run_lattice_session(prompt, config=config, client=client)
    elapsed = time.time() - started

    print(f"Session complete in {elapsed:.1f}s")
    print(f"Session ID: {session.session_id}")
    print()

    # Phase 1 summary.
    _phase_banner("PHASE 1 - Independent generation")
    for model, response in session.phase_1.items():
        print(f"\n--- {model.value} ---")
        print(f"  focus_tag: {response.focus_tag.value}")
        print(f"  confidence: {response.confidence}")
        print(f"  claims: {len(response.claims)}")
        for c in response.claims:
            print(f"    [{c.claim_id}] {c.text}")
        if response.self_reflection_artifacts:
            r = response.self_reflection_artifacts[0]
            print(f"  reflection:")
            print(f"    strongest: {r.strongest_claim_id}")
            print(f"    weakest:   {r.weakest_claim_id}")
            print(f"    justification: {r.tag_justification}")

    # Phase 2 summary.
    print()
    _phase_banner("PHASE 2 - Cross-reading + tagging")
    print(f"  cross-readings: {len(session.phase_2)}")
    print(f"  taggings:       {len(session.phase_2_taggings)}")
    for cr in session.phase_2:
        n_a = len(cr.agreements)
        n_d = len(cr.disagreements)
        n_m = len(cr.missing)
        print(
            f"    {cr.reader_model.value} -> {cr.target_model.value}: "
            f"{n_a} agreements, {n_d} disagreements, {n_m} missing"
        )

    # Phase 3 summary.
    print()
    _phase_banner("PHASE 3 - Structured dialogue")
    print(f"  dialogue turns: {len(session.phase_3)}")
    by_speaker: dict = {}
    for turn in session.phase_3:
        key = (turn.speaker_model.value, turn.channel)
        by_speaker.setdefault(key, 0)
        by_speaker[key] += 1
    for (speaker, channel), count in sorted(by_speaker.items()):
        print(f"    {speaker} {channel}: {count}")

    # Phase 4 summary.
    print()
    _phase_banner("PHASE 4 - Synthesis (annotated mode)")
    artifact = session.phase_4
    assert artifact is not None
    print(f"  output_mode: {artifact.output_mode.value}")
    print(f"  rules_applied: {[r.value for r in artifact.synthesis_rules_applied]}")
    print(f"  elevations: {len(artifact.elevations)}")
    print(f"  surfaced_disagreements: {len(artifact.surfaced_disagreements)}")
    print(f"  claim_trace entries: {len(artifact.claim_trace)}")
    n_present = sum(1 for e in artifact.claim_trace if e.disposition == "present")
    n_modified = sum(1 for e in artifact.claim_trace if e.disposition == "modified")
    n_omitted = sum(1 for e in artifact.claim_trace if e.disposition == "omitted")
    print(f"    present:  {n_present}")
    print(f"    modified: {n_modified}")
    print(f"    omitted:  {n_omitted}")

    if artifact.elevations:
        print()
        print("  --- Elevation content ---")
        for i, elev in enumerate(artifact.elevations):
            print(f"    Elevation {i}:")
            print(f"      claim_ids: {list(elev.claim_ids)}")
            print(f"      converge_turn_ids: {list(elev.converge_turn_ids)}")

    if artifact.surfaced_disagreements:
        print()
        print("  --- Surfaced disagreement content ---")
        for i, sd in enumerate(artifact.surfaced_disagreements):
            print(f"    SurfacedDisagreement {i}:")
            print(f"      claim_ids: {list(sd.claim_ids)}")
            print(f"      note: {sd.note}")

    # Parity metrics (spec §5.4). Uses substrate's PARITY_THRESHOLD,
    # not the config's confidence_threshold — different concepts.
    print()
    _phase_banner("PARITY METRICS (spec section 5.4)")
    from golden_lattice.memory_graph.base import PARITY_THRESHOLD
    metrics = compute_parity_shares(session, threshold=PARITY_THRESHOLD)
    if metrics is None:
        print("  (Parity metrics undefined - fewer than 3 invited models.)")
    else:
        print(f"  parity_threshold: {metrics.parity_threshold}")
        print(f"  parity_below_threshold: {metrics.parity_below_threshold}")
        print()
        print("  distinct_claim_share:")
        for m, share in metrics.distinct_claim_share.items():
            print(f"    {m.value}: {share:.3f}")
        print("  edge_case_coverage_share:")
        for m, share in metrics.edge_case_coverage_share.items():
            print(f"    {m.value}: {share:.3f}")
        print("  structural_pattern_share:")
        for m, share in metrics.structural_pattern_share.items():
            print(f"    {m.value}: {share:.3f}")
        if metrics.parity_violations:
            print()
            print("  PARITY VIOLATIONS (below threshold):")
            for label, model, share in metrics.parity_violations:
                print(f"    {label}[{model.value}] = {share:.3f}")

    print()
    print("--- ANNOTATED OUTPUT ---")
    print()
    print(artifact.output)
    print()
    print("--- END OUTPUT ---")
    print()

    sessions_dir = Path.home() / "golden-lattice" / "sessions"
    store = JsonFileSessionStore(sessions_dir)
    store.save(session)
    session_path = sessions_dir / f"{session.session_id}{store.SUFFIX}"
    print(f"Session persisted via JsonFileSessionStore: {session_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
