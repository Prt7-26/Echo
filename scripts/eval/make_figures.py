"""Publication-grade figure rendering for the Echo evaluation report.

Redraws the six report figures in a unified Nature-leaning style (English labels,
restrained semantic palette, explicit error bars + statistics, editable vector
text). This is a *figures-only* successor to ``analyze.py``: it does NOT recompute
``stats.json`` — it consumes the already-computed, committed numbers so the figures
are reproducible from the repository alone.

Data sources (committed):
    DevPlan/experiment-figures/stats.json            (all aggregate numbers)
    DevPlan/experiment-figures/metric2_deterministic.json
Data sources (raw, local-only; gitignored under scripts/eval/results/):
    closedloop_runs.jsonl    -> per-turn 95% CI for the satisfaction curve
A committed sidecar ``satisfaction_curve_ci.json`` is written next to the figures
so the satisfaction panel stays reproducible even without the raw shards.

Outputs (PNG @ 300 dpi + vector PDF) into DevPlan/experiment-figures/:
    personamem_accuracy, prefeval_adherence, satisfaction_curve,
    error_propagation_deterministic, overhead, micrometrics

Run:  python -m scripts.eval.make_figures      (from repo root)
"""
from __future__ import annotations

import json
import math
import pathlib
from collections import defaultdict

import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# ---------------------------------------------------------------- paths
ROOT = pathlib.Path(__file__).resolve().parents[2]
FIGDIR = ROOT / "DevPlan" / "experiment-figures"
RAWDIR = ROOT / "scripts" / "eval" / "results"
FIGDIR.mkdir(parents=True, exist_ok=True)

STATS = json.loads((FIGDIR / "stats.json").read_text())

# ---------------------------------------------------------------- style
# One restrained, semantically-fixed palette reused across every figure:
#   Echo  = teal (hero / the system under test, matches the product identity)
#   A     = neutral grey (no-memory control)
#   B     = muted ochre (self-eval + decay control)
#   ceiling/oracle = deep teal;  full-history = muted blue
#   Layer C = rose;  gains = green;  drops = red  (directional cues only)
C_ECHO = "#2A9D8F"
C_ECHO_DEEP = "#1D6F66"
C_A = "#8A9099"
C_B = "#C68A3C"
C_FULL = "#5B7FB4"
C_LAYERB = "#2A9D8F"
C_LAYERC = "#C25B6E"
C_AGENT = "#B9BEC4"
C_GOOD = "#2E9E44"
C_BAD = "#C0392B"
INK = "#272727"

mpl.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
    "svg.fonttype": "none",
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
    "font.size": 9,
    "axes.titlesize": 10.5,
    "axes.labelsize": 9.5,
    "axes.linewidth": 0.9,
    "axes.edgecolor": "#444444",
    "axes.labelcolor": INK,
    "text.color": INK,
    "xtick.color": "#444444",
    "ytick.color": "#444444",
    "xtick.labelsize": 8.5,
    "ytick.labelsize": 8.5,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "axes.axisbelow": True,
    "grid.color": "#000000",
    "grid.alpha": 0.07,
    "grid.linewidth": 0.7,
    "legend.frameon": False,
    "legend.fontsize": 8.5,
    "figure.dpi": 120,
    "savefig.dpi": 300,
})


def save(fig, name):
    for ext in ("png", "pdf"):
        fig.savefig(FIGDIR / f"{name}.{ext}", bbox_inches="tight")
    plt.close(fig)


def panel_label(ax, s, x=-0.14, y=1.04):
    ax.text(x, y, s, transform=ax.transAxes, fontsize=12, fontweight="bold",
            va="bottom", ha="left", color=INK)


def titled(ax, main, sub):
    """Bold title with a smaller grey sub-line that never collides with it."""
    ax.set_title(main, fontweight="bold", pad=26)
    ax.text(0.5, 1.015, sub, transform=ax.transAxes, ha="center", va="bottom",
            fontsize=7.8, color="#555")


def style(ax):
    ax.tick_params(length=3, width=0.8)
    ax.set_axisbelow(True)


