"""End-to-end tests for the four metric scripts.

Runs the harness once on the default scenarios, then invokes each
metric's ``compute()`` against the artifact and asserts the result
shape is sensible. We deliberately don't pin exact numbers — the
metric values depend on the working hyperparameters and may shift
when those are tuned — but we do pin sign/magnitude bounds.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.eval import harness as H
from scripts.eval.metrics import common, m1, m3, m4, m5


@pytest.fixture(scope="module")
def artifact(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("eval") / "run.jsonl"
    home = tmp_path_factory.mktemp("home")
    h = H.Harness(out_path=out, hermes_home=home)
    for s in H.build_default_scenarios():
        h.add_scenario(s)
    h.run()
    h.dump()
    return out


# ---------------------------------------------------------------------
# Common loader
# ---------------------------------------------------------------------


def test_artifact_loads(artifact):
    art = common.load(artifact)
    assert art.config != {}
    assert len(art.ground_truth) == 4   # one per default scenario
    assert len(art.invocations) > 0
    assert len(art.confidence) > 0


def test_spearman_basic():
    # Perfectly correlated.
    assert common.spearman_rho([1, 2, 3, 4], [1, 2, 3, 4]) == pytest.approx(1.0)
    # Perfectly anti-correlated.
    assert common.spearman_rho([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    # Degenerate cases.
    assert common.spearman_rho([], []) == 0.0
    assert common.spearman_rho([1, 1, 1], [1, 2, 3]) == 0.0


# ---------------------------------------------------------------------
# M1 nomination precision
# ---------------------------------------------------------------------


def test_m1_runs(artifact):
    art = common.load(artifact)
    result = m1.compute(art)
    # We planted some relevant invocations in the scenarios.
    assert result.n_relevant > 0
    # Precision/recall are in [0, 1].
    assert 0.0 <= result.precision_echo <= 1.0
    assert 0.0 <= result.recall_echo <= 1.0
    assert 0.0 <= result.precision_hermes <= 1.0
    assert 0.0 <= result.recall_hermes <= 1.0


def test_m1_serialisation(artifact):
    art = common.load(artifact)
    d = m1.compute(art).to_dict()
    # Round-trips through JSON.
    json.loads(json.dumps(d))
    assert "echo_precision" in d
    assert "hermes_precision" in d


# ---------------------------------------------------------------------
# M3 drift precision/recall
# ---------------------------------------------------------------------


def test_m3_runs(artifact):
    art = common.load(artifact)
    res = m3.compute(art)
    # The drift scenario plants one true-positive on the 26th invocation
    # (after the 20-invocation warm-up), so we expect:
    #   tp >= 1, fp == 0 if the detector is well-calibrated on this set.
    assert res.tp + res.fn >= 1, "should have at least one ground-truth drift"
    assert res.tp >= 1, "drift detector should have caught the spike"
    assert 0.0 <= res.precision <= 1.0
    assert 0.0 <= res.recall <= 1.0


# ---------------------------------------------------------------------
# M4 confidence calibration
# ---------------------------------------------------------------------


def test_m4_runs(artifact):
    art = common.load(artifact)
    res = m4.compute(art)
    assert res.n_pairs > 0
    assert -1.0 <= res.rho <= 1.0


# ---------------------------------------------------------------------
# M5 retrieval uplift (self-contained)
# ---------------------------------------------------------------------


def test_m5_runs(tmp_path):
    res = m5.compute(home=tmp_path / "m5-home")
    # Recalls in [0, 1].
    assert 0.0 <= res.recall_with_weights <= 1.0
    assert 0.0 <= res.recall_no_weights <= 1.0
    # The library is set up so the planted-relevant skills all have high
    # confidence — weighting should NOT hurt recall.
    assert res.recall_with_weights >= res.recall_no_weights - 1e-6
