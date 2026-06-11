"""M1 metric: nomination precision.

Reads a Harness JSONL artifact and computes:

  precision_echo   = TP / (TP + FP)  where Echo's m1_candidates are the
                     positive set, and ground-truth ``should_be_nominated``
                     is the relevance label.
  recall_echo      = TP / (TP + FN)
  precision_thresh = same, but the positive set is Hermes' "≥5 tool calls"
                     rule alone (read from the runtime invocation rows).

Run:
    python -m scripts.eval.metrics.m1 run.jsonl
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Set

from .common import Artifact, load


HERMES_TOOL_COUNT_THRESHOLD = 5


@dataclass
class M1Result:
    n_relevant: int                 # total should_be_nominated=True invocations
    echo_positives: Set[int]
    hermes_positives: Set[int]
    relevant: Set[int]              # ground-truth positive set

    @property
    def precision_echo(self) -> float:
        return _precision(self.echo_positives, self.relevant)

    @property
    def recall_echo(self) -> float:
        return _recall(self.echo_positives, self.relevant)

    @property
    def precision_hermes(self) -> float:
        return _precision(self.hermes_positives, self.relevant)

    @property
    def recall_hermes(self) -> float:
        return _recall(self.hermes_positives, self.relevant)

    def to_dict(self) -> Dict[str, float]:
        return {
            "echo_precision": self.precision_echo,
            "echo_recall": self.recall_echo,
            "hermes_precision": self.precision_hermes,
            "hermes_recall": self.recall_hermes,
            "n_relevant": self.n_relevant,
            "n_echo_flagged": len(self.echo_positives),
            "n_hermes_flagged": len(self.hermes_positives),
        }


def _precision(predicted: Set[int], relevant: Set[int]) -> float:
    return len(predicted & relevant) / len(predicted) if predicted else 0.0


def _recall(predicted: Set[int], relevant: Set[int]) -> float:
    return len(predicted & relevant) / len(relevant) if relevant else 0.0


def compute(art: Artifact) -> M1Result:
    relevant: Set[int] = set()
    for gt_inv in art.gt_invocations():
        if gt_inv.get("should_be_nominated"):
            inv_id = art.invocation_id_for(
                session_id=gt_inv["session_id"], skill_id=gt_inv["skill_id"],
            )
            if inv_id is not None:
                relevant.add(inv_id)

    echo_positives = {
        c["invocation_id"] for c in art.m1_candidates if c.get("invocation_id")
    }

    # Hermes' rule: count tool_call signals per invocation; positive if >= 5.
    tool_counts: Dict[int, int] = {}
    for sig in art.signals:
        if sig.get("signal_type") == "tool_call":
            inv_id = sig.get("invocation_id")
            if inv_id is None:
                continue
            tool_counts[inv_id] = tool_counts.get(inv_id, 0) + 1
    hermes_positives = {
        inv_id for inv_id, n in tool_counts.items()
        if n >= HERMES_TOOL_COUNT_THRESHOLD
    }

    return M1Result(
        n_relevant=len(relevant),
        echo_positives=echo_positives,
        hermes_positives=hermes_positives,
        relevant=relevant,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="M1 nomination precision")
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    art = load(args.artifact)
    result = compute(art)

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(f"M1 nomination precision  (n_relevant={result.n_relevant})")
        print(f"  Echo:   P={result.precision_echo:.3f}  R={result.recall_echo:.3f}  "
              f"({len(result.echo_positives)} flagged)")
        print(f"  Hermes: P={result.precision_hermes:.3f}  R={result.recall_hermes:.3f}  "
              f"({len(result.hermes_positives)} flagged)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
