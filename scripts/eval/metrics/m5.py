"""M5 metric: preference-retrieval uplift.

We can't grade a real-agent answer without a real agent, so M5 is
proxied as ``retrieval recall@k``: given a planted library of
preference examples and a set of queries with planted relevant-example
IDs, what fraction of the relevant examples appear in the top-k
results?

Two configurations are compared:
  * Echo with confidence-weighting (full system).
  * Echo without confidence-weighting (signals-only ablation).

The difference is the "uplift" the confidence engine adds to M5.

This script is self-contained — it does NOT consume the simulator
artifact. It seeds its own isolated Hermes home and Echo DB so the
result is reproducible from a clean slate.

Run:
    python -m scripts.eval.metrics.m5
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# Planted preference library. (skill_id, task_request, agent_output, rating)
PLANTED_LIBRARY: List[Tuple[str, str, str, int]] = [
    # marketing-email: 3 entries (relevant skill)
    ("marketing-email", "write a marketing email for our new product",
     "Subject: Introducing X — body...", 5),
    ("marketing-email", "draft a promotional email about our launch",
     "Subject: Big news — ...", 5),
    ("marketing-email", "compose a marketing email for our spring sale",
     "Subject: Spring sale ...", 5),
    # code-refactor: 3 entries (different domain)
    ("code-refactor", "refactor this function to use list comprehensions",
     "Refactored code: ...", 5),
    ("code-refactor", "rewrite this loop more pythonically",
     "Pythonic version: ...", 5),
    ("code-refactor", "clean up this nested if-else",
     "Cleaner version: ...", 5),
    # data-analysis: 2 entries
    ("data-analysis", "summarise the trends in this csv",
     "Top trends: ...", 5),
    ("data-analysis", "give me the highest correlations in this dataframe",
     "Top correlations: ...", 5),
]


# Planted queries. Each row: (query_text, list of *positions in the library*
# above that are "relevant"). Positions index into PLANTED_LIBRARY by order
# of insertion.
PLANTED_QUERIES: List[Tuple[str, List[int]]] = [
    ("write a marketing email for our autumn sale", [0, 1, 2]),
    ("refactor this code to be cleaner", [3, 4, 5]),
    ("summarise correlations in this csv", [6, 7]),
]


# Per-skill confidence used in the full-Echo configuration. Skills the
# user found useful keep high confidence; the unused 'noise' skill is
# downranked so its examples should appear less often in top-k.
PLANTED_CONFIDENCE: Dict[str, float] = {
    "marketing-email": 0.9,
    "code-refactor": 0.9,
    "data-analysis": 0.9,
}


K_DEFAULT = 3


@dataclass
class M5Result:
    recall_with_weights: float
    recall_no_weights: float

    @property
    def uplift(self) -> float:
        return self.recall_with_weights - self.recall_no_weights

    def to_dict(self) -> Dict[str, float]:
        return {
            "recall_with_confidence_weights": self.recall_with_weights,
            "recall_no_weights": self.recall_no_weights,
            "uplift": self.uplift,
        }


def _seed(home: Path) -> List[int]:
    """Open Echo DB at `home/sessions.db`, seed it, return example_ids."""
    import hermes_state
    from plugins.echo_signals import db as echo_db, preference_rag

    hermes_state.DEFAULT_DB_PATH = home / "sessions.db"
    echo_db.reset_for_tests()

    example_ids: List[int] = []
    conn = echo_db.get_echo_conn()

    # Seed the confidence anchors first so the FK from preference_example
    # / signal_event is satisfied (preference_example has no FK to
    # confidence but we still update the row below for the weighting).
    import time
    now = time.time()
    for skill_id, c in PLANTED_CONFIDENCE.items():
        conn.execute(
            "INSERT OR IGNORE INTO echo_skill_confidence "
            "(skill_id, created_at, updated_at) VALUES (?, ?, ?)",
            (skill_id, now, now),
        )
        conn.execute(
            "UPDATE echo_skill_confidence SET confidence = ? WHERE skill_id = ?",
            (c, skill_id),
        )
    conn.commit()

    for skill_id, request, output, rating in PLANTED_LIBRARY:
        ex_id = preference_rag.store_preference(
            task_request=request,
            agent_output=output,
            rating=rating,
            skill_id=skill_id,
        )
        example_ids.append(ex_id)
    return example_ids


def _retrieve(query: str, k: int, weights: Optional[Dict[str, float]]):
    from plugins.echo_signals import preference_rag
    return preference_rag.retrieve_topk(query, k=k, confidence_weights=weights)


def compute(*, k: int = K_DEFAULT, home: Optional[Path] = None) -> M5Result:
    owns_home = home is None
    home = home or Path(tempfile.mkdtemp(prefix="echo-m5-"))
    prev_home = os.environ.get("HERMES_HOME")
    os.environ["HERMES_HOME"] = str(home)
    try:
        example_ids = _seed(home)
        # The planted indices [0..7] need to be mapped to actual example_ids.
        idx_to_id = {i: example_ids[i] for i in range(len(example_ids))}

        recall_hits_w, recall_total_w = 0, 0
        recall_hits_nw, recall_total_nw = 0, 0

        for query, relevant_idxs in PLANTED_QUERIES:
            relevant_ids = {idx_to_id[i] for i in relevant_idxs}
            recall_total_w += len(relevant_ids)
            recall_total_nw += len(relevant_ids)

            top_with = _retrieve(query, k, PLANTED_CONFIDENCE)
            top_no = _retrieve(query, k, None)

            recall_hits_w += sum(1 for ex in top_with if ex.example_id in relevant_ids)
            recall_hits_nw += sum(1 for ex in top_no if ex.example_id in relevant_ids)

        recall_w = recall_hits_w / recall_total_w if recall_total_w else 0.0
        recall_nw = recall_hits_nw / recall_total_nw if recall_total_nw else 0.0
        return M5Result(recall_with_weights=recall_w, recall_no_weights=recall_nw)
    finally:
        from plugins.echo_signals import db as echo_db
        echo_db.reset_for_tests()
        if prev_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = prev_home
        if owns_home:
            import shutil
            shutil.rmtree(home, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="M5 retrieval uplift")
    parser.add_argument("--k", type=int, default=K_DEFAULT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = compute(k=args.k)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(f"M5 retrieval recall@{args.k}")
        print(f"  with confidence weights: {result.recall_with_weights:.3f}")
        print(f"  no weights:              {result.recall_no_weights:.3f}")
        print(f"  uplift:                  {result.uplift:+.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