# ============================================================ Fig 1: PersonaMem
def fig_personamem():
    pm = STATS["personamem"]
    conds = ["no_mem", "full_hist", "echo_m5"]
    labels = ["No memory\n(cold)", "Full history\n(naive RAG)", "Echo M5\n(retrieval)"]
    colors = [C_A, C_FULL, C_ECHO]
    means = [pm["accuracy_mean"][c] * 100 for c in conds]
    sds = [pm["accuracy_sd"][c] * 100 for c in conds]
    inj = [pm["avg_inject_chars"][c] for c in conds]

    fig, ax = plt.subplots(figsize=(5.4, 4.1))
    x = np.arange(3)
    bars = ax.bar(x, means, width=0.62, color=colors, edgecolor="white", linewidth=0.6,
                  yerr=sds, error_kw=dict(elinewidth=1.1, capthick=1.1, capsize=4,
                                          ecolor="#3a3a3a"), zorder=3)
    for xi, m, s in zip(x, means, sds):
        ax.text(xi, m + s + 1.6, f"{m:.1f}%", ha="center", va="bottom",
                fontweight="bold", fontsize=10)
    # context-cost annotation (Echo's efficiency story) under each bar
    for xi, c in zip(x, inj):
        txt = "0 chars" if c == 0 else f"{int(round(c)):,} chars"
        ax.text(xi, 2.0, txt, ha="center", va="bottom", fontsize=7.6, color="#3a3a3a")
    ax.annotate("⅓ the context,\nhigher accuracy", xy=(2, means[2]), xytext=(1.25, 70),
                fontsize=8, color=C_ECHO_DEEP, ha="center",
                arrowprops=dict(arrowstyle="-|>", color=C_ECHO_DEEP, lw=1.0))
    ax.set_ylim(0, 80)
    ax.set_xticks(x); ax.set_xticklabels(labels)
    ax.set_ylabel("Preference-probe accuracy (%)")
    titled(ax, "PersonaMem (COLM 2025): preference recall",
           f"n = {pm['n_total']} probes · {pm['seeds']} seeds · error bars = ±1 SD across seeds")
    style(ax)
    save(fig, "personamem_accuracy")


# ============================================================ Fig 2: PrefEval
def fig_prefeval():
    pe = STATS["prefeval"]
    conds = ["no_pref", "echo_m5", "oracle"]
    labels = ["No memory", "Echo M5\n(retrieval)", "Oracle\n(pref. given)"]
    colors = [C_A, C_ECHO, C_ECHO_DEEP]
    means = [pe["adherence_mean"][c] * 100 for c in conds]
    sds = [pe["adherence_sd"][c] * 100 for c in conds]

    fig, ax = plt.subplots(figsize=(5.4, 4.1))
    x = np.arange(3)
    ax.bar(x, means, width=0.62, color=colors, edgecolor="white", linewidth=0.6,
           yerr=sds, error_kw=dict(elinewidth=1.1, capthick=1.1, capsize=4,
                                   ecolor="#3a3a3a"), zorder=3)
    for xi, m, s in zip(x, means, sds):
        ax.text(xi, m + s + 2.2, f"{m:.0f}%", ha="center", va="bottom",
                fontweight="bold", fontsize=10)
    # gap-to-oracle bracket
    ax.annotate("", xy=(1, means[1]), xytext=(2, means[2]),
                arrowprops=dict(arrowstyle="<->", color="#888", lw=0.9))
    ax.text(1.5, (means[1] + means[2]) / 2 + 4, f"−{means[2]-means[1]:.0f} pts\nto oracle",
            ha="center", va="bottom", fontsize=7.6, color="#555")
    ax.set_ylim(0, 108)
    ax.set_xticks(x); ax.set_xticklabels(labels)
    ax.set_ylabel("Preference adherence rate (%)")
    titled(ax, "PrefEval (ICLR 2025): adherence in generation",
           f"n = {pe['n_total']} · {pe['seeds']} seeds · 1 preference retrieved from a 200-preference pool")
    style(ax)
    save(fig, "prefeval_adherence")


