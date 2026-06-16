"""Closed-loop longitudinal experiment — the centerpiece.

A DeepSeek persona converses with a mimo agent over many turns of recurring
tasks. After each output the persona reacts (rating + NL feedback + thumb +
maybe a revision), and an INDEPENDENT GLM-5.2 evaluator scores the output 1-5
against the persona's ground-truth preference rubric. Four-model isolation:

    persona  = DeepSeek v4 flash   (produces requests + reactions)
    agent    = mimo-v2.5           (the system under test)
    signals  = Qwen qwen-plus      (Echo's Layer B/C, via the plugin)
    evaluator= GLM-5.2             (the metric — never sees Echo, never the persona's grade)

Three conditions:
    A (none)  : plain mimo, no memory, no personalization.
    B (selfeval): mimo + a self-evaluation-gated template memory with frequency/
                  recency decay and NO user signal (the Hermes / agentmemory
                  behaviour the proposal targets).
    echo      : mimo + Echo's real M5 preference RAG (confidence-weighted) +
                M4 confidence lifecycle driven by the persona's signals (Qwen).

Outputs feed three proposal metrics:
    Metric 1  satisfaction curve   — GLM score vs turn index, per condition.
    Metric 2  error propagation    — a planted bad approach is pre-seeded; we
                                     track how many turns it keeps being used.
    Metric 3  system overhead      — agent + Echo-signal tokens per condition.

Live Echo DB untouched (isolated temp DB); config/env still from ~/.hermes.
Everything checkpoints per turn so a crash leaves usable partial data.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import random
import sys
import tempfile
import time

from scripts.eval import llm_clients as L
from scripts.eval.personas import Persona, get_personas

RESULTS = pathlib.Path(__file__).parent / "results"

C_RETIRE = 0.10   # mirrors confidence.C_RETIRE


# ---------------------------------------------------------------------------
# LLM-mediated steps
# ---------------------------------------------------------------------------

def agent_output(agent, request: str, injected: list[str]) -> str:
    sys_msg = ("You are a writing assistant for a specific user whose preferences "
               "you have learned. Any learned preferences below are HARD "
               "REQUIREMENTS that override your defaults — obey every one exactly, "
               "and reproduce any required exact phrase or sign-off verbatim. "
               "Produce only the requested text, nothing else.")
    ctx = ""
    if injected:
        items = "\n".join(f"- {e}" for e in injected)
        ctx = (f"\nHARD REQUIREMENTS from this user (obey every one exactly):\n{items}\n")
    msg = f"{ctx}\nRequest: {request}"
    return agent.chat([{"role": "system", "content": sys_msg},
                       {"role": "user", "content": msg}], max_tokens=400, temperature=0.4)


def persona_react(persona_cli, persona: Persona, request: str, output: str) -> dict:
    prompt = (
        f"{persona.persona_brief}\n\n"
        f"You asked: {request}\n\nThe assistant produced:\n---\n{output}\n---\n\n"
        "React AS THIS PERSON. Output ONLY JSON:\n"
        '{"satisfaction": <1-5 int>, "thumb": <1 or -1>, "revise": <true/false>, '
        '"feedback": "<a clear DIRECTIVE telling the assistant exactly what you '
        'require next time, imperative and specific, e.g. \'Always end with X; keep '
        'under N words; never use Y\'>"}\n'
        "thumb=1 only if you are genuinely happy; revise=true if you would ask for changes."
    )
    j = persona_cli.chat_json([{"role": "user", "content": prompt}],
                              max_tokens=400, temperature=0.5)
    if not j:
        return {"satisfaction": 3, "thumb": -1, "revise": True, "feedback": "not quite what I wanted"}
    j.setdefault("satisfaction", 3); j.setdefault("thumb", -1)
    j.setdefault("revise", False); j.setdefault("feedback", "")
    try:
        j["satisfaction"] = max(1, min(5, int(j["satisfaction"])))
        j["thumb"] = 1 if int(j["thumb"]) > 0 else -1
    except Exception:
        j["satisfaction"], j["thumb"] = 3, -1
    return j


def evaluate(evaluator, persona: Persona, request: str, output: str) -> int:
    prompt = (
        "You are a strict, neutral evaluator. Score how well the TEXT satisfies "
        "the USER'S PREFERENCE RULES below, 1 (violates) to 5 (fully satisfies). "
        "Judge ONLY by the rules, not your own taste.\n\n"
        f"PREFERENCE RULES:\n{persona.pref_rules}\n\n"
        f"REQUEST: {request}\n\nTEXT:\n---\n{output}\n---\n\n"
        'Output ONLY JSON: {"score": <1-5 int>}'
    )
    j = evaluator.chat_json([{"role": "user", "content": prompt}],
                            max_tokens=2000, temperature=0.0)
    try:
        return max(1, min(5, int(j["score"])))
    except Exception:
        return 3


# ---------------------------------------------------------------------------
# Baseline B — self-eval gated template memory with frequency/recency decay
# ---------------------------------------------------------------------------

class BaselineBMemory:
    """Stores agent outputs its OWN self-eval deems 'successful' (no user signal).
    Decay is frequency/recency-based, like agentmemory / Hermes."""

    def __init__(self, agent):
        self.agent = agent
        self.store: dict[str, list[dict]] = {}   # task_type -> [{text, score}]

    def self_eval_success(self, request: str, output: str) -> bool:
        # The documented flaw: same-source self-eval almost always says success.
        j = self.agent.chat_json(
            [{"role": "user", "content":
              f"You wrote this in response to '{request}':\n{output}\n\n"
              'Did you do a good job? Output ONLY JSON: {"success": true/false}'}],
            max_tokens=200, temperature=0.0)
        return bool(j.get("success", True)) if j else True

    def retrieve(self, task_type: str) -> list[str]:
        items = self.store.get(task_type, [])
        items.sort(key=lambda d: d["score"], reverse=True)
        return [d["text"] for d in items[:2]]

    def update(self, task_type: str, request: str, output: str) -> None:
        # recency/frequency decay on everything
        for items in self.store.values():
            for d in items:
                d["score"] *= 0.9
        if self.self_eval_success(request, output):
            self.store.setdefault(task_type, []).append({"text": output, "score": 1.0})


# ---------------------------------------------------------------------------
# Echo driver — real plugin
# ---------------------------------------------------------------------------

class EchoDriver:
    def __init__(self):
        from plugins.echo_signals import db as echo_db
        from plugins.echo_signals import preference_rag as prag
        from plugins.echo_signals import confidence as conf
        from plugins.echo_signals import nl_classifier
        from plugins.echo_signals import confidence_actions as ca
        from plugins.echo_signals import session_context as sc
        self.echo_db, self.prag, self.conf = echo_db, prag, conf
        self.nl, self.ca, self.sc = nl_classifier, ca, sc
        self.conn = echo_db.get_echo_conn()

    def anchor(self, skill_id: str, confidence: float = 0.5):
        now = time.time()
        self.conn.execute(
            "INSERT OR IGNORE INTO echo_skill_confidence (skill_id, confidence, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)", (skill_id, confidence, now, now))
        self.conn.execute("UPDATE echo_skill_confidence SET confidence=? WHERE skill_id=?",
                          (confidence, skill_id))
        self.conn.commit()

    def confidences(self, skill_ids: list[str]) -> dict[str, float]:
        out = {}
        for s in skill_ids:
            r = self.conn.execute("SELECT confidence FROM echo_skill_confidence WHERE skill_id=?",
                                  (s,)).fetchone()
            if r:
                out[s] = float(r["confidence"])
        return out

    def status(self, skill_id: str) -> tuple[float, str]:
        r = self.conn.execute("SELECT confidence,status FROM echo_skill_confidence WHERE skill_id=?",
                              (skill_id,)).fetchone()
        return (float(r["confidence"]), r["status"]) if r else (0.0, "missing")

    def retrieve(self, request: str, skill_ids: list[str]) -> list:
        cw = self.confidences(skill_ids)
        try:
            return self.prag.retrieve_topk(request, k=2, min_similarity=0.15,
                                           min_rating=4, confidence_weights=cw)
        except Exception:
            return []

    def store_good(self, request: str, output: str, skill_id: str):
        try:
            self.prag.store_preference(task_request=request, agent_output=output,
                                       rating=5, skill_id=skill_id)
        except Exception:
            pass

    def feedback(self, skill_id: str, thumb: int, feedback_text: str, revise_rounds: int):
        """Drive Echo's real signal pipeline for one turn's feedback."""
        # explicit thumb (Layer B)
        ev = "explicit_positive" if thumb > 0 else "explicit_negative"
        try:
            self.ca.apply_signal_event(skill_id, ev)
        except Exception:
            pass
        # NL sentiment (Layer B, Qwen) on the persona's typed feedback
        try:
            label = self.nl.classify(feedback_text)
            if label == "positive":
                self.ca.apply_signal_event(skill_id, "nl_positive")
            elif label == "negative":
                self.ca.apply_signal_event(skill_id, "nl_negative")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# One run of one condition for one persona+seed
# ---------------------------------------------------------------------------

def run_condition(condition: str, persona: Persona, seed: int, n_turns: int,
                  clients: dict, fout) -> list[dict]:
    rng = random.Random(hash((condition, persona.pid, seed)) & 0xffffffff)
    agent = clients["agent"]; persona_cli = clients["persona"]; evalr = clients["eval"]

    # task stream: cycle through this persona's task types, varied topics
    stream = []
    for t in range(n_turns):
        tt = persona.task_types[t % len(persona.task_types)]
        topic = tt.topics[(t // len(persona.task_types)) % len(tt.topics)]
        stream.append((tt.key, tt.template.format(topic=topic)))

    bad_key, bad_text = persona.bad_skill

    echo = bmem = None
    if condition == "echo":
        echo = EchoDriver()
        # seed the planted WRONG remembered preference as a high-confidence skill.
        # task_request is a representative request of that task type so it actually
        # gets retrieved for similar requests (cosine match).
        if bad_key:
            echo.anchor(f"bad_{bad_key}", 0.7)
            rep = next((tt.template.format(topic=tt.topics[0])
                        for tt in persona.task_types if tt.key == bad_key), bad_key)
            echo.store_good(rep, bad_text, f"bad_{bad_key}")
        for tt in persona.task_types:
            echo.anchor(f"good_{tt.key}", 0.5)
    elif condition == "B":
        bmem = BaselineBMemory(agent)
        if bad_key:
            bmem.store.setdefault(bad_key, []).append({"text": bad_text, "score": 5.0})

    records = []
    for i, (tkey, request) in enumerate(stream):
        injected = []
        used_bad = False
        dominant_skill = f"good_{tkey}"

        if condition == "echo":
            skill_ids = [f"good_{tt.key}" for tt in persona.task_types]
            if bad_key:
                skill_ids.append(f"bad_{bad_key}")
            ex = echo.retrieve(request, skill_ids)
            injected = [e.agent_output for e in ex]
            if ex:
                dominant_skill = ex[0].skill_id or dominant_skill
                used_bad = (ex[0].skill_id or "").startswith("bad_")
        elif condition == "B":
            injected = bmem.retrieve(tkey)
            used_bad = bool(bad_key == tkey and bmem.store.get(tkey) and
                            bmem.store[tkey][0].get("text") == bad_text and injected
                            and injected[0] == bad_text)

        # METRIC: the FIRST output, before any revision — did the agent
        # proactively satisfy this user's known preferences?
        first_out = agent_output(agent, request, injected)
        score = evaluate(evalr, persona, request, first_out)
        react = persona_react(persona_cli, persona, request, first_out)

        # A revision is how the preference gets *communicated* (the persona's
        # feedback names the unmet rule). The satisfying revision is what memory
        # learns — but it does NOT count toward the proactive-satisfaction metric.
        final_out, final_react, rounds = first_out, react, 1
        if react.get("revise") and condition != "A":
            fb = react.get("feedback", "")
            out2 = agent_output(
                agent, request + f"\n\nThe user was not satisfied: \"{fb}\". "
                f"Revise so it fully satisfies them.", injected)
            r2 = persona_react(persona_cli, persona, request, out2)
            rounds = 2
            if r2["satisfaction"] >= react["satisfaction"]:
                final_out, final_react = out2, r2

        bad_conf = None
        if condition == "echo":
            from plugins.echo_signals import usage_hook
            from plugins.echo_signals.signals import record_signal
            echo.sc.set_session_context(f"{persona.pid}-{seed}", "cli")
            # Attribute the FIRST-output reaction to the skill that produced it
            # (the top-injected example) — so a bad injected example is what gets
            # penalised, and drift accrues against it.
            inv_skill = dominant_skill
            usage_hook._record_invocation(inv_skill)
            inv = echo.sc.get_current_invocation_id()
            for _ in range(rounds):     # modification rounds -> Layer A signal
                record_signal(invocation_id=inv, layer="A", signal_type="user_turn")
            echo.feedback(inv_skill, react["thumb"], react.get("feedback", ""), rounds)
            # Learn from the USER'S SIGNAL: when the first output missed, the
            # persona's feedback states the unmet preference — store THAT (the
            # user's words) so it can be injected on similar future tasks. This is
            # the user-signal-driven core; Baseline B never does it.
            fb = react.get("feedback", "").strip()
            if react["thumb"] < 0 and fb:
                echo.store_good(request, f"User preference: {fb}", f"good_{tkey}")
            try:
                from plugins.echo_signals.baseline import finalize_invocation
                finalize_invocation(inv)
            except Exception:
                pass
            if bad_key:
                bad_conf, _ = echo.status(f"bad_{bad_key}")
        elif condition == "B":
            # Self-eval gate on the FIRST output, ignoring the user's verdict
            # (the documented same-source bias) — so it banks generic first
            # attempts and never learns the user's actual quirks.
            bmem.update(tkey, request, first_out)

        rec = {"condition": condition, "persona": persona.pid, "seed": seed,
               "turn": i, "task": tkey, "is_bad_task": int(tkey == bad_key),
               "used_bad": int(used_bad), "rounds": rounds,
               "eval_score": score, "persona_sat": react["satisfaction"],
               "first_thumb": react["thumb"], "final_thumb": final_react["thumb"],
               "bad_conf": bad_conf}
        records.append(rec)
        fout.write(json.dumps(rec, ensure_ascii=False) + "\n"); fout.flush()
    return records


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", type=int, default=12)
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--conditions", default="A,B,echo")
    ap.add_argument("--personas", default="")  # comma pids, empty=all
    args = ap.parse_args()

    L.load_env()
    RESULTS.mkdir(parents=True, exist_ok=True)
    # isolated Echo DB
    import hermes_state
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="echo_cl_")) / "state.db"
    hermes_state.DEFAULT_DB_PATH = tmp
    from plugins.echo_signals import db as echo_db
    echo_db.reset_for_tests()
    from plugins.echo_signals import embeddings
    embeddings.install_active_encoder()
    print(f"[setup] isolated Echo DB at {tmp}")

    # Metric 3 instrumentation: wrap Hermes' auxiliary client so we can count the
    # REAL token cost of Echo's Qwen signal calls (Layer B classify, Layer C
    # judge, reason scoring). The plugin imports call_llm at call time, so
    # patching the module attribute is picked up.
    import agent.auxiliary_client as aux
    _orig_call = aux.call_llm
    SIG = {"prompt": 0, "completion": 0, "calls": 0}

    def _wrapped_call(*a, **k):
        r = _orig_call(*a, **k)
        try:
            u = r.usage
            SIG["prompt"] += int(u.prompt_tokens or 0)
            SIG["completion"] += int(u.completion_tokens or 0)
            SIG["calls"] += 1
        except Exception:
            pass
        return r
    aux.call_llm = _wrapped_call

    clients = {"agent": L.make_agent(), "persona": L.make_persona(), "eval": L.make_evaluator()}
    personas = get_personas()
    if args.personas:
        want = set(args.personas.split(","))
        personas = [p for p in personas if p.pid in want]
    conditions = args.conditions.split(",")

    fout = open(RESULTS / "closedloop_runs.jsonl", "w")
    per_run_usage = []
    t0 = time.time()
    total = len(conditions) * len(personas) * args.seeds
    done = 0
    for persona in personas:
        for seed in range(args.seeds):
            for cond in conditions:
                done += 1
                print(f"[{done}/{total}] {persona.pid} seed={seed} cond={cond} "
                      f"({time.time()-t0:.0f}s elapsed)")
                # reset Echo DB per (persona,seed,cond) so confidence/M5 start clean
                echo_db.reset_for_tests()
                embeddings.install_active_encoder()
                # snapshot token counters for per-condition Metric 3
                a0 = dict(clients["agent"].usage.as_dict()); s0 = dict(SIG)
                try:
                    run_condition(cond, persona, seed, args.turns, clients, fout)
                except Exception as e:
                    print(f"   !! run failed: {type(e).__name__}: {e}")
                a1 = clients["agent"].usage.as_dict()
                per_run_usage.append({
                    "condition": cond, "persona": persona.pid, "seed": seed,
                    "turns": args.turns,
                    "agent_tokens": a1["total_tokens"] - a0["total_tokens"],
                    "agent_calls": a1["calls"] - a0["calls"],
                    "signal_tokens": (SIG["prompt"] + SIG["completion"]) - (s0["prompt"] + s0["completion"]),
                    "signal_calls": SIG["calls"] - s0["calls"],
                })
    fout.close()
    usage = {k: v.usage.as_dict() for k, v in clients.items()}
    (RESULTS / "closedloop_usage.json").write_text(json.dumps(
        {"totals": usage, "per_run": per_run_usage}, indent=2, ensure_ascii=False))
    print(f"\nDONE in {time.time()-t0:.0f}s. usage -> {usage}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
