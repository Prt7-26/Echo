"""Third-party benchmark experiment — PersonaMem (COLM 2025).

PersonaMem (bowen-upenn/PersonaMem, MIT) gives 20 personas, each with a long
multi-session conversation where the user states and *updates* preferences, plus
589 multiple-choice probes that test whether an assistant recalls / applies those
preferences. correct_answer is the benchmark's own ground truth, so this metric
has NO evaluator circularity.

We test Echo's M5 preference-RAG against two references, all on mimo-v2.5:

    no_mem     — agent sees only the probe question + options (cold model).
    full_hist  — naive recency RAG: dump the last K user turns into the prompt.
    echo_m5    — Echo's confidence-weighted neural retrieval picks the relevant
                 preference(s) from the WHOLE history and injects them.

Hypothesis: echo_m5 ≥ full_hist > no_mem in accuracy, and echo_m5 reaches
full_hist-level accuracy at a fraction of the injected tokens (ties into the
overhead metric) — because semantic retrieval finds the relevant preference even
when it was stated far back, which a fixed recency window misses.

Isolated: Echo's tables go to a temp DB (live ~/.hermes/state.db untouched);
config.yaml + .env (Qwen, mimo, neural embeddings) still resolve from ~/.hermes.

Run:
    /Users/mac/.hermes/hermes-agent/venv/bin/python -m scripts.eval.exp_personamem --limit 120
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import random
import re
import sys
import tempfile
import time

from scripts.eval import llm_clients as L

DATA = pathlib.Path(__file__).parent / "data" / "personamem"
RESULTS = pathlib.Path(__file__).parent / "results"

# Question types whose answer depends on the user's stated preferences (these are
# where a preference memory should help). Pure fact-recall types are included too
# but flagged, since retrieval helps there as well.
PREF_TYPES = {
    "provide_preference_aligned_recommendations",
    "generalizing_to_new_scenarios",
    "track_full_preference_evolution",
    "recall_user_shared_facts",
    "recalling_the_reasons_behind_previous_updates",
    "recalling_facts_mentioned_by_the_user",
    "suggest_new_ideas",
}

FULL_HIST_K = 12          # recency window for the naive full_hist baseline
ECHO_TOPK = 4             # M5 retrieval depth


def _setup_isolated_db():
    """Point Echo's DB at a temp file; keep config/env from ~/.hermes."""
    import hermes_state
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="echo_pm_")) / "state.db"
    hermes_state.DEFAULT_DB_PATH = tmp
    from plugins.echo_signals import db as echo_db
    echo_db.reset_for_tests()
    # Install the configured (neural) encoder so M5 stores/retrieves at the right dim.
    from plugins.echo_signals import embeddings
    embeddings.install_active_encoder()
    return tmp


def _load():
    import pandas as pd
    df = pd.read_csv(DATA / "questions_32k.csv")
    contexts = {}
    with open(DATA / "shared_contexts_32k.jsonl") as f:
        for line in f:
            obj = json.loads(line)
            contexts.update(obj)
    return df, contexts


def _user_turns(messages: list[dict]) -> list[str]:
    """All user-authored turns in a persona's conversation history."""
    out = []
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str) and c.strip():
                out.append(c.strip())
    return out


def _persona_desc(messages: list[dict]) -> str:
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "system":
            return str(m.get("content", ""))[:600]
    return ""


_LETTER = re.compile(r"\(?([a-dA-D])\)?")


def _parse_letter(text: str) -> str:
    t = (text or "").strip()
    m = _LETTER.match(t)
    if m:
        return m.group(1).lower()
    m = _LETTER.search(t)
    return m.group(1).lower() if m else ""


def _options_str(all_options) -> str:
    try:
        opts = json.loads(all_options)
        if isinstance(opts, list):
            return "\n".join(str(o) for o in opts)
    except Exception:
        pass
    return str(all_options)


