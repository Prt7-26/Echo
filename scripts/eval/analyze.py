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


# ---------------------------------------------------------------- PersonaMem
def fig_personamem(stats):
    s = _load_json("personamem_summary.json")
    if not s:
        return
    acc = s["accuracy"]; inj = s["avg_inject_chars"]
    conds = ["no_mem", "full_hist", "echo_m5"]
    labels = ["No memory\n(cold)", "Full history\n(naive RAG)", "Echo M5\n(retrieval)"]
    vals = [acc[c] for c in conds]
    fig, ax = plt.subplots(figsize=(6.2, 4.2))
    bars = ax.bar(labels, vals, color=[GREY, AMBER, TEAL])
    for b, c in zip(bars, conds):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.01,
                f"{acc[c]*100:.1f}%", ha="center", fontweight="bold")
        if inj[c]:
            ax.text(b.get_x() + b.get_width() / 2, 0.03,
                    f"{int(inj[c])} chars\ninjected", ha="center", color="white", fontsize=8)
    ax.set_ylim(0, max(vals) * 1.25); ax.set_ylabel("Preference-probe accuracy")
    ax.set_title(f"PersonaMem (COLM'25): preference recall, n={s['n_probes']} probes\n"
                 "Echo M5 beats naive full-history at ~1/3 the injected context")
    fig.tight_layout(); fig.savefig(FIG / "personamem_accuracy.png"); plt.close(fig)
    stats["personamem"] = {"accuracy": acc, "avg_inject_chars": inj, "n": s["n_probes"]}

    # per-type
    bt = s.get("by_type", {})
    if bt:
        types = list(bt.keys())
        x = np.arange(len(types)); w = 0.26
        fig, ax = plt.subplots(figsize=(11, 4.6))
        for i, (c, col) in enumerate(zip(conds, [GREY, AMBER, TEAL])):
            ax.bar(x + (i - 1) * w, [bt[t][c] for t in types], w, label=c, color=col)
        ax.set_xticks(x); ax.set_xticklabels([t.replace("_", "\n") for t in types], fontsize=7.5)
        ax.set_ylabel("accuracy"); ax.legend(); ax.set_title("PersonaMem accuracy by question type")
        fig.tight_layout(); fig.savefig(FIG / "personamem_by_type.png"); plt.close(fig)


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
        # turns the planted bad approach was actually used, per condition
        used = defaultdict(list)
        runs = defaultdict(int)
        for r in rows:
            pass
        per_run = defaultdict(lambda: defaultdict(int))
        for r in rows:
            k = (r["condition"], r["persona"], r["seed"])
            per_run[k]["used"] += int(r.get("used_bad", 0))
        agg = defaultdict(list)
        for (c, p, s), d in per_run.items():
            agg[c].append(d["used"])
        out["closedloop_bad_used_turns_mean"] = {c: float(np.mean(v)) for c, v in agg.items()}

        # bad-skill confidence decay (echo), averaged by turn
        maxt = max(r["turn"] for r in rows)
        decay = []
        for t in range(maxt + 1):
            vals = [r["bad_conf"] for r in rows if r["condition"] == "echo"
                    and r["turn"] == t and r.get("bad_conf") is not None]
            decay.append(np.mean(vals) if vals else np.nan)
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.3))
        ax1.plot(range(len(decay)), decay, "-o", color=ROSE, lw=2)
        ax1.axhline(0.30, ls="--", color=GREY, label="pending-review (0.30)")
        ax1.axhline(0.10, ls=":", color="black", label="retired (0.10)")
        ax1.set_xlabel("turn"); ax1.set_ylabel("planted bad-skill confidence")
        ax1.set_title("Echo decays the planted bad skill"); ax1.legend(fontsize=8)
        cs = list(out["closedloop_bad_used_turns_mean"])
        ax2.bar([{"A": "A", "B": "B", "echo": "Echo"}.get(c, c) for c in cs],
                [out["closedloop_bad_used_turns_mean"][c] for c in cs],
                color=[{"A": GREY, "B": AMBER, "echo": TEAL}.get(c, GREY) for c in cs])
        ax2.set_ylabel("turns the bad approach kept being used")
        ax2.set_title("Bad-approach persistence (lower = better)")
        fig.tight_layout(); fig.savefig(FIG / "error_propagation.png"); plt.close(fig)

    if "Metric2_error_propagation" in mm:
        m = mm["Metric2_error_propagation"]
        out["deterministic"] = {
            "echo_caught": f"{m.get('echo_bad_caught')}/{m.get('n_bad')}",
            "baseline_b_caught": f"{m.get('baseline_b_bad_caught')}/{m.get('n_bad')}",
            "echo_mean_conf_bad": m.get("echo_mean_conf_bad"),
        }
        # bar chart
        fig, ax = plt.subplots(figsize=(5.2, 4))
        nb = m.get("n_bad", 3)
        ax.bar(["Echo", "Baseline B"], [m.get("echo_bad_caught", 0), m.get("baseline_b_bad_caught", 0)],
               color=[TEAL, AMBER])
        ax.set_ylim(0, nb + 0.5); ax.set_ylabel(f"bad skills caught (of {nb})")
        ax.set_title("Metric 2 (deterministic) — silently-wrong skills caught")
        for i, v in enumerate([m.get("echo_bad_caught", 0), m.get("baseline_b_bad_caught", 0)]):
            ax.text(i, v + 0.05, str(v), ha="center", fontweight="bold")
        fig.tight_layout(); fig.savefig(FIG / "error_propagation_deterministic.png"); plt.close(fig)
    stats["metric2_error_propagation"] = out


