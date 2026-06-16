"""Second third-party benchmark — PrefEval (ICLR 2025).

PrefEval (siyanzhao/prefeval_explicit) pairs a stated user preference with a
question whose *natural* answer violates it. Published finding: preference
following collapses below 10% after ~10 turns for most LLMs. We test whether
Echo's M5 retrieval lets the agent honour the right preference.

Conditions (all on mimo-v2.5):
    no_pref  — agent answers the question with no memory of the preference.
    echo_m5  — Echo's M5 store holds MANY preferences (across topics); it
               retrieves the relevant one for this question and injects it.
    oracle   — the exact paired preference is injected (upper bound).

Metric: adherence rate, judged by the independent GLM-5.2 evaluator (does the
answer respect the stated preference?). Echo's value = does semantic retrieval
surface the right preference among many so the answer adheres, vs a cold model.

Isolated temp DB; config/env from ~/.hermes.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import random
import sys
import tempfile

from scripts.eval import llm_clients as L

DATA = pathlib.Path(__file__).parent / "data" / "prefeval"
RESULTS = pathlib.Path(__file__).parent / "results"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--pool", type=int, default=200, help="preferences loaded into M5")
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args()

    L.load_env()
    RESULTS.mkdir(parents=True, exist_ok=True)
    import pandas as pd
    df = pd.read_parquet(DATA / "explicit.parquet")
    rng = random.Random(args.seed)
    idx = list(range(len(df))); rng.shuffle(idx)

    import hermes_state
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="echo_pe_")) / "state.db"
    hermes_state.DEFAULT_DB_PATH = tmp
    from plugins.echo_signals import db as echo_db
    echo_db.reset_for_tests()
    from plugins.echo_signals import embeddings
    embeddings.install_active_encoder()
    from plugins.echo_signals import preference_rag as prag

    # Populate M5 with a POOL of preferences (across topics) so retrieval must
    # find the right one among many. Each preference keyed by topic.
    pool_idx = idx[: args.pool]
    for i in pool_idx:
        r = df.iloc[i]
        try:
            prag.store_preference(task_request=str(r["question"]),
                                  agent_output=f"User preference: {r['preference']}",
                                  rating=5, skill_id=str(r["topic"]))
        except Exception:
            pass

    eval_idx = pool_idx[: args.limit]   # evaluate on items that ARE in the store
    agent = L.make_agent(); evalr = L.make_evaluator()

    def answer(question, injected):
        sysmsg = ("You are a helpful assistant. If any user preferences are listed, "
                  "honour them in your answer. Answer in 3-5 sentences.")
        ctx = ("\nKnown user preferences:\n" + "\n".join(f"- {x}" for x in injected) + "\n") if injected else ""
        return agent.chat([{"role": "system", "content": sysmsg},
                           {"role": "user", "content": ctx + "\nQuestion: " + question}],
                          max_tokens=300, temperature=0.4)

    def adheres(pref, question, ans) -> int:
        j = evalr.chat_json([{"role": "user", "content":
            "Does the assistant's answer RESPECT the user's stated preference? "
            "Answer strictly.\n\n"
            f"PREFERENCE: {pref}\nQUESTION: {question}\nANSWER: {ans}\n\n"
            'Output ONLY JSON: {"adhere": true or false}'}], max_tokens=2000, temperature=0.0)
        try:
            return int(bool(j["adhere"]))
        except Exception:
            return 0

    fout = open(RESULTS / "prefeval_runs.jsonl", "w")
    score = {"no_pref": 0, "echo_m5": 0, "oracle": 0}
    n = 0
    for c, i in enumerate(eval_idx, 1):
        r = df.iloc[i]
        pref, q, topic = str(r["preference"]), str(r["question"]), str(r["topic"])
        n += 1
        a_no = answer(q, [])
        ex = prag.retrieve_topk(q, k=2, min_similarity=0.2, min_rating=4)
        m5 = [e.agent_output for e in ex]
        a_m5 = answer(q, m5)
        a_or = answer(q, [f"User preference: {pref}"])
        s_no, s_m5, s_or = adheres(pref, q, a_no), adheres(pref, q, a_m5), adheres(pref, q, a_or)
        score["no_pref"] += s_no; score["echo_m5"] += s_m5; score["oracle"] += s_or
        rec = {"topic": topic, "no_pref": s_no, "echo_m5": s_m5, "oracle": s_or,
               "m5_retrieved": len(m5),
               "m5_hit_topic": int(any(topic in (e.skill_id or "") for e in ex))}
        fout.write(json.dumps(rec, ensure_ascii=False) + "\n"); fout.flush()
        if c % 10 == 0:
            print(f"  [{c}/{len(eval_idx)}] adherence={ {k: round(v/n,2) for k,v in score.items()} }")
    fout.close()
    summary = {"n": n, "adherence": {k: round(v / n, 4) for k, v in score.items()},
               "agent_usage": agent.usage.as_dict()}
    (RESULTS / "prefeval_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\n=== PrefEval ===\n", json.dumps(summary["adherence"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
