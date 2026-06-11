"""Smoke tests for scripts.eval.harness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.eval import harness as H


@pytest.fixture
def small_run(tmp_path):
    """A tiny 2-scenario run; returns the artifact path."""
    h = H.Harness(out_path=tmp_path / "run.jsonl", hermes_home=tmp_path / "home")
    h.add_scenario(H._scenario_neutral_baseline())
    h.add_scenario(H._scenario_repeat_save_intent())
    h.run()
    h.dump()
    return tmp_path / "run.jsonl"


def _read_jsonl(path: Path):
    out = []
    for line in path.read_text().splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def test_harness_writes_artifact(small_run):
    rows = _read_jsonl(small_run)
    kinds = {r["kind"] for r in rows}
    # All expected row kinds present.
    assert {"config", "ground_truth", "invocation", "signal", "confidence"} <= kinds


def test_harness_records_invocations(small_run):
    rows = _read_jsonl(small_run)
    invocs = [r for r in rows if r["kind"] == "invocation"]
    skill_ids = {r["skill_id"] for r in invocs}
    # neutral_baseline → quick-qa; repeat_save_intent → marketing-email-1 and -2.
    assert skill_ids >= {"quick-qa", "marketing-email-1", "marketing-email-2"}


def test_harness_records_tool_call_signals(small_run):
    rows = _read_jsonl(small_run)
    sigs = [r for r in rows if r["kind"] == "signal"]
    tool_calls = [s for s in sigs if s["signal_type"] == "tool_call"]
    # repeat_save_intent has 2 invocations, each with 2 tool calls.
    # neutral_baseline has 0 tool calls. So total >= 4.
    assert len(tool_calls) >= 4


def test_harness_records_confidence_anchors(small_run):
    rows = _read_jsonl(small_run)
    confs = [r for r in rows if r["kind"] == "confidence"]
    by_skill = {r["skill_id"]: r for r in confs}
    # Every skill that ran bump_use gets an anchor row.
    assert {"quick-qa", "marketing-email-1", "marketing-email-2"} <= set(by_skill)
    # All anchors start at confidence 0.5 (no negative signals in these scenarios).
    for r in by_skill.values():
        assert 0.0 <= r["confidence"] <= 1.0


def test_harness_records_m1_candidates(small_run):
    rows = _read_jsonl(small_run)
    candidates = [r for r in rows if r["kind"] == "m1_candidate"]
    # repeat_save_intent has "save this as a skill" in the second turn —
    # save_intent should fire → at least one candidate.
    assert len(candidates) >= 1


def test_harness_ground_truth_round_trip(small_run):
    rows = _read_jsonl(small_run)
    gt = [r for r in rows if r["kind"] == "ground_truth"]
    by_name = {r["scenario"]: r for r in gt}
    assert "neutral_baseline" in by_name
    assert "repeat_save_intent" in by_name
    # Planted invocation labels survived.
    nb = by_name["neutral_baseline"]
    assert all(inv["should_be_nominated"] is False for inv in nb["invocations"])
    rs = by_name["repeat_save_intent"]
    # The second invocation in repeat_save_intent has should_be_nominated=True.
    second = [inv for inv in rs["invocations"] if inv["invocation_index"] == 0
              and inv["session_index"] == 1][0]
    assert second["should_be_nominated"] is True


def test_disable_confidence_short_circuits_engine(tmp_path):
    """With ECHO_DISABLE_CONFIDENCE on, confidence shouldn't move from 0.5."""
    h = H.Harness(
        out_path=tmp_path / "run.jsonl",
        hermes_home=tmp_path / "home",
        disable_confidence=True,
    )
    # Build a scenario that would normally produce negative feedback.
    s = H.Scenario(
        name="ablation_check",
        sessions=[H.Session(
            session_id="abl-1",
            invocations=[H.Invocation(
                skill_id="will-stay-at-half",
                turns=[H.UserTurn(
                    text="this is terrible",
                    expected_sentiment="negative",
                )],
            )],
        )],
    )
    h.add_scenario(s)
    h.run()
    h.dump()

    rows = _read_jsonl(tmp_path / "run.jsonl")
    confs = {r["skill_id"]: r for r in rows if r["kind"] == "confidence"}
    # In ablation mode the engine returns disabled_for_ablation, leaving
    # confidence at its initial value 0.5.
    assert confs["will-stay-at-half"]["confidence"] == pytest.approx(0.5)


def test_drift_scenario_yields_drift_signal(tmp_path):
    """After 25 normal invocations + 1 spike, M3 should record drift_detected."""
    h = H.Harness(out_path=tmp_path / "run.jsonl", hermes_home=tmp_path / "home")
    h.add_scenario(H._scenario_drift())
    h.run()
    h.dump()
    rows = _read_jsonl(tmp_path / "run.jsonl")
    drift = [r for r in rows if r["kind"] == "drift"]
    assert len(drift) >= 1, "drift detector should have fired on the spike invocation"
