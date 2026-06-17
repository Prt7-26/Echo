"""Analyse all Echo experiment artifacts → figures + stats.json.

Consumes:
  results/personamem_summary.json         (third-party benchmark)
  results/closedloop_runs.jsonl           (Metric 1 + 2)
  results/closedloop_usage.json           (Metric 3)
  results/micrometrics.json               (deterministic M1/M3/M4/M5 + Metric 2)

Produces results/figures/*.png and results/stats.json.
"""
from __future__ import annotations

import json
import pathlib
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

RES = pathlib.Path(__file__).parent / "results"
FIG = RES / "figures"
FIG.mkdir(parents=True, exist_ok=True)

TEAL = "#0fb5ae"; ROSE = "#e0556e"; GREY = "#9aa0a6"; AMBER = "#e8a13a"
plt.rcParams.update({"figure.dpi": 130, "font.size": 11, "axes.grid": True,
                     "grid.alpha": 0.25, "axes.spines.top": False, "axes.spines.right": False})


def _load_json(p):
    p = RES / p
    return json.loads(p.read_text()) if p.exists() else None


def _load_jsonl(p):
    p = RES / p
    return [json.loads(l) for l in p.open()] if p.exists() else []


def cliffs_delta(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if not len(a) or not len(b):
        return 0.0
    gt = sum((x > b).sum() for x in a); lt = sum((x < b).sum() for x in a)
    return (gt - lt) / (len(a) * len(b))


def _load_shards(prefix):
    """Load all seed-shards <prefix>_s*.json; fall back to <prefix>.json."""
    shards = []
    for p in sorted(RES.glob(f"{prefix}_s*.json")):
        try:
            shards.append(json.loads(p.read_text()))
        except Exception:
            pass
    if not shards:
        single = RES / f"{prefix}.json"
        if single.exists():
            shards = [json.loads(single.read_text())]
    return shards


# ---------------------------------------------------------------- PersonaMem
def fig_personamem(stats):
    shards = _load_shards("personamem_summary")
    if not shards:
        return
    conds = ["no_mem", "full_hist", "echo_m5"]
    labels = ["No memory\n(cold)", "Full history\n(naive RAG)", "Echo M5\n(retrieval)"]
    # mean ± std across seed-shards
    means = {c: float(np.mean([sh["accuracy"][c] for sh in shards])) for c in conds}
    sds = {c: float(np.std([sh["accuracy"][c] for sh in shards])) for c in conds}
    inj = {c: float(np.mean([sh["avg_inject_chars"][c] for sh in shards])) for c in conds}
    n_total = int(sum(sh["n_probes"] for sh in shards))
    vals = [means[c] for c in conds]; errs = [sds[c] for c in conds]
    fig, ax = plt.subplots(figsize=(6.2, 4.2))
    bars = ax.bar(labels, vals, yerr=errs, capsize=5, color=[GREY, AMBER, TEAL])
    for b, c in zip(bars, conds):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + errs[conds.index(c)] + 0.015,
                f"{means[c]*100:.1f}%", ha="center", fontweight="bold")
        if inj[c]:
            ax.text(b.get_x() + b.get_width() / 2, 0.03,
                    f"{int(inj[c])} chars\ninjected", ha="center", color="white", fontsize=8)
    ax.set_ylim(0, max(vals) * 1.3); ax.set_ylabel("Preference-probe accuracy")
    ax.set_title(f"PersonaMem (COLM'25): preference recall\n"
                 f"n={n_total} probes over {len(shards)} seeds (error bars = ±1 SD across seeds)")
    fig.tight_layout(); fig.savefig(FIG / "personamem_accuracy.png"); plt.close(fig)
    stats["personamem"] = {"accuracy_mean": means, "accuracy_sd": sds,
                           "avg_inject_chars": inj, "n_total": n_total, "seeds": len(shards)}