# ============================================================ Fig 3: satisfaction
def _satisfaction_ci():
    """Per-(condition,turn) mean and 95% CI from raw runs; cache a committed sidecar."""
    raw = RAWDIR / "closedloop_runs.jsonl"
    side = FIGDIR / "satisfaction_curve_ci.json"
    if raw.exists():
        rows = [json.loads(l) for l in raw.open()]
        out = {}
        for c in ("A", "B", "echo"):
            turns, mean, lo, hi = [], [], [], []
            for t in range(max(r["turn"] for r in rows) + 1):
                v = np.array([r["eval_score"] for r in rows
                              if r["condition"] == c and r["turn"] == t], float)
                if not len(v):
                    continue
                m = v.mean(); ci = 1.96 * v.std(ddof=1) / math.sqrt(len(v))
                turns.append(t); mean.append(m); lo.append(m - ci); hi.append(m + ci)
            out[c] = {"turns": turns, "mean": mean, "lo": lo, "hi": hi, "n_per_turn": len(v)}
        side.write_text(json.dumps(out, indent=2))
        return out
    return json.loads(side.read_text())


def fig_satisfaction():
    ci = _satisfaction_ci()
    m1 = STATS["metric1_satisfaction"]
    spec = {"A": ("Baseline A · no memory", C_A, "o", "--"),
            "B": ("Baseline B · self-eval + decay", C_B, "s", "--"),
            "echo": ("Echo · full system", C_ECHO, "o", "-")}
    fig, ax = plt.subplots(figsize=(7.0, 4.4))
    for c in ("A", "B", "echo"):
        d = ci[c]
        name, col, mk, ls = spec[c]
        lw = 2.6 if c == "echo" else 1.7
        z = 5 if c == "echo" else 3
        ax.fill_between(d["turns"], d["lo"], d["hi"], color=col, alpha=0.13, lw=0, zorder=z - 1)
        ax.plot(d["turns"], d["mean"], ls, color=col, lw=lw, marker=mk, ms=5,
                mfc="white" if c != "echo" else col, mec=col, mew=1.3,
                label=name, zorder=z)
    # endpoint value labels
    for c, dy in (("echo", 0.18), ("A", 0.22), ("B", -0.32)):
        d = ci[c]
        ax.text(d["turns"][-1] + 0.12, d["mean"][-1] + dy, f"{d['mean'][-1]:.1f}",
                color=spec[c][1], fontsize=9, fontweight="bold", va="center")
    ax.set_xlim(-0.3, 10.2); ax.set_ylim(0.8, 5.25)
    ax.set_xticks(range(0, 10))
    ax.set_yticks([1, 2, 3, 4, 5])
    ax.set_xlabel("Interaction index (turn)")
    ax.set_ylabel("Proactive satisfaction (GLM-5.2, 1–5)")
    titled(ax, "Metric 1: proactive satisfaction over time",
           "Independent GLM-5.2 scores each turn's first output · 15 personas × 3 seeds (n = 45/turn) · bands = 95% CI")
    # effect-size callout box
    dA = m1["echo_vs_A"]; dB = m1["echo_vs_B"]
    txt = ("Echo vs A:  Cliff's δ = %.2f,  p < 10$^{-72}$\n"
           "Echo vs B:  Cliff's δ = %.2f,  p < 10$^{-75}$\n"
           "(paired by persona/seed/turn, n = %d)" %
           (dA["cliffs_delta"], dB["cliffs_delta"], dA["n_pairs"]))
    ax.text(0.985, 0.045, txt, transform=ax.transAxes, ha="right", va="bottom",
            fontsize=7.6, color=INK,
            bbox=dict(boxstyle="round,pad=0.45", fc="#f3f8f7", ec=C_ECHO, lw=0.8))
    ax.legend(loc="center right", bbox_to_anchor=(1.0, 0.62))
    style(ax)
    save(fig, "satisfaction_curve")


