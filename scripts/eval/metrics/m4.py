"""M4 metric: confidence calibration.

Spearman's rho between Echo's final per-skill confidence and the
simulator's planted ``true_usefulness`` score. A rho near 1.0 means
Echo's confidence ranking matches reality.

Run:
    python -m scripts.eval.metrics.m4 run.jsonl
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from .common import Artifact, load, spearman_rho


@dataclass
class M4Result:
    rho: float
    n_pairs: int
    pairs: List[Dict[str, float]]   # debug payload

    def to_dict(self) -> Dict:
        return {
            "spearman_rho": self.rho,
            "n_pairs": self.n_pairs,
            "pairs": self.pairs,
        }


def compute(art: Artifact) -> M4Result:
    truth = art.skill_true_usefulness()
    conf_by_skill = {c["skill_id"]: c["confidence"] for c in art.confidence}

    pairs = []
    xs: List[float] = []
    ys: List[float] = []
    for skill_id, true_score in truth.items():
        if skill_id in conf_by_skill:
            xs.append(conf_by_skill[skill_id])
            ys.append(float(true_score))
            pairs.append({
                "skill_id": skill_id,
                "confidence": conf_by_skill[skill_id],
                "true_usefulness": float(true_score),
            })

    return M4Result(rho=spearman_rho(xs, ys), n_pairs=len(xs), pairs=pairs)


def main() -> int:
    parser = argparse.ArgumentParser(description="M4 confidence calibration")
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    art = load(args.artifact)
    res = compute(art)
    if args.json:
        print(json.dumps(res.to_dict(), indent=2))
    else:
        print(f"M4 confidence calibration (n_pairs={res.n_pairs})")
        print(f"  Spearman rho = {res.rho:+.3f}")
        for p in res.pairs:
            print(f"    {p['skill_id']:32s}  c={p['confidence']:.3f}  "
                  f"true={p['true_usefulness']:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
