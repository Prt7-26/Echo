"""Smoke tests for the hyperparameter sweep driver."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.eval import sweep


def test_grid_has_expected_size():
    # Default: 4 knobs × 2 values each = 16 cells.
    assert len(sweep.grid()) == 16


def test_grid_subset():
    # Just one knob.
    cells = sweep.grid(["n_warm"])
    assert len(cells) == 2
    assert {c["n_warm"] for c in cells} == {10, 20}


def test_run_cell_produces_metrics():
    # One specific cell.
    res = sweep.run_cell({"alpha_explicit_pos": 0.10, "n_warm": 20})
    assert "echo_precision" in res.m1
    assert "spearman_rho" in res.m4
    assert "f1" in res.m3
    assert res.elapsed_sec > 0


def test_override_restores_defaults():
    """After run_cell the module attributes are back to their defaults."""
    from plugins.echo_signals import confidence, baseline
    orig_alpha = confidence.ALPHA_EXPLICIT_POSITIVE
    orig_z = baseline.DRIFT_THRESHOLD_Z

    sweep.run_cell({"alpha_explicit_pos": 0.05, "drift_threshold_z": 1.5})

    assert confidence.ALPHA_EXPLICIT_POSITIVE == orig_alpha
    assert baseline.DRIFT_THRESHOLD_Z == orig_z


def test_run_writes_jsonl(tmp_path):
    out = tmp_path / "sweep.jsonl"
    results = sweep.run(out, knob_names=["n_warm"])
    # 2 cells; each line is one JSON record.
    lines = out.read_text().strip().splitlines()
    assert len(lines) == 2
    for line in lines:
        row = json.loads(line)
        assert "overrides" in row
        assert "m1" in row
        assert "m3" in row
        assert "m4" in row


def test_summarise_returns_winners(tmp_path):
    results = sweep.run(tmp_path / "sweep.jsonl", knob_names=["n_warm"])
    s = sweep.summarise(results)
    assert s["n_cells"] == 2
    assert "best_m1_echo_precision" in s
    assert "best_m3_f1" in s
    assert "best_m4_rho" in s
