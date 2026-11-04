"""Offline audit checks on synthetic session data (hardening, no model calls)."""

from __future__ import annotations

import json
from pathlib import Path

from golden_lattice.evaluation.audit import (
    audit_run,
    audit_session,
    channel_pressure,
    claim_concentration,
    distinct_insights,
    null_targets_by_channel,
    parity_readings,
    same_claim_agree_and_disagree,
)

OPUS, SONNET, HAIKU = "claude-opus-5", "claude-sonnet-5", "claude-haiku-5-5"


def _reading(reader, target, agree, disagree):
    return {
        "reader_model": reader,
        "target_model": target,
        "agreements": [{"claim_id": c} for c in agree],
        "disagreements": [{"target_claim_id": c, "reason": "r"} for c in disagree],
    }


def test_same_claim_agreed_and_disagreed_is_found():
    phase_2 = [_reading(HAIKU, OPUS, agree=["a1", "a2"], disagree=["a2", "b1"])]
    hits = same_claim_agree_and_disagree(phase_2)
    assert hits == [{"reader": HAIKU, "target": OPUS, "claim_id": "a2"}]


def test_clean_cross_reading_has_no_hits():
    phase_2 = [_reading(SONNET, OPUS, agree=["a1"], disagree=["a2"])]
    assert same_claim_agree_and_disagree(phase_2) == []


def _turn(speaker, channel, target=None, ids=()):
    return {"speaker_model": speaker, "channel": channel,
            "target_model": target, "target_claim_ids": list(ids)}


def test_channel_pressure_flags_at_cap_and_over_cap():
    turns = [_turn(OPUS, "converge")] * 3 + [_turn(SONNET, "converge")] * 4 + [_turn(HAIKU, "augment")]
    report = channel_pressure(turns)
    assert (OPUS, "converge") in report["at_cap"]
    assert (SONNET, "converge") in report["over_cap"]
    assert (OPUS, "converge") not in report["over_cap"]


def test_critique_cap_is_per_speaker_and_target_not_aggregate():
    # 2 + 2 critiques to two different peers: each pair is under the cap.
    split = [_turn(OPUS, "critique", target=SONNET, ids=["x"])] * 2 + \
            [_turn(OPUS, "critique", target=HAIKU, ids=["y"])] * 2
    assert channel_pressure(split)["over_cap"] == []
    # 4 critiques to the same peer: over the per-pair cap.
    same = [_turn(OPUS, "critique", target=SONNET, ids=["x"])] * 4
    assert (OPUS, f"critique->{SONNET}") in channel_pressure(same)["over_cap"]


def test_null_targets_counted_per_channel():
    turns = [_turn(OPUS, "augment"), _turn(OPUS, "critique", target=SONNET, ids=["x"])]
    counts = null_targets_by_channel(turns)
    assert counts["augment"] == {"turns": 1, "null_target": 1}
    assert counts["critique"] == {"turns": 1, "null_target": 0}


def test_claim_concentration_counts_all_citations():
    phase_2 = [_reading(HAIKU, OPUS, agree=["a1", "a1"], disagree=["a1"])]
    phase_3 = [_turn(SONNET, "critique", target=OPUS, ids=["a1", "b2"])]
    result = claim_concentration(phase_2, phase_3)
    assert result["max_cites"] == 4
    assert result["top_claim_id"] == "a1"
    assert result["distinct_cited"] == 2


def test_identical_claims_across_models_merge_at_half_band():
    phase_1 = {
        OPUS: {"claims": [{"text": "use a write ahead log for durability"}]},
        SONNET: {"claims": [{"text": "Use a write-ahead log for durability"}]},
        HAIKU: {"claims": [{"text": "shard the ledger by account"}]},
    }
    result = distinct_insights(phase_1)
    assert result["raw"] == 3
    assert result["clusters"]["0.5"] == 2
    assert result["merged_at_0_5"] == 1


def test_parity_readings_report_both_flag_rules():
    metrics = {
        "parity_threshold": 0.15,
        "distinct_claim_share": {OPUS: 0.6, SONNET: 0.1, HAIKU: 0.3},
        "edge_case_coverage_share": {OPUS: 0.7, SONNET: 0.1, HAIKU: 0.1},
        "structural_pattern_share": {OPUS: 0.8, SONNET: 0.1, HAIKU: 0.1},
    }
    readings = parity_readings(metrics)
    assert readings["flag_any_share"] is True
    assert readings["flag_all_three"] is True
    assert readings["models_below_all_three"] == [SONNET]


def test_parity_rules_disagree_when_a_model_is_low_on_two_of_three():
    metrics = {
        "parity_threshold": 0.15,
        "distinct_claim_share": {OPUS: 0.6, SONNET: 0.1, HAIKU: 0.3},
        "edge_case_coverage_share": {OPUS: 0.7, SONNET: 0.2, HAIKU: 0.1},
        "structural_pattern_share": {OPUS: 0.8, SONNET: 0.1, HAIKU: 0.1},
    }
    readings = parity_readings(metrics)
    assert readings["flag_any_share"] is True
    assert readings["flag_all_three"] is False
    assert readings["models_below_all_three"] == []


def test_parity_none_for_dyad_is_not_a_flag():
    readings = parity_readings({"distinct_claim_share": {OPUS: None, SONNET: None}})
    assert readings["flag_any_share"] is False
    assert readings["flag_all_three"] is False


def test_audit_session_returns_every_section():
    session = {
        "session_id": "s1",
        "phase_1": {OPUS: {"claims": [{"text": "alpha beta"}]}},
        "phase_2": [],
        "phase_3": [],
        "metrics": {"parity_threshold": 0.15},
    }
    result = audit_session(session)
    assert set(result) == {
        "session_id", "same_claim_agree_and_disagree", "channel_pressure",
        "null_targets_by_channel", "claim_concentration", "distinct_insights", "parity",
    }


def test_audit_run_writes_per_session_and_summary(tmp_path: Path):
    session = {"session_id": "s1", "phase_1": {}, "phase_2": [], "phase_3": [], "metrics": {}}
    (tmp_path / "s1.session.json").write_text(json.dumps(session), encoding="utf-8")
    summary = audit_run(tmp_path)
    assert summary["sessions"] == 1
    assert (tmp_path / "s1.audit.json").is_file()
    assert (tmp_path / "run_audit.json").is_file()