# ============================================================ Fig 4: error prop
def fig_error_prop():
    det = json.loads((FIGDIR / "metric2_deterministic.json").read_text())
    keys = [k for k in ("n_bad_3", "n_bad_10") if k in det]
    fig, axes = plt.subplots(1, 2, figsize=(8.6, 4.0))

    # --- panel a: bad skills caught, Echo vs Baseline B ---------------------
    ax = axes[0]
    x = np.arange(len(keys)); w = 0.36
    echo = [det[k]["echo_caught_mean"] for k in keys]
    echo_err = [[det[k]["echo_caught_mean"] - det[k]["echo_caught_min"] for k in keys],
                [det[k]["echo_caught_max"] - det[k]["echo_caught_mean"] for k in keys]]
    bb = [det[k]["baseline_b_caught_mean"] for k in keys]
    ax.bar(x - w / 2, echo, w, yerr=echo_err, capsize=4, color=C_ECHO,
           edgecolor="white", linewidth=0.6, label="Echo", zorder=3,
           error_kw=dict(elinewidth=1.1, capthick=1.1, ecolor="#333"))
    ax.bar(x + w / 2, bb, w, color=C_B, edgecolor="white", linewidth=0.6,
           label="Baseline B (frequency decay)", zorder=3)
    for i, k in enumerate(keys):
        n = det[k]["n_bad"]
        ax.text(i - w / 2, echo[i] + 0.25, f"{echo[i]:.0f}/{n}", ha="center",
                va="bottom", fontweight="bold", fontsize=9, color=C_ECHO_DEEP)
        ax.text(i + w / 2, bb[i] + 0.25, f"{bb[i]:.0f}/{n}", ha="center",
                va="bottom", fontweight="bold", fontsize=9, color=C_B)
    ax.set_xticks(x)
    ax.set_xticklabels([f"{det[k]['n_bad']} planted\nbad skills" for k in keys])
    ax.set_ylabel("Silently-wrong skills retired")
    ax.set_ylim(0, max(max(echo), 1) * 1.18)
    ax.set_title("Bad-skill retirement (5 seeds, 15% noise)", fontsize=9.5, fontweight="bold")
    ax.legend(loc="upper left")
    panel_label(ax, "a")
    style(ax)

    # --- panel b: WHY — final confidence separates good vs bad --------------
    ax = axes[1]
    rows3 = STATS["metric2_error_propagation"]["deterministic"]["n_bad_3"]["per_seed"]
    rows10 = STATS["metric2_error_propagation"]["deterministic"]["n_bad_10"]["per_seed"]
    bad = [r["echo_mean_conf_bad"] for r in rows3 + rows10]
    good = [r["echo_mean_conf_good"] for r in rows3 + rows10]
    # threshold reference bands
    ax.axhspan(0, 0.10, color=C_BAD, alpha=0.06, lw=0)
    ax.axhline(0.30, color="#999", ls=":", lw=1.0)
    ax.axhline(0.10, color=C_BAD, ls=":", lw=1.0)
    ax.text(-0.46, 0.315, "c$_{min}$ = 0.30  (→ review)", fontsize=7, color="#777", va="bottom", ha="left")
    ax.text(-0.46, 0.115, "c$_{retire}$ = 0.10  (→ retired)", fontsize=7, color=C_BAD, va="bottom", ha="left")
    rng = np.random.default_rng(0)
    for xc, vals, col, lab in ((0, good, C_GOOD, "Good skills"), (1, bad, C_BAD, "Bad skills")):
        jit = (rng.random(len(vals)) - 0.5) * 0.16
        ax.scatter(xc + jit, vals, s=26, color=col, alpha=0.75, edgecolor="white",
                   linewidth=0.5, zorder=3)
        ax.scatter([xc], [np.mean(vals)], marker="_", s=900, color=col, linewidth=2.2, zorder=4)
        ax.text(xc, np.mean(vals) + (0.05 if xc == 0 else 0.07), f"mean {np.mean(vals):.2f}",
                ha="center", va="bottom", fontsize=8, color=col, fontweight="bold")
    ax.set_xlim(-0.5, 1.5); ax.set_ylim(0, 1.05)
    ax.set_xticks([0, 1]); ax.set_xticklabels(["Good skills", "Bad skills"])
    ax.set_ylabel("Echo final confidence")
    ax.set_title("Mechanism: confidence cleanly separates the two", fontsize=9.5, fontweight="bold")
    panel_label(ax, "b")
    style(ax)

    fig.suptitle("Metric 2: error propagation — Echo retires every silently-wrong skill; frequency decay retires none",
                 fontsize=10.5, fontweight="bold", y=1.02)
    fig.tight_layout(w_pad=2.5)
    save(fig, "error_propagation_deterministic")