# ------------------------------------------------- Metric 1: satisfaction curve
def fig_satisfaction(stats):
    rows = _load_jsonl("closedloop_runs.jsonl")
    if not rows:
        return
    conds = ["A", "B", "echo"]
    cols = {"A": GREY, "B": AMBER, "echo": TEAL}
    names = {"A": "Baseline A (no memory)", "B": "Baseline B (self-eval + decay)", "echo": "Echo"}
    max_turn = max(r["turn"] for r in rows)
    fig, ax = plt.subplots(figsize=(7.4, 4.6))
    curve = {}
    for c in conds:
        means, sds, turns = [], [], []
        for t in range(max_turn + 1):
            vals = [r["eval_score"] for r in rows if r["condition"] == c and r["turn"] == t]
            if vals:
                turns.append(t); means.append(np.mean(vals)); sds.append(np.std(vals))
        means, sds = np.array(means), np.array(sds)
        ax.plot(turns, means, "-o", color=cols[c], label=names[c], lw=2, ms=4)
        ax.fill_between(turns, means - sds, means + sds, color=cols[c], alpha=0.12)
        curve[c] = {"turns": turns, "mean": means.tolist()}
    ax.set_xlabel("interaction (turn)"); ax.set_ylabel("GLM-judged satisfaction (1–5)")
    ax.set_ylim(0.8, 5.2); ax.legend(loc="center right")
    ax.set_title("Metric 1 — proactive satisfaction over time\n(independent GLM-5.2 evaluator scores each first-output)")
    fig.tight_layout(); fig.savefig(FIG / "satisfaction_curve.png"); plt.close(fig)

    # paired stats: echo vs A, echo vs B (paired by persona,seed,turn)
    from scipy.stats import wilcoxon
    def paired(c1, c2):
        key = lambda r: (r["persona"], r["seed"], r["turn"])
        m1 = {key(r): r["eval_score"] for r in rows if r["condition"] == c1}
        m2 = {key(r): r["eval_score"] for r in rows if r["condition"] == c2}
        common = sorted(set(m1) & set(m2))
        a = [m1[k] for k in common]; b = [m2[k] for k in common]
        out = {"n_pairs": len(common), "mean_1": float(np.mean(a)) if a else None,
               "mean_2": float(np.mean(b)) if b else None, "cliffs_delta": cliffs_delta(a, b)}
        try:
            if a and any(x != y for x, y in zip(a, b)):
                w, p = wilcoxon(a, b)
                out["wilcoxon_p"] = float(p)
        except Exception as e:
            out["wilcoxon_p"] = None
        return out
    stats["metric1_satisfaction"] = {
        "curve": curve,
        "overall_mean": {c: float(np.mean([r["eval_score"] for r in rows if r["condition"] == c]))
                         for c in conds},
        "late_mean_turns>=half": {c: float(np.mean([r["eval_score"] for r in rows
                                  if r["condition"] == c and r["turn"] >= max_turn // 2])) for c in conds},
        "echo_vs_A": paired("echo", "A"), "echo_vs_B": paired("echo", "B"),
    }


# ------------------------------------------------- Metric 2: error propagation
def fig_error_prop(stats):
    rows = _load_jsonl("closedloop_runs.jsonl")
    mm = _load_json("micrometrics.json") or {}
    out = {}
    if rows:
        # NOTE: the closed-loop "bad-approach used turns" count is CONFOUNDED by
        # the always-on M5 profile — once the profile is injected the planted bad
        # example no longer degrades the output, so it stays "present" but
        # harmless and is never punished. We record it for transparency but do
        # NOT chart it as persistence (it would misleadingly show echo high). The
        # real closed-loop error-propagation signal is the satisfaction outcome:
        # Baseline B stays at the floor (errors persist) while Echo recovers.
        per_run = defaultdict(lambda: defaultdict(int))
        for r in rows:
            per_run[(r["condition"], r["persona"], r["seed"])]["used"] += int(r.get("used_bad", 0))
        agg = defaultdict(list)
        for (c, p, s), d in per_run.items():
            agg[c].append(d["used"])
        out["closedloop_bad_used_turns_mean"] = {c: float(np.mean(v)) for c, v in agg.items()}
        out["closedloop_note"] = ("bad-used count is confounded by the always-on profile "
                                  "(bad example becomes harmless); use deterministic Metric 2 "
                                  "+ the satisfaction gap instead.")
        # Mean satisfaction on the planted bad task — the honest closed-loop view.
        bad_sat = {}
        for c in ("A", "B", "echo"):
            v = [r["eval_score"] for r in rows if r["condition"] == c and r.get("is_bad_task")]
            if v:
                bad_sat[c] = float(np.mean(v))
        out["closedloop_bad_task_satisfaction"] = bad_sat

    # Deterministic Metric 2 — prefer the new seeded sweep (n_bad 3 & 10, 5 seeds)
    det = _load_json("metric2_deterministic.json")
    if det:
        out["deterministic"] = det
        keys = [k for k in ("n_bad_3", "n_bad_10") if k in det]
        fig, ax = plt.subplots(figsize=(6.4, 4.2))
        x = np.arange(len(keys)); w = 0.36
        echo_means = [det[k]["echo_caught_mean"] for k in keys]
        echo_err = [[det[k]["echo_caught_mean"] - det[k]["echo_caught_min"] for k in keys],
                    [det[k]["echo_caught_max"] - det[k]["echo_caught_mean"] for k in keys]]
        b_means = [det[k]["baseline_b_caught_mean"] for k in keys]
        ax.bar(x - w/2, echo_means, w, yerr=echo_err, capsize=5, color=TEAL, label="Echo")
        ax.bar(x + w/2, b_means, w, color=AMBER, label="Baseline B")
        for i, k in enumerate(keys):
            ax.text(i - w/2, echo_means[i] + 0.2, f"{echo_means[i]:.1f}/{det[k]['n_bad']}",
                    ha="center", fontweight="bold", fontsize=9)
            ax.text(i + w/2, b_means[i] + 0.2, f"{b_means[i]:.0f}/{det[k]['n_bad']}",
                    ha="center", fontweight="bold", fontsize=9)
        ax.set_xticks(x); ax.set_xticklabels([f"{det[k]['n_bad']} planted bad skills" for k in keys])
        ax.set_ylabel("bad skills caught (mean over 5 seeds)")
        ax.legend(); ax.set_title("Metric 2 (deterministic, 5 seeds, 15% noise)\n"
                                  "silently-wrong skills caught — Echo vs frequency-decay Baseline B")
        fig.tight_layout(); fig.savefig(FIG / "error_propagation_deterministic.png"); plt.close(fig)
    elif "Metric2_error_propagation" in mm:
        m = mm["Metric2_error_propagation"]
        out["deterministic"] = {
            "echo_caught": f"{m.get('echo_bad_caught')}/{m.get('n_bad')}",
            "baseline_b_caught": f"{m.get('baseline_b_bad_caught')}/{m.get('n_bad')}",
        }
    stats["metric2_error_propagation"] = out


# ------------------------------------------------- Metric 3: overhead
def fig_overhead(stats):
    u = _load_json("closedloop_usage.json")
    if not u or "per_run" not in u:
        return
    pr = u["per_run"]
    agg = defaultdict(lambda: defaultdict(list))
    for r in pr:
        c = r["condition"]
        agg[c]["agent"].append(r["agent_tokens"])
        agg[c]["lb"].append(r.get("layerB_tokens", 0))
        agg[c]["lc"].append(r.get("layerC_tokens", 0))
        agg[c]["firings"].append(r.get("layerC_firings", 0))
        agg[c]["turns"].append(r.get("turns", 10))
    conds = [c for c in ["A", "B", "echo"] if c in agg]
    labels = {"A": "Baseline A", "B": "Baseline B", "echo": "Echo"}
    agent_m = [np.mean(agg[c]["agent"]) for c in conds]
    lb_m = [np.mean(agg[c]["lb"]) for c in conds]
    lc_m = [np.mean(agg[c]["lc"]) for c in conds]

    # ---- stacked overhead chart: agent / Layer B (steady) / Layer C (incident)
    fig, ax = plt.subplots(figsize=(6.8, 4.4))
    x = np.arange(len(conds))
    ax.bar(x, agent_m, color=GREY, label="agent (mimo) tokens")
    ax.bar(x, lb_m, bottom=agent_m, color=TEAL, label="Echo Layer B (every turn)")
    ax.bar(x, lc_m, bottom=np.array(agent_m) + np.array(lb_m), color=ROSE,
           label="Echo Layer C (on alarm, rare)")
    ax.set_xticks(x); ax.set_xticklabels([labels[c] for c in conds])
    ax.set_ylabel("mean tokens per 10-turn run"); ax.legend(fontsize=8)
    ax.set_title("Metric 3 — token overhead (A also revises = fair baseline)\n"
                 "Echo's steady-state add is Layer B only; Layer C is a rare event")
    fig.tight_layout(); fig.savefig(FIG / "overhead.png"); plt.close(fig)

    # ---- steady vs incident decomposition (the fair framing)
    echo_turns = sum(agg["echo"]["turns"]) or 1
    lb_total = sum(agg["echo"]["lb"]); lc_total = sum(agg["echo"]["lc"])
    firings_total = sum(agg["echo"]["firings"])
    # per-turn fair agent cost: use mean over ALL conditions' agent tokens / turns
    fair_agent_per_turn = np.mean([r["agent_tokens"] / r.get("turns", 10)
                                   for r in pr if r["condition"] in ("A", "B", "echo")])
    lb_per_turn = lb_total / echo_turns
    steady_overhead_pct = (lb_per_turn / fair_agent_per_turn) * 100 if fair_agent_per_turn else 0
    lc_per_firing = (lc_total / firings_total) if firings_total else 0
    # split runs: judge-fired vs not (daily vs incident)
    nojudge = [r for r in pr if r["condition"] == "echo" and r.get("layerC_firings", 0) == 0]
    judged = [r for r in pr if r["condition"] == "echo" and r.get("layerC_firings", 0) > 0]

    # fair agent comparison now that A revises
    a_agent = np.mean(agg["A"]["agent"]) if "A" in agg else 0
    echo_agent = np.mean(agg["echo"]["agent"]) if "echo" in agg else 0

    stats["metric3_overhead"] = {
        "mean_agent_tokens": {c: float(np.mean(agg[c]["agent"])) for c in conds},
        "mean_layerB_tokens": {c: float(np.mean(agg[c]["lb"])) for c in conds},
        "mean_layerC_tokens": {c: float(np.mean(agg[c]["lc"])) for c in conds},
        "fair_agent_tokens_A_vs_echo": {"A": float(a_agent), "echo": float(echo_agent),
                                        "echo_vs_A_pct": float((echo_agent/a_agent-1)*100) if a_agent else None},
        "steady_state": {
            "layerB_tokens_per_turn": float(lb_per_turn),
            "fair_agent_tokens_per_turn": float(fair_agent_per_turn),
            "steady_overhead_pct_per_turn": float(steady_overhead_pct),
            "note": "daily conversation = Layer B only, no Layer C",
        },
        "layerC_incident": {
            "total_firings": float(firings_total),
            "total_echo_turns": int(echo_turns),
            "firing_rate_per_turn": float(firings_total / echo_turns),
            "tokens_per_firing": float(lc_per_firing),
            "runs_with_no_judge": len(nojudge),
            "runs_with_judge": len(judged),
            "note": "every run had a PLANTED bad skill; normal use ~ 0 firings",
        },
        "mean_satisfaction_by_judge_presence": {
            "no_judge_runs_overhead_tokens": float(np.mean([r["agent_tokens"]+r["layerB_tokens"]+r["layerC_tokens"] for r in nojudge])) if nojudge else None,
            "judge_runs_overhead_tokens": float(np.mean([r["agent_tokens"]+r["layerB_tokens"]+r["layerC_tokens"] for r in judged])) if judged else None,
        },
    }


# ------------------------------------------------- PrefEval (2nd benchmark)
def fig_prefeval(stats):
    shards = _load_shards("prefeval_summary")
    if not shards:
        return
    conds = ["no_pref", "echo_m5", "oracle"]
    conds = [c for c in conds if c in shards[0]["adherence"]]
    labels = {"no_pref": "No memory", "echo_m5": "Echo M5", "oracle": "Oracle\n(pref given)"}
    means = {c: float(np.mean([sh["adherence"][c] for sh in shards])) for c in conds}
    sds = {c: float(np.std([sh["adherence"][c] for sh in shards])) for c in conds}
    n_total = int(sum(sh["n"] for sh in shards))
    fig, ax = plt.subplots(figsize=(5.8, 4.2))
    bars = ax.bar([labels[c] for c in conds], [means[c] for c in conds],
                  yerr=[sds[c] for c in conds], capsize=5,
                  color=[GREY, TEAL, "#2a7d76"][:len(conds)])
    for b, c in zip(bars, conds):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + sds[c] + 0.02,
                f"{means[c]*100:.0f}%", ha="center", fontweight="bold")
    ax.set_ylim(0, 1.08); ax.set_ylabel("preference adherence rate")
    ax.set_title(f"PrefEval (ICLR'25): preference adherence\n"
                 f"n={n_total} over {len(shards)} seeds (error bars = ±1 SD across seeds)")
    fig.tight_layout(); fig.savefig(FIG / "prefeval_adherence.png"); plt.close(fig)
    stats["prefeval"] = {"adherence_mean": means, "adherence_sd": sds,
                         "n_total": n_total, "seeds": len(shards)}


# ------------------------------------------------- micro-metrics summary
def fig_micrometrics(stats):
    mm = _load_json("micrometrics.json")
    if not mm:
        return
    stats["micrometrics"] = mm


def main():
    stats = {}
    fig_personamem(stats)
    fig_prefeval(stats)
    fig_satisfaction(stats)
    fig_error_prop(stats)
    fig_overhead(stats)
    fig_micrometrics(stats)
    (RES / "stats.json").write_text(json.dumps(stats, indent=2, ensure_ascii=False))
    print("figures ->", sorted(p.name for p in FIG.glob("*.png")))
    print("stats   ->", RES / "stats.json")
    print(json.dumps(stats.get("metric1_satisfaction", {}).get("overall_mean", {}), indent=2))


if __name__ == "__main__":
    main()