def _answer(agent, question, options_str, injected: str) -> tuple[str, int]:
    """Ask the agent to pick a letter. Returns (letter, injected_char_len)."""
    sys_msg = (
        "You are a helpful assistant continuing a conversation with a user. "
        "Answer the multiple-choice question by selecting the option that best "
        "fits THIS user. Respond with ONLY the letter: a, b, c, or d."
    )
    ctx = f"\n\nWhat you know about this user:\n{injected}\n" if injected else ""
    user_msg = f"{ctx}\nUser says: {question}\n\nOptions:\n{options_str}\n\nAnswer (one letter):"
    out = agent.chat(
        [{"role": "system", "content": sys_msg},
         {"role": "user", "content": user_msg}],
        max_tokens=64, temperature=0.0,
    )
    return _parse_letter(out), len(injected)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=120, help="probes to evaluate")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--personas", type=int, default=20, help="max personas to use")
    args = ap.parse_args()

    L.load_env()
    RESULTS.mkdir(parents=True, exist_ok=True)
    db = _setup_isolated_db()
    print(f"[setup] isolated Echo DB at {db}")

    df, contexts = _load()
    from plugins.echo_signals import preference_rag as prag
    from plugins.echo_signals import embeddings

    # Build a quick map persona_id -> its messages (via any probe's shared_context_id).
    persona_ctx = {}
    for _, r in df.iterrows():
        pid = r["persona_id"]
        if pid not in persona_ctx:
            cid = r["shared_context_id"]
            if cid in contexts:
                persona_ctx[pid] = contexts[cid]
    persona_ids = list(persona_ctx)[: args.personas]
    print(f"[data] {len(persona_ids)} personas, {len(df)} probes total")

    # Subsample probes (balanced-ish across personas), preference-dependent types.
    pool = df[df.persona_id.isin(persona_ids) & df.question_type.isin(PREF_TYPES)].copy()
    rng = random.Random(args.seed)
    idx = list(pool.index)
    rng.shuffle(idx)
    idx = idx[: args.limit]
    # Group selected probes by persona so we can scope the M5 store per persona
    # (retrieve_topk searches the whole corpus, so we keep only one persona's
    # preferences loaded at a time — no cross-persona contamination).
    by_persona: dict = {}
    for ix in idx:
        by_persona.setdefault(pool.loc[ix, "persona_id"], []).append(ix)
    print(f"[probes] evaluating {len(idx)} probes across {len(by_persona)} personas")

    agent = L.make_agent()
    rows = []
    run_path = RESULTS / "personamem_runs.jsonl"
    fout = open(run_path, "w")
    correct = {"no_mem": 0, "full_hist": 0, "echo_m5": 0}
    inj_chars = {"no_mem": 0, "full_hist": 0, "echo_m5": 0}
    n = 0
    done = 0
    for pid, probe_idxs in by_persona.items():
        # Reset the M5 corpus, then load only THIS persona's user turns.
        embeddings.clear_embedding_corpus()
        turns = _user_turns(persona_ctx[pid])
        for t in turns:
            try:
                prag.store_preference(task_request=t, agent_output="(noted)",
                                      rating=4, skill_id=str(pid))
            except Exception:
                pass

        for ix in probe_idxs:
            r = pool.loc[ix]
            q = str(r["user_question_or_message"])
            opts = _options_str(r["all_options"])
            gold = _parse_letter(str(r["correct_answer"]))
            done += 1
            if not gold:
                continue
            n += 1

            a_no, _ = _answer(agent, q, opts, "")

            hist = "\n".join(f"- {t}" for t in turns[-FULL_HIST_K:])
            a_fh, fh_len = _answer(agent, q, opts, hist)

            try:
                ex = prag.retrieve_topk(q, k=ECHO_TOPK, min_similarity=0.2, min_rating=4)
                m5 = "\n".join(f"- {e.task_request}" for e in ex) if ex else ""
            except Exception:
                m5 = ""
            a_m5, m5_len = _answer(agent, q, opts, m5)

            for cond, ans, ln in (("no_mem", a_no, 0), ("full_hist", a_fh, fh_len),
                                  ("echo_m5", a_m5, m5_len)):
                correct[cond] += int(ans == gold)
                inj_chars[cond] += ln
            rec = {"persona_id": str(pid), "type": r["question_type"], "topic": r["topic"],
                   "gold": gold, "no_mem": a_no, "full_hist": a_fh, "echo_m5": a_m5,
                   "m5_inject_chars": m5_len, "fh_inject_chars": fh_len,
                   "m5_retrieved": len(ex) if 'ex' in dir() and isinstance(ex, list) else 0}
            rows.append(rec)
            fout.write(json.dumps(rec, ensure_ascii=False) + "\n"); fout.flush()
            if done % 10 == 0:
                acc = {k: f"{v/max(n,1):.2f}" for k, v in correct.items()}
                print(f"  [{done}/{len(idx)}] acc={acc}")
    fout.close()

    summary = {
        "n_probes": n,
        "accuracy": {k: round(v / n, 4) for k, v in correct.items()},
        "avg_inject_chars": {k: round(v / n, 1) for k, v in inj_chars.items()},
        "by_type": {},
    }
    # per-type accuracy
    import collections
    tot = collections.Counter()
    hit = {c: collections.Counter() for c in correct}
    for rec in rows:
        tot[rec["type"]] += 1
        for c in correct:
            hit[c][rec["type"]] += int(rec[c] == rec["gold"])
    for t in tot:
        summary["by_type"][t] = {"n": tot[t],
                                 **{c: round(hit[c][t] / tot[t], 3) for c in correct}}
    summary["agent_usage"] = agent.usage.as_dict()
    (RESULTS / "personamem_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2))
    print("\n=== PersonaMem result ===")
    print(json.dumps(summary["accuracy"], indent=2))
    print("avg injected chars:", summary["avg_inject_chars"])
    print(f"saved -> {RESULTS/'personamem_summary.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
