"""Shared helpers for the four metric scripts.

The metric scripts read a Harness artifact (JSON Lines) and compute one
number each. This module centralises the file parsing + a few shared
data accessors so each metric script can stay focused on its own logic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class Artifact:
    """Lightweight in-memory view of a Harness JSONL artifact."""

    config: Dict[str, Any] = field(default_factory=dict)
    ground_truth: List[Dict[str, Any]] = field(default_factory=list)
    invocations: List[Dict[str, Any]] = field(default_factory=list)
    signals: List[Dict[str, Any]] = field(default_factory=list)
    confidence: List[Dict[str, Any]] = field(default_factory=list)
    m1_candidates: List[Dict[str, Any]] = field(default_factory=list)
    drifts: List[Dict[str, Any]] = field(default_factory=list)

    def gt_invocations(self) -> List[Dict[str, Any]]:
        """Flatten every per-invocation ground-truth row across scenarios."""
        out: List[Dict[str, Any]] = []
        for gt in self.ground_truth:
            out.extend(gt.get("invocations", []))
        return out

    def invocation_id_for(self, *, session_id: str, skill_id: str
                          ) -> Optional[int]:
        """Map a ground-truth (session_id, skill_id) to its runtime row."""
        for inv in self.invocations:
            if inv.get("session_id") == session_id and inv.get("skill_id") == skill_id:
                return inv.get("invocation_id")
        return None

    def skill_true_usefulness(self) -> Dict[str, float]:
        """Merge all scenarios' planted true-usefulness scores."""
        merged: Dict[str, float] = {}
        for gt in self.ground_truth:
            merged.update(gt.get("skill_true_usefulness", {}))
        return merged


def load(path: str | Path) -> Artifact:
    """Read a JSONL artifact into an `Artifact`."""
    p = Path(path)
    art = Artifact()
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            kind = row.get("kind")
            if kind == "config":
                art.config = row
            elif kind == "ground_truth":
                art.ground_truth.append(row)
            elif kind == "invocation":
                art.invocations.append(row)
            elif kind == "signal":
                art.signals.append(row)
            elif kind == "confidence":
                art.confidence.append(row)
            elif kind == "m1_candidate":
                art.m1_candidates.append(row)
            elif kind == "drift":
                art.drifts.append(row)
    return art


# ---------------------------------------------------------------------
# Pure-Python rank correlation (avoids a scipy dependency).
# ---------------------------------------------------------------------


def _rank(xs: List[float]) -> List[float]:
    """Average ranks; ties get the mean of their slot indices (1-based)."""
    indexed = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(indexed):
        j = i
        while j + 1 < len(indexed) and xs[indexed[j + 1]] == xs[indexed[i]]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0  # 1-based midpoint
        for k in range(i, j + 1):
            ranks[indexed[k]] = avg_rank
        i = j + 1
    return ranks


def spearman_rho(xs: List[float], ys: List[float]) -> float:
    """Spearman's rank correlation. Returns 0.0 when degenerate (n<2 or
    one of the columns is constant)."""
    n = len(xs)
    if n < 2 or n != len(ys):
        return 0.0
    rx, ry = _rank(xs), _rank(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx2 = sum((a - mx) ** 2 for a in rx)
    dy2 = sum((b - my) ** 2 for b in ry)
    if dx2 == 0 or dy2 == 0:
        return 0.0
    return num / ((dx2 ** 0.5) * (dy2 ** 0.5))
