"""Hyperparameter sweep over the four metrics.

This script picks a small grid of values for Echo's tunable constants
(α, β, drift threshold, warm-up window, state thresholds), runs the
simulator + metric scripts for each combination, and writes one row
per cell to a results JSONL file.

We override constants by setting module attributes before each run.
This is fine because each cell's run is fully sequential and uses its
own isolated Hermes home.

The grid is deliberately small (2 values per knob → 16 cells with
4 knobs). Bigger sweeps are a follow-up; the report's "still being
tuned" claim only needs a few datapoints to be honest.

Run:
    python -m scripts.eval.sweep --out /tmp/sweep.jsonl
"""

from __future__ import annotations

import argparse
import contextlib
import itertools
import json
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ----------------------------------------------------------------------
# Knob definitions
# ----------------------------------------------------------------------


KNOBS: Dict[str, Tuple[str, str, List[Any]]] = {
    # name → (module_path, attr_name, list of values to try)
    "alpha_explicit_pos": (
        "plugins.echo_signals.confidence", "ALPHA_EXPLICIT_POSITIVE", [0.05, 0.10],
    ),
    "gamma_explicit_neg": (
        "plugins.echo_signals.confidence", "GAMMA_EXPLICIT_NEGATIVE", [0.20, 0.30],
    ),
    "drift_threshold_z": (
        "plugins.echo_signals.baseline", "DRIFT_THRESHOLD_Z", [1.5, 2.0],
    ),
    "n_warm": (
        "plugins.echo_signals.baseline", "N_WARM", [10, 20],
    ),
}


@contextlib.contextmanager
def _override(overrides: Dict[str, Any]):
    """Temporarily set module attributes to the values in `overrides`.

    `overrides` keys are entries from KNOBS. Restores prior values on
    exit regardless of exceptions.
    """
    import importlib

    saved: List[Tuple[str, str, Any]] = []
    try:
        for knob_name, value in overrides.items():
            module_path, attr_name, _ = KNOBS[knob_name]
            mod = importlib.import_module(module_path)
            saved.append((module_path, attr_name, getattr(mod, attr_name)))
            setattr(mod, attr_name, value)
        yield
    finally:
        for module_path, attr_name, old_value in saved:
            mod = importlib.import_module(module_path)
            setattr(mod, attr_name, old_value)


# ----------------------------------------------------------------------
# One cell
# ----------------------------------------------------------------------


@dataclass
class CellResult:
    overrides: Dict[str, Any]
    m1: Dict[str, float]
    m3: Dict[str, float]
    m4: Dict[str, float]
    elapsed_sec: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "overrides": self.overrides,
            "m1": self.m1,
            "m3": self.m3,
            "m4": self.m4,
            "elapsed_sec": self.elapsed_sec,
        }


def run_cell(overrides: Dict[str, Any]) -> CellResult:
    """Run one (overrides → metrics) cell."""
    from scripts.eval import harness as H
    from scripts.eval.metrics import common, m1, m3, m4

    start = time.monotonic()
    with _override(overrides):
        tmp = Path(tempfile.mkdtemp(prefix="echo-sweep-"))
        try:
            out = tmp / "run.jsonl"
            h = H.Harness(out_path=out, hermes_home=tmp / "home")
            for s in H.build_default_scenarios():
                h.add_scenario(s)
            h.run()
            h.dump()

            art = common.load(out)
            m1_res = m1.compute(art).to_dict()
            m3_res = m3.compute(art).to_dict()
            m4_res = m4.compute(art).to_dict()
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    return CellResult(
        overrides=overrides,
        m1=m1_res,
        m3=m3_res,
        m4=m4_res,
        elapsed_sec=time.monotonic() - start,
    )


# ----------------------------------------------------------------------
# Grid driver
# ----------------------------------------------------------------------


def grid(knob_names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Cartesian product of the value lists for the chosen knobs."""
    if knob_names is None:
        knob_names = list(KNOBS.keys())
    value_lists = [KNOBS[n][2] for n in knob_names]
    return [
        dict(zip(knob_names, combo)) for combo in itertools.product(*value_lists)
    ]


def run(out_path: Path, knob_names: Optional[List[str]] = None,
        max_cells: Optional[int] = None) -> List[CellResult]:
    cells = grid(knob_names)
    if max_cells is not None:
        cells = cells[:max_cells]

    results: List[CellResult] = []
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for i, ov in enumerate(cells, 1):
            print(f"[{i}/{len(cells)}] {ov}")
            r = run_cell(ov)
            f.write(json.dumps(r.to_dict()) + "\n")
            results.append(r)
    return results


# ----------------------------------------------------------------------
# Summary printer
# ----------------------------------------------------------------------


def summarise(results: List[CellResult]) -> Dict[str, Any]:
    """Pick the best cell by each metric and report it."""
    if not results:
        return {}

    def best_by(key: str, lookup):
        winner = max(results, key=lookup)
        return {"overrides": winner.overrides, key: lookup(winner)}

    return {
        "n_cells": len(results),
        "best_m1_echo_precision": best_by("value", lambda c: c.m1["echo_precision"]),
        "best_m3_f1":              best_by("value", lambda c: c.m3["f1"]),
        "best_m4_rho":             best_by("value", lambda c: c.m4["spearman_rho"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Echo hyperparameter sweep")
    parser.add_argument("--out", type=Path, default=Path("/tmp/echo-sweep.jsonl"))
    parser.add_argument(
        "--knobs", nargs="*", default=None,
        help="Subset of knob names to vary (default: all)",
    )
    parser.add_argument(
        "--max-cells", type=int, default=None,
        help="Cap on number of cells to run (handy for smoke testing).",
    )
    args = parser.parse_args()

    results = run(args.out, knob_names=args.knobs, max_cells=args.max_cells)
    summary = summarise(results)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