# ============================================================ Fig 5: overhead
def fig_overhead():
    o = STATS["metric3_overhead"]
    conds = ["A", "B", "echo"]
    labels = ["Baseline A", "Baseline B", "Echo"]
    agent = [o["mean_agent_tokens"][c] for c in conds]
    lb = [o["mean_layerB_tokens"][c] for c in conds]
    lc = [o["mean_layerC_tokens"][c] for c in conds]

    fig, axes = plt.subplots(1, 2, figsize=(8.8, 4.1),
                             gridspec_kw=dict(width_ratios=[1.18, 1.0]))

    # --- panel a: total token budget per 10-turn run -----------------------
    ax = axes[0]
    x = np.arange(3)
    ax.bar(x, agent, color=C_AGENT, edgecolor="white", linewidth=0.6,
           label="Agent (mimo) reply", zorder=3)
    ax.bar(x, lb, bottom=agent, color=C_LAYERB, edgecolor="white", linewidth=0.6,
           label="Echo Layer B (every turn)", zorder=3)
    ax.bar(x, lc, bottom=np.array(agent) + np.array(lb), color=C_LAYERC,
           edgecolor="white", linewidth=0.6, label="Echo Layer C (on alarm)", zorder=3)
    for xi, c in zip(x, conds):
        tot = agent[conds.index(c)] + lb[conds.index(c)] + lc[conds.index(c)]
        ax.text(xi, tot + 350, f"{tot/1000:.1f}k", ha="center", va="bottom",
                fontsize=8.5, fontweight="bold")
    pct = o["fair_agent_tokens_A_vs_echo"]["echo_vs_A_pct"]
    ax.annotate(f"fair agent-token\nΔ = +{pct:.1f}% vs A", xy=(2, agent[2]),
                xytext=(1.15, 9200), fontsize=7.8, color=C_ECHO_DEEP, ha="center",
                arrowprops=dict(arrowstyle="-|>", color=C_ECHO_DEEP, lw=1.0))
    ax.set_xticks(x); ax.set_xticklabels(labels)
    ax.set_ylabel("Mean tokens per 10-turn run")
    ax.set_ylim(0, max(np.array(agent) + np.array(lb) + np.array(lc)) * 1.22)
    ax.set_title("Total token budget", fontsize=9.5, fontweight="bold")
    ax.legend(loc="upper left", fontsize=7.4, bbox_to_anchor=(0.0, 1.0))
    panel_label(ax, "a")
    style(ax)

    # --- panel b: the honest steady-state framing --------------------------
    ax = axes[1]
    ss = o["steady_state"]; inc = o["layerC_incident"]
    agent_pt = ss["fair_agent_tokens_per_turn"]
    lb_pt = ss["layerB_tokens_per_turn"]
    ax.bar([0], [agent_pt], width=0.5, color=C_AGENT, edgecolor="white", linewidth=0.6,
           label="Agent reply", zorder=3)
    ax.bar([0], [lb_pt], bottom=[agent_pt], width=0.5, color=C_LAYERB,
           edgecolor="white", linewidth=0.6, label="Layer B add", zorder=3)
    ax.annotate("", xy=(0.34, agent_pt), xytext=(0.34, agent_pt + lb_pt),
                arrowprops=dict(arrowstyle="<->", color=INK, lw=1.0))
    ax.text(0.40, agent_pt + lb_pt / 2,
            f"+{ss['steady_overhead_pct_per_turn']:.0f}%\nsteady-state",
            fontsize=8, va="center", ha="left", color=INK)
    ax.text(0, agent_pt / 2, f"{agent_pt:.0f}", ha="center", va="center", fontsize=8, color="#444")
    ax.text(0, agent_pt + lb_pt / 2, f"{lb_pt:.0f}", ha="center", va="center", fontsize=7.5, color="white")
    ax.set_xlim(-0.6, 1.25); ax.set_ylim(0, (agent_pt + lb_pt) * 1.45)
    ax.set_xticks([0]); ax.set_xticklabels(["Per-turn\nsteady state"])
    ax.set_ylabel("Tokens per turn")
    ax.set_title("Steady-state cost (Layer B only)", fontsize=9.5, fontweight="bold")
    # Layer C rarity callout
    ax.text(0.66, (agent_pt + lb_pt) * 1.30,
            "Layer C (judge) is rare:\n"
            f"• {int(inc['total_firings'])} firings / {inc['total_echo_turns']} turns (≈1 per 35)\n"
            f"• {inc['runs_with_no_judge']}/{inc['runs_with_no_judge']+inc['runs_with_judge']} runs never fired it\n"
            "• under a planted-bad stress test;\n  normal use ≈ 0",
            fontsize=7.2, va="top", ha="left", color=INK,
            bbox=dict(boxstyle="round,pad=0.4", fc="#fdf3f5", ec=C_LAYERC, lw=0.8))
    ax.legend(loc="upper left", fontsize=7.6)
    panel_label(ax, "b")
    style(ax)

    fig.suptitle("Metric 3: system overhead — modest and on a cheap, off-latency-path tier",
                 fontsize=10.5, fontweight="bold", y=1.02)
    fig.tight_layout(w_pad=2.5)
    save(fig, "overhead")


