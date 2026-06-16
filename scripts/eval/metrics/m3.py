"""M3 metric: drift detection precision/recall.

Reads the Harness artifact's ``kind=drift`` rows (events that fired)
and compares them to the planted ``should_drift`` labels.

Invocations whose baseline is not yet ready (warm-up window) are
excluded from the metric — the detector physically cannot fire on
them, so counting them as FN would be unfair.

Run:
    python -m scripts.eval.metrics.m3 run.jsonl
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Set

from .common import Artifact, load


# Mirrors baseline.N_WARM. Hard-coded rather than imported so this script
# stays consumable without bringing in the Echo plugin.
N_WARM = 8


@dataclass
class M3Result:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    excluded_warmup: int = 0

    @property
    def precision(self) -> float:
        denom = self.tp + self.fp
        return self.tp / denom if denom else 0.0

    @property
    def recall(self) -> float:
        denom = self.tp + self.fn
        return self.tp / denom if denom else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    def to_dict(self) -> Dict[str, float]:
        return {
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "tn": self.tn,
            "excluded_warmup": self.excluded_warmup,
        }


def compute(art: Artifact) -> M3Result:
    # Count how many invocations of each skill came before any given
    # invocation_id, so we can identify which are still in warm-up.
    by_skill_sorted: Dict[str, List[int]] = {}
    for inv in art.invocations:
        by_skill_sorted.setdefault(inv["skill_id"], []).append(inv["invocation_id"])
    for ids in by_skill_sorted.values():
        ids.sort()

    def warmed_up_at(inv_id: int, skill_id: str) -> bool:
        ids = by_skill_sorted.get(skill_id, [])
        try:
            idx = ids.index(inv_id)
        except ValueError:
            return False
        return idx >= N_WARM

    drifted_at: Set[int] = {d["invocation_id"] for d in art.drifts}

    res = M3Result()
    for gt_inv in art.gt_invocations():
        inv_id = art.invocation_id_for(
            session_id=gt_inv["session_id"], skill_id=gt_inv["skill_id"],
        )
        if inv_id is None:
            continue
        if not warmed_up_at(inv_id, gt_inv["skill_id"]):
            res.excluded_warmup += 1
            continue

        should = bool(gt_inv.get("should_drift"))
        did = inv_id in drifted_at
        if should and did:
            res.tp += 1
        elif should and not did:
            res.fn += 1
        elif not should and did:
            res.fp += 1
        else:
            res.tn += 1
    return res


def main() -> int:
    parser = argparse.ArgumentParser(description="M3 drift precision/recall")
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    art = load(args.artifact)
    res = compute(art)
    if args.json:
        print(json.dumps(res.to_dict(), indent=2))
    else:
        print(f"M3 drift precision/recall  (warmup-excluded: {res.excluded_warmup})")
        print(f"  P={res.precision:.3f}  R={res.recall:.3f}  F1={res.f1:.3f}")
        print(f"  TP={res.tp}  FP={res.fp}  FN={res.fn}  TN={res.tn}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