# ------------------------------------------------- Metric 3: overhead
def fig_overhead(stats):
    u = _load_json("closedloop_usage.json")
    if not u or "per_run" not in u:
        return
    agg = defaultdict(lambda: {"agent": [], "signal": []})
    for r in u["per_run"]:
        agg[r["condition"]]["agent"].append(r["agent_tokens"])
        agg[r["condition"]]["signal"].append(r["signal_tokens"])
    conds = [c for c in ["A", "B", "echo"] if c in agg]
    agent_m = [np.mean(agg[c]["agent"]) for c in conds]
    signal_m = [np.mean(agg[c]["signal"]) for c in conds]
    labels = {"A": "Baseline A", "B": "Baseline B", "echo": "Echo"}
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    x = np.arange(len(conds))
    ax.bar(x, agent_m, color=GREY, label="agent (mimo) tokens")
    ax.bar(x, signal_m, bottom=agent_m, color=TEAL, label="Echo signal (Qwen) tokens")
    ax.set_xticks(x); ax.set_xticklabels([labels[c] for c in conds])
    ax.set_ylabel("mean tokens per run"); ax.legend()
    base = agent_m[conds.index("A")] if "A" in conds else agent_m[0]
    echo_total = (agent_m[conds.index("echo")] + signal_m[conds.index("echo")]) if "echo" in conds else 0
    ovh = (echo_total / base - 1) * 100 if base else 0
    ax.set_title(f"Metric 3 — token overhead\nEcho total ≈ {ovh:+.0f}% vs Baseline A")
    fig.tight_layout(); fig.savefig(FIG / "overhead.png"); plt.close(fig)
    stats["metric3_overhead"] = {
        "mean_agent_tokens": {c: float(np.mean(agg[c]["agent"])) for c in conds},
        "mean_signal_tokens": {c: float(np.mean(agg[c]["signal"])) for c in conds},
        "echo_overhead_pct_vs_A": float(ovh),
    }


# ------------------------------------------------- PrefEval (2nd benchmark)
def fig_prefeval(stats):
    s = _load_json("prefeval_summary.json")
    if not s:
        return
    adh = s["adherence"]
    conds = [c for c in ["no_pref", "echo_m5", "oracle"] if c in adh]
    labels = {"no_pref": "No memory", "echo_m5": "Echo M5", "oracle": "Oracle\n(pref given)"}
    fig, ax = plt.subplots(figsize=(5.8, 4.2))
    bars = ax.bar([labels[c] for c in conds], [adh[c] for c in conds],
                  color=[GREY, TEAL, "#2a7d76"][:len(conds)])
    for b, c in zip(bars, conds):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.01,
                f"{adh[c]*100:.0f}%", ha="center", fontweight="bold")
    ax.set_ylim(0, 1.05); ax.set_ylabel("preference adherence rate")
    ax.set_title(f"PrefEval (ICLR'25): preference adherence, n={s['n']}\n"
                 "Echo M5 retrieval recovers adherence a cold model loses")
    fig.tight_layout(); fig.savefig(FIG / "prefeval_adherence.png"); plt.close(fig)
    stats["prefeval"] = {"adherence": adh, "n": s["n"]}


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