# ============================================================ Fig 6: micrometrics
def fig_micrometrics():
    mm = STATS["micrometrics"]
    fig, axes = plt.subplots(2, 2, figsize=(8.4, 7.0))

    # (a) M4 confidence vs true usefulness — the quantitative hero
    ax = axes[0, 0]
    pairs = mm["M4_confidence"]["pairs"]
    tu = [p["true_usefulness"] for p in pairs]
    cf = [p["confidence"] for p in pairs]
    ax.plot([0, 1], [0, 1], ls="--", color="#ccc", lw=1.0, zorder=1)
    ax.scatter(tu, cf, s=66, color=C_ECHO, edgecolor="white", linewidth=0.8, zorder=3)
    ax.set_xlim(0.4, 1.0); ax.set_ylim(0.2, 0.7)
    ax.set_xlabel("Planted true usefulness")
    ax.set_ylabel("Echo confidence")
    rho = mm["M4_confidence"]["spearman_rho"]
    ax.text(0.04, 0.95, f"Spearman ρ = +{rho:.2f}\n(n = {mm['M4_confidence']['n_pairs']} skills)",
            transform=ax.transAxes, va="top", ha="left", fontsize=8.5, color=C_ECHO_DEEP,
            bbox=dict(boxstyle="round,pad=0.35", fc="#f3f8f7", ec=C_ECHO, lw=0.7))
    ax.set_title("M4 · confidence tracks true usefulness", fontsize=9.5, fontweight="bold")
    panel_label(ax, "a")
    style(ax)

    # (b) M1 trigger precision/recall — Echo vs Hermes rule
    ax = axes[0, 1]
    m1 = mm["M1_trigger"]
    metrics = ["Precision", "Recall"]
    echo_v = [m1["echo_precision"], m1["echo_recall"]]
    herm_v = [m1["hermes_precision"], m1["hermes_recall"]]
    x = np.arange(2); w = 0.36
    ax.bar(x - w / 2, echo_v, w, color=C_ECHO, edgecolor="white", linewidth=0.6, label="Echo M1", zorder=3)
    ax.bar(x + w / 2, herm_v, w, color=C_A, edgecolor="white", linewidth=0.6, label="Hermes ≥-tool rule", zorder=3)
    for xi, v in zip(x - w / 2, echo_v):
        ax.text(xi, v + 0.03, f"{v:.2f}", ha="center", va="bottom", fontsize=8, color=C_ECHO_DEEP)
    for xi, v in zip(x + w / 2, herm_v):
        ax.text(xi, v + 0.03, f"{v:.2f}", ha="center", va="bottom", fontsize=8, color="#666")
    ax.set_xticks(x); ax.set_xticklabels(metrics)
    ax.set_ylim(0, 1.18); ax.set_ylabel("Score")
    ax.set_title("M1 · nomination ties the Hermes rule", fontsize=9.5, fontweight="bold")
    ax.legend(loc="upper right", fontsize=7.6)
    panel_label(ax, "b")
    style(ax)

    # (c) M3 drift detection P/R/F1
    ax = axes[1, 0]
    m3 = mm["M3_drift"]
    names = ["Precision", "Recall", "F1"]
    vals = [m3["precision"], m3["recall"], m3["f1"]]
    ax.bar(np.arange(3), vals, width=0.6, color=C_ECHO, edgecolor="white", linewidth=0.6, zorder=3)
    for xi, v in enumerate(vals):
        ax.text(xi, v + 0.03, f"{v:.2f}", ha="center", va="bottom", fontsize=8.5,
                fontweight="bold", color=C_ECHO_DEEP)
    ax.set_xticks(np.arange(3)); ax.set_xticklabels(names)
    ax.set_ylim(0, 1.18); ax.set_ylabel("Score")
    ax.set_title("M3 · drift detection (perfect, small n)", fontsize=9.5, fontweight="bold")
    ax.text(0.5, 0.05, f"TP={m3['tp']}  FP={m3['fp']}  FN={m3['fn']}  TN={m3['tn']}\n"
                       f"({m3['excluded_warmup']} warm-up invocations excluded)",
            transform=ax.transAxes, ha="center", va="bottom", fontsize=7.4, color="#555",
            bbox=dict(boxstyle="round,pad=0.35", fc="white", ec="#ddd", lw=0.7))
    panel_label(ax, "c")
    style(ax)

    # (d) M5 retrieval recall ± confidence weights
    ax = axes[1, 1]
    m5 = mm["M5_retrieval"]
    vals = [m5["recall_no_weights"], m5["recall_with_confidence_weights"]]
    ax.bar([0, 1], vals, width=0.55, color=[C_A, C_ECHO], edgecolor="white", linewidth=0.6, zorder=3)
    for xi, v in zip([0, 1], vals):
        ax.text(xi, v + 0.02, f"{v:.3f}", ha="center", va="bottom", fontsize=8.5, fontweight="bold")
    ax.set_xticks([0, 1]); ax.set_xticklabels(["recall@k\n(no weights)", "recall@k\n(+ conf. weights)"])
    ax.set_ylim(0, 0.55); ax.set_ylabel("Recall@k")
    ax.set_title("M5 · weighting-uplift null on this built-in case", fontsize=9.5, fontweight="bold")
    ax.text(0.5, 0.92, "real M5 value: Figs 1–2", transform=ax.transAxes, ha="center",
            va="top", fontsize=7.6, color="#555", style="italic")
    panel_label(ax, "d")
    style(ax)

    fig.suptitle("Per-module micro-metrics (deterministic, planted ground truth, scale-invariant)",
                 fontsize=11, fontweight="bold", y=1.005)
    fig.tight_layout(h_pad=2.6, w_pad=2.6)
    save(fig, "micrometrics")


def main():
    fig_personamem()
    fig_prefeval()
    fig_satisfaction()
    fig_error_prop()
    fig_overhead()
    fig_micrometrics()
    print("wrote figures (png+pdf) ->", FIGDIR)
    for p in sorted(FIGDIR.glob("*.png")):
        print("  ", p.name)


if __name__ == "__main__":
    main()
