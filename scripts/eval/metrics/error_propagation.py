"""Metric 2 — error-propagation rate (proposal §4 Metric 2).

The proposal: inject K "looks-successful-but-actually-wrong" skills and
track how long they survive across three arms. Echo should detect the bad
skills through accumulated negative signals and retire them; a
frequency-decay baseline (Baseline B — agentmemory / Hermes-style: usage
raises confidence, silence/negative never lowers it) keeps them alive
forever.

This script is self-contained: it plants K bad skills (each used N times,
every use producing a negative user-sentiment turn) plus a couple of good
control skills (positive sentiment), runs them through the real Echo
plugin via the harness, then compares two arms:

  * Echo arm        — real confidence engine. A bad skill is "caught" when
                      it ends retired or below C_MIN. Good controls must
                      NOT be caught (false-positive guard).
  * Baseline B arm  — frequency-decay model computed from the same
                      invocation counts: confidence rises with use and
                      never falls, so it catches 0 bad skills by construction.

Run:
    python -m scripts.eval.metrics.error_propagation
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

# Planted library.
N_BAD_SKILLS = 3
N_GOOD_SKILLS = 2
USES_PER_SKILL = 12          # enough negative turns to drive a bad skill to retire
BAD_USEFULNESS = 0.1
GOOD_USEFULNESS = 0.9

# Baseline B frequency-decay model: confidence grows with use, never falls.
BASELINE_B_BASE = 0.5
BASELINE_B_PER_USE = 0.02


@dataclass
class ErrorPropagationResult:
    n_bad: int
    n_good: int
    echo_bad_caught: int          # bad skills Echo retired / pushed below C_MIN
    echo_good_false_positives: int  # good skills Echo wrongly caught
    baseline_b_bad_caught: int    # always 0 by construction
    echo_mean_conf_bad: float
    echo_mean_conf_good: float

    @property
    def echo_catch_rate(self) -> float:
        return self.echo_bad_caught / self.n_bad if self.n_bad else 0.0

    @property
    def baseline_b_catch_rate(self) -> float:
        return self.baseline_b_bad_caught / self.n_bad if self.n_bad else 0.0

    def to_dict(self) -> Dict:
        return {
            "n_bad": self.n_bad,
            "n_good": self.n_good,
            "echo_bad_caught": self.echo_bad_caught,
            "echo_catch_rate": self.echo_catch_rate,
            "echo_good_false_positives": self.echo_good_false_positives,
            "baseline_b_bad_caught": self.baseline_b_bad_caught,
            "baseline_b_catch_rate": self.baseline_b_catch_rate,
            "echo_mean_conf_bad": self.echo_mean_conf_bad,
            "echo_mean_conf_good": self.echo_mean_conf_good,
        }


def _build_scenario():
    from scripts.eval.harness import (
        GroundTruth, Invocation, Scenario, Session, UserTurn,
    )

    sessions: List = []
    truth: Dict[str, float] = {}

    def _runs(skill_id: str, sentiment: str, text: str):
        for i in range(USES_PER_SKILL):
            sessions.append(Session(
                session_id=f"{skill_id}-{i}",
                invocations=[Invocation(
                    skill_id=skill_id,
                    turns=[UserTurn(text=text, expected_sentiment=sentiment)],
                )],
            ))

    for k in range(N_BAD_SKILLS):
        sid = f"bad-skill-{k}"
        truth[sid] = BAD_USEFULNESS
        _runs(sid, "negative", "this output is wrong again, not what I wanted")

    for k in range(N_GOOD_SKILLS):
        sid = f"good-skill-{k}"
        truth[sid] = GOOD_USEFULNESS
        _runs(sid, "positive", "perfect, exactly what I needed, thank you")

    return Scenario(
        name="error_propagation",
        sessions=sessions,
        ground_truth=GroundTruth(skill_true_usefulness=truth),
    )


def compute(*, home: Optional[Path] = None) -> ErrorPropagationResult:
    from scripts.eval.harness import Harness
    from scripts.eval.metrics.common import load

    owns_home = home is None
    home = home or Path(tempfile.mkdtemp(prefix="echo-ep-"))
    out = home / "run.jsonl"
    try:
        h = Harness(out_path=out, hermes_home=home / "hh")
        h.add_scenario(_build_scenario())
        h.run()
        h.dump()
        art = load(out)

        # Map skill → final confidence/status + use count.
        conf = {c["skill_id"]: c for c in art.confidence}
        use_count: Dict[str, int] = {}
        for inv in art.invocations:
            use_count[inv["skill_id"]] = use_count.get(inv["skill_id"], 0) + 1

        truth = art.skill_true_usefulness()
        bad = [s for s, u in truth.items() if u < 0.3]
        good = [s for s, u in truth.items() if u >= 0.7]

        # Echo arm: caught = retired or confidence below C_MIN.
        try:
            from plugins.echo_signals.confidence import C_MIN
        except Exception:
            C_MIN = 0.30

        def _caught(skill_id: str) -> bool:
            row = conf.get(skill_id)
            if row is None:
                return False
            return row["status"] == "retired" or float(row["confidence"]) < C_MIN

        echo_bad_caught = sum(1 for s in bad if _caught(s))
        echo_good_fp = sum(1 for s in good if _caught(s))

        def _mean_conf(skills: List[str]) -> float:
            vals = [float(conf[s]["confidence"]) for s in skills if s in conf]
            return sum(vals) / len(vals) if vals else 0.0

        # Baseline B: frequency decay — confidence only rises with use.
        def _baseline_b_conf(skill_id: str) -> float:
            return min(1.0, BASELINE_B_BASE + BASELINE_B_PER_USE * use_count.get(skill_id, 0))

        baseline_b_caught = sum(
            1 for s in bad if _baseline_b_conf(s) < C_MIN
        )  # 0 by construction — never decays

        return ErrorPropagationResult(
            n_bad=len(bad),
            n_good=len(good),
            echo_bad_caught=echo_bad_caught,
            echo_good_false_positives=echo_good_fp,
            baseline_b_bad_caught=baseline_b_caught,
            echo_mean_conf_bad=_mean_conf(bad),
            echo_mean_conf_good=_mean_conf(good),
        )
    finally:
        if owns_home:
            shutil.rmtree(home, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Metric 2 — error propagation")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    res = compute()
    if args.json:
        print(json.dumps(res.to_dict(), indent=2))
    else:
        print(f"Metric 2 — error propagation  ({res.n_bad} bad, {res.n_good} good skills)")
        print(f"  Echo:       caught {res.echo_bad_caught}/{res.n_bad} bad "
              f"(rate {res.echo_catch_rate:.2f}), "
              f"{res.echo_good_false_positives} good false-positives")
        print(f"  Baseline B: caught {res.baseline_b_bad_caught}/{res.n_bad} bad "
              f"(rate {res.baseline_b_catch_rate:.2f}) — frequency decay never retires")
        print(f"  Echo mean confidence: bad={res.echo_mean_conf_bad:.3f}  "
              f"good={res.echo_mean_conf_good:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
