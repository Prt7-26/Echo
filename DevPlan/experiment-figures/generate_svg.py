"""Editorial-style hand-authored SVG charts for the Echo evaluation report.

Design language: FT / Economist / Datawrapper — generous whitespace, light
horizontal-only gridlines, direct value labels, restrained palette, system
typography (SF Pro on macOS via -apple-system). Reads the committed numbers
(stats.json + satisfaction_curve_ci.json) so the charts stay reproducible.

Run:  python generate_svg.py     (from this directory)
"""
from __future__ import annotations
import json, pathlib

FIG = pathlib.Path(__file__).resolve().parent          # DevPlan/experiment-figures
ROOT = FIG.parents[1]                                   # repo root
OUT = FIG                                               # write the .svg report figures here
RAW = ROOT / "scripts" / "eval" / "results" / "closedloop_runs.jsonl"
S = json.loads((FIG / "stats.json").read_text())

# canonical report-figure filenames (match the links in experiment-report*.md)
NAME = {"personamem": "personamem_accuracy", "prefeval": "prefeval_adherence",
        "satisfaction": "satisfaction_curve", "error_prop": "error_propagation_deterministic",
        "overhead": "overhead", "micrometrics": "micrometrics"}


def _load_ci():
    """Per-(condition,turn) mean + 95% CI; rebuild from raw shards if present,
    else read the committed sidecar so the figure stays reproducible alone."""
    import math
    side = FIG / "satisfaction_curve_ci.json"
    if RAW.exists():
        rows = [json.loads(l) for l in RAW.open()]
        out = {}
        for c in ("A", "B", "echo"):
            turns, mean, lo, hi = [], [], [], []
            for t in range(max(r["turn"] for r in rows) + 1):
                v = [r["eval_score"] for r in rows if r["condition"] == c and r["turn"] == t]
                if not v:
                    continue
                m = sum(v) / len(v)
                sd = (sum((x - m) ** 2 for x in v) / (len(v) - 1)) ** 0.5
                ci = 1.96 * sd / math.sqrt(len(v))
                turns.append(t); mean.append(m); lo.append(m - ci); hi.append(m + ci)
            out[c] = {"turns": turns, "mean": mean, "lo": lo, "hi": hi}
        side.write_text(json.dumps(out, indent=2))
        return out
    return json.loads(side.read_text())


CI = _load_ci()

# ---- palette --------------------------------------------------------------
INK, INK2, INK3 = "#19222C", "#5B6B7B", "#93A1B0"
GRID = "#ECEFF3"
ECHO, ECHO_DEEP = "#0FA295", "#0A6E66"
A_GREY = "#A9B4BF"
B_AMBER = "#E1A458"
FULL = "#7E9BD0"
GOOD, BAD = "#46A56B", "#DD6B4B"
LAYERC = "#D9748B"
AGENT = "#CCD3DA"

STYLE = """<style>
text{font-family:-apple-system,'SF Pro Display','SF Pro Text','Inter','Helvetica Neue',Arial,sans-serif;fill:%s;font-variant-numeric:tabular-nums}
.title{font-size:17px;font-weight:600;letter-spacing:.1px}
.sub{font-size:11.5px;fill:#8492A0;letter-spacing:.2px}
.val{font-size:13.5px;font-weight:600}
.valsm{font-size:11.5px;font-weight:600}
.axis{font-size:10.5px;fill:#9AA6B2}
.cat{font-size:12.5px;fill:#3F4D5A;font-weight:500}
.lgd{font-size:12px;fill:#41505D}
.note{font-size:10.5px;fill:#6B7A88}
.tag{font-size:10px;fill:#8492A0;letter-spacing:.4px}
.pl{font-size:13px;font-weight:700;fill:#19222C}
</style>""" % INK


def doc(w, h, body):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
            f'width="{w}" height="{h}" font-size="12">{STYLE}'
            f'<rect width="{w}" height="{h}" fill="#FFFFFF"/>{body}</svg>')


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def T(x, y, s, cls="", anchor="start", extra=""):
    a = f' text-anchor="{anchor}"' if anchor else ""
    c = f' class="{cls}"' if cls else ""
    return f'<text x="{x:.1f}" y="{y:.1f}"{c}{a}{extra}>{esc(s)}</text>'


def L(x1, y1, x2, y2, stroke=GRID, w=1, dash=""):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{stroke}" stroke-width="{w}"{d}/>'


def R(x, y, w, h, fill, rx=4, extra=""):
    return f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}" fill="{fill}"{extra}/>'


def C(cx, cy, r, fill, extra=""):
    return f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r}" fill="{fill}"{extra}/>'


def whisker(cx, ytop, ybot, color=INK3, cap=5, w=1.4):
    return (L(cx, ytop, cx, ybot, color, w) +
            L(cx - cap, ytop, cx + cap, ytop, color, w) +
            L(cx - cap, ybot, cx + cap, ybot, color, w))


def header(x, y, title, sub):
    return T(x, y, title, "title") + T(x, y + 18, sub, "sub")


# ============================================================ 1 · PersonaMem
def personamem():
    pm = S["personamem"]
    conds = ["no_mem", "full_hist", "echo_m5"]
    names = ["No memory", "Full history", "Echo M5"]
    subs = ["cold", "naive RAG", "retrieval"]
    cols = [A_GREY, FULL, ECHO]
    mean = [pm["accuracy_mean"][c] * 100 for c in conds]
    sd = [pm["accuracy_sd"][c] * 100 for c in conds]
    inj = [pm["avg_inject_chars"][c] for c in conds]

    W, H = 720, 440
    mL, mR, mT, mB = 56, 150, 92, 64
    plot_b = H - mB
    ymax = 80
    def sy(v): return plot_b - (v / ymax) * (plot_b - mT)
    s = header(mL, 40, "Preference recall — PersonaMem (COLM 2025)",
               "Multiple-choice probe accuracy · 3 seeds · n = 540 · whiskers ±1 SD")
    # gridlines + y labels
    for g in range(0, ymax + 1, 20):
        y = sy(g)
        s += L(mL, y, W - mR, y)
        s += T(mL - 10, y + 3.5, f"{g}", "axis", "end")
    s += T(mL - 10, sy(ymax) - 12, "%", "axis", "end")
    # bars
    plot_w = (W - mR) - mL
    bw = 92
    gap = (plot_w - bw * 3) / 3
    for i, c in enumerate(conds):
        x = mL + gap / 2 + i * (bw + gap)
        y = sy(mean[i])
        s += R(x, y, bw, plot_b - y, cols[i], rx=5)
        # whisker
        cx = x + bw / 2
        s += whisker(cx, sy(mean[i] + sd[i]), sy(mean[i] - sd[i]))
        # value
        s += T(cx, y - 12, f"{mean[i]:.1f}%", "val", "middle")
        # category
        s += T(cx, plot_b + 22, names[i], "cat", "middle")
        s += T(cx, plot_b + 38, subs[i], "note", "middle")
        # context chip inside bar
        ctx = "0 chars" if inj[i] == 0 else f"{int(round(inj[i])):,} chars"
        s += T(cx, plot_b - 12, ctx, "tag", "middle",
               extra=f' fill="{"#52606D" if i==0 else "#FFFFFF"}"')
    # annotation callout (right)
    ax0 = W - mR + 18
    s += T(ax0, mT + 26, "Echo wins on", "lgd")
    s += T(ax0, mT + 44, "both axes:", "lgd")
    s += T(ax0, mT + 70, "+17.8 pts", "pl", extra=f' fill="{ECHO_DEEP}"')
    s += T(ax0, mT + 86, "vs cold", "note")
    s += T(ax0, mT + 112, "⅓ the context", "pl", extra=f' fill="{ECHO_DEEP}"')
    s += T(ax0, mT + 128, "vs full history", "note")
    (OUT / f"{NAME['personamem']}.svg").write_text(doc(W, H, s))


# ============================================================ 2 · PrefEval
def prefeval():
    pe = S["prefeval"]
    conds = ["no_pref", "echo_m5", "oracle"]
    names = ["No memory", "Echo M5", "Oracle"]
    subs = ["", "retrieval", "pref. given"]
    cols = [A_GREY, ECHO, ECHO_DEEP]
    mean = [pe["adherence_mean"][c] * 100 for c in conds]
    sd = [pe["adherence_sd"][c] * 100 for c in conds]

    W, H = 720, 440
    mL, mR, mT, mB = 56, 150, 92, 64
    plot_b = H - mB
    ymax = 100
    def sy(v): return plot_b - (v / ymax) * (plot_b - mT)
    s = header(mL, 40, "Preference adherence — PrefEval (ICLR 2025)",
               "Generation adheres to the stored preference · 3 seeds · n = 300 · whiskers ±1 SD")
    for g in range(0, ymax + 1, 20):
        y = sy(g)
        s += L(mL, y, W - mR, y)
        s += T(mL - 10, y + 3.5, f"{g}", "axis", "end")
    s += T(mL - 10, sy(ymax) - 12, "%", "axis", "end")
    plot_w = (W - mR) - mL
    bw = 92
    gap = (plot_w - bw * 3) / 3
    cxs = []
    for i, c in enumerate(conds):
        x = mL + gap / 2 + i * (bw + gap)
        y = sy(mean[i])
        s += R(x, y, bw, plot_b - y, cols[i], rx=5)
        cx = x + bw / 2
        cxs.append((cx, y))
        s += whisker(cx, sy(mean[i] + sd[i]), sy(mean[i] - sd[i]))
        s += T(cx, y - 12, f"{mean[i]:.0f}%", "val", "middle")
        s += T(cx, plot_b + 22, names[i], "cat", "middle")
        if subs[i]:
            s += T(cx, plot_b + 38, subs[i], "note", "middle")
    # gap-to-oracle bracket between echo and oracle
    (cx1, y1), (cx2, y2) = cxs[1], cxs[2]
    by = min(y1, y2) - 30
    s += L(cx1, by, cx2, by, INK3, 1.2)
    s += L(cx1, by, cx1, y1 - 8, INK3, 1.2)
    s += L(cx2, by, cx2, y2 - 8, INK3, 1.2)
    s += T((cx1 + cx2) / 2, by - 8, "−8 pts", "note", "middle")
    # right note
    ax0 = W - mR + 18
    s += T(ax0, mT + 40, "Cold model", "lgd")
    s += T(ax0, mT + 58, "collapses to", "lgd")
    s += T(ax0, mT + 78, "13%", "pl", extra=' fill="#8492A0"')
    s += T(ax0, mT + 112, "Echo retrieves", "lgd")
    s += T(ax0, mT + 130, "1-of-200 →", "lgd")
    s += T(ax0, mT + 150, "82%", "pl", extra=f' fill="{ECHO_DEEP}"')
    (OUT / f"{NAME['prefeval']}.svg").write_text(doc(W, H, s))


# ============================================================ 3 · Satisfaction
def satisfaction():
    spec = [("A", "Baseline A · no memory", A_GREY, False),
            ("B", "Baseline B · self-eval + decay", B_AMBER, False),
            ("echo", "Echo · full system", ECHO, True)]
    W, H = 760, 460
    mL, mR, mT, mB = 52, 196, 96, 56
    plot_b = H - mB
    xmax = 9
    ylo, yhi = 1, 5
    def sx(t): return mL + (t / xmax) * ((W - mR) - mL)
    def sy(v): return plot_b - ((v - ylo) / (yhi - ylo)) * (plot_b - mT)
    s = header(mL, 40, "Proactive satisfaction across the conversation",
               "Independent GLM-5.2 score (1–5) of each turn's first output · 15 personas × 3 seeds · band = 95% CI")
    # gridlines
    for g in range(1, 6):
        y = sy(g)
        s += L(mL, y, W - mR, y)
        s += T(mL - 10, y + 3.5, f"{g}", "axis", "end")
    for t in range(0, xmax + 1):
        s += T(sx(t), plot_b + 20, f"{t}", "axis", "middle")
    s += T((mL + (W - mR)) / 2, H - 12, "interaction (turn)", "note", "middle")
    # echo CI band
    e = CI["echo"]
    pts_top = " ".join(f"{sx(t):.1f},{sy(v):.1f}" for t, v in zip(e["turns"], e["hi"]))
    pts_bot = " ".join(f"{sx(t):.1f},{sy(v):.1f}" for t, v in zip(reversed(e["turns"]), reversed(e["lo"])))
    s += f'<polygon points="{pts_top} {pts_bot}" fill="{ECHO}" fill-opacity="0.12"/>'
    # lines
    for key, _, col, hero in spec:
        d = CI[key]
        pts = " ".join(f"{sx(t):.1f},{sy(v):.1f}" for t, v in zip(d["turns"], d["mean"]))
        w = 3 if hero else 1.8
        dash_attr = "" if hero else ' stroke-dasharray="1,5"'
        s += (f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="{w}" '
              f'stroke-linecap="round" stroke-linejoin="round"{dash_attr}/>')
        for t, v in zip(d["turns"], d["mean"]):
            s += C(sx(t), sy(v), 3.6 if hero else 2.8, col)
        # endpoint label
        lab = d["mean"][-1]
        s += T(sx(xmax) + 10, sy(lab) + 4, f"{lab:.1f}", "val", "start", extra=f' fill="{col}"')
    # legend (right) — placed in the empty mid-band so Echo's top endpoint label can't collide
    lx, ly = W - mR + 16, 156
    for i, (key, name, col, hero) in enumerate(spec):
        yy = ly + i * 26
        s += L(lx, yy, lx + 22, yy, col, 3 if hero else 1.8)
        s += C(lx + 11, yy, 3.4, col)
        # wrap names manually
        parts = name.split(" · ")
        s += T(lx + 30, yy - 2, parts[0], "lgd")
        s += T(lx + 30, yy + 12, parts[1] if len(parts) > 1 else "", "note")
    # effect-size card
    cardx, cardy = lx, ly + 3 * 26 + 18
    cw, ch = 176, 96
    s += f'<rect x="{cardx}" y="{cardy}" width="{cw}" height="{ch}" rx="10" fill="#F2F9F8" stroke="{ECHO}" stroke-opacity="0.5"/>'
    s += T(cardx + 14, cardy + 22, "Echo vs baselines", "lgd", extra=' font-weight="600"')
    s += T(cardx + 14, cardy + 44, "Cliff's δ = 0.84 / 0.86", "note", extra=f' fill="{ECHO_DEEP}" font-size="12"')
    s += T(cardx + 14, cardy + 62, "p < 10⁻⁷²  (large)", "note", extra=f' fill="{ECHO_DEEP}" font-size="12"')
    s += T(cardx + 14, cardy + 82, "n = 450 paired", "note")
    (OUT / f"{NAME['satisfaction']}.svg").write_text(doc(W, H, s))


# ============================================================ 4 · Error prop
def error_prop():
    det = json.loads((FIG / "metric2_deterministic.json").read_text())
    keys = ["n_bad_3", "n_bad_10"]
    rows3 = S["metric2_error_propagation"]["deterministic"]["n_bad_3"]["per_seed"]
    rows10 = S["metric2_error_propagation"]["deterministic"]["n_bad_10"]["per_seed"]
    bad = [r["echo_mean_conf_bad"] for r in rows3 + rows10]
    good = [r["echo_mean_conf_good"] for r in rows3 + rows10]

    W, H = 760, 470
    s = header(46, 40, "Error propagation — silently-wrong skills get retired",
               "Deterministic harness · 5 seeds · 15% signal noise · planted ground truth")
    # ---- panel a (left): caught counts ----
    aL, aR, aT, aB = 52, 380, 96, 70
    plot_b = H - aB
    ymax = 10
    def asy(v): return plot_b - (v / ymax) * (plot_b - aT)
    s += T(aL, aT - 16, "a", "pl")
    s += T(aL + 16, aT - 16, "Bad skills retired", "lgd", extra=' font-weight="600"')
    for g in [0, 2, 4, 6, 8, 10]:
        y = asy(g); s += L(aL, y, aR, y); s += T(aL - 10, y + 3.5, f"{g}", "axis", "end")
    groups = [("3 planted", 3, det["n_bad_3"]), ("10 planted", 10, det["n_bad_10"])]
    gw = (aR - aL) / 2
    for gi, (lab, n, dd) in enumerate(groups):
        gx = aL + gi * gw + gw / 2
        bw = 52
        # echo bar
        ev = dd["echo_caught_mean"]
        x1 = gx - bw - 6
        s += R(x1, asy(ev), bw, plot_b - asy(ev), ECHO, rx=5)
        s += T(x1 + bw / 2, asy(ev) - 10, f"{ev:.0f}/{n}", "valsm", "middle", extra=f' fill="{ECHO_DEEP}"')
        # baseline B bar (0)
        bv = dd["baseline_b_caught_mean"]
        x2 = gx + 6
        s += R(x2, plot_b - 2, bw, 2, B_AMBER, rx=2)
        s += T(x2 + bw / 2, plot_b - 8, f"{bv:.0f}/{n}", "valsm", "middle", extra=f' fill="{B_AMBER}"')
        s += T(gx, plot_b + 22, lab, "cat", "middle")
        s += T(gx, plot_b + 38, "bad skills", "note", "middle")
    # mini legend for panel a — stacked, kept well left of the full-height 10/10 bar
    s += C(aL + 8, aT + 16, 5, ECHO); s += T(aL + 18, aT + 20, "Echo", "note")
    s += C(aL + 8, aT + 34, 5, B_AMBER); s += T(aL + 18, aT + 38, "Baseline B (freq. decay)", "note")

    # ---- panel b (right): confidence separation ----
    bL, bR, bT, bB = 430, W - 30, 96, 70
    pb = H - bB
    def bsy(v): return pb - v * (pb - bT)   # 0..1
    s += T(bL, bT - 16, "b", "pl")
    s += T(bL + 16, bT - 16, "Why: final confidence separates them", "lgd", extra=' font-weight="600"')
    # threshold bands
    s += R(bL, bsy(0.10), bR - bL, pb - bsy(0.10), "#FBEDE9", rx=0, extra=' fill-opacity="0.8"')
    for thr, lab, col in [(0.30, "c_min 0.30 → review", "#9AA6B2"), (0.10, "c_retire 0.10 → retired", BAD)]:
        y = bsy(thr)
        s += L(bL, y, bR, y, col, 1.1, dash="4,4")
        s += T(bL + 4, y - 6, lab, "note", "start", extra=f' fill="{col}"')
    for g in [0, 0.25, 0.5, 0.75, 1.0]:
        s += T(bL - 8, bsy(g) + 3.5, f"{g:.2f}", "axis", "end")
    # dot strips
    import math
    def strip(cx, vals, col, label):
        body = ""
        for i, v in enumerate(vals):
            jit = (((i * 2654435761) % 1000) / 1000 - 0.5) * 26
            body += C(cx + jit, bsy(v), 3.4, col, extra=' fill-opacity="0.7"')
        m = sum(vals) / len(vals)
        body += L(cx - 26, bsy(m), cx + 26, bsy(m), col, 2.4)
        # label to the RIGHT of the strip, at mean-line height — never over the points
        body += T(cx + 32, bsy(m) + 3.5, f"mean {m:.2f}", "valsm", "start", extra=f' fill="{col}"')
        body += T(cx, pb + 22, label + " skills", "cat", "middle")
        return body
    s += strip(bL + (bR - bL) * 0.30, good, GOOD, "Good")
    s += strip(bL + (bR - bL) * 0.70, bad, BAD, "Bad")
    (OUT / f"{NAME['error_prop']}.svg").write_text(doc(W, H, s))


# ============================================================ 5 · Overhead
def overhead():
    o = S["metric3_overhead"]
    conds = ["A", "B", "echo"]
    names = ["Baseline A", "Baseline B", "Echo"]
    agent = [o["mean_agent_tokens"][c] for c in conds]
    lb = [o["mean_layerB_tokens"][c] for c in conds]
    lc = [o["mean_layerC_tokens"][c] for c in conds]
    ss = o["steady_state"]; inc = o["layerC_incident"]

    W, H = 760, 560
    s = header(46, 40, "System overhead — modest, on a cheap off-latency tier",
               "Tokens per 10-turn run · Baseline A also revises (fair agent-token comparison)")
    # panel a stacked
    aL, aR, aT, aB = 56, 400, 96, 160
    pb = H - aB
    ymax = 16000
    def asy(v): return pb - (v / ymax) * (pb - aT)
    s += T(aL, aT - 16, "a", "pl"); s += T(aL + 16, aT - 16, "Total token budget", "lgd", extra=' font-weight="600"')
    for g in [0, 4000, 8000, 12000, 16000]:
        y = asy(g); s += L(aL, y, aR, y); s += T(aL - 8, y + 3.5, f"{g//1000}k", "axis", "end")
    plot_w = aR - aL; bw = 70; gap = (plot_w - bw * 3) / 3
    for i, c in enumerate(conds):
        x = aL + gap / 2 + i * (bw + gap); cx = x + bw / 2
        y0 = pb
        for val, col in [(agent[i], AGENT), (lb[i], ECHO), (lc[i], LAYERC)]:
            if val <= 0: continue
            hh = (val / ymax) * (pb - aT)
            s += R(x, y0 - hh, bw, hh, col, rx=3)
            y0 -= hh
        tot = agent[i] + lb[i] + lc[i]
        s += T(cx, y0 - 10, f"{tot/1000:.1f}k", "valsm", "middle")
        s += T(cx, pb + 22, names[i], "cat", "middle")
    # fair delta note
    pct = o["fair_agent_tokens_A_vs_echo"]["echo_vs_A_pct"]
    s += T(aL, pb + 44, f"fair agent-token Δ (Echo vs A) = +{pct:.1f}%", "note", extra=f' fill="{ECHO_DEEP}"')
    # legend (horizontal, under title)
    lx = aL; ly = aT + 2
    items = [("agent", AGENT), ("Layer B", ECHO), ("Layer C", LAYERC)]
    off = 0
    for lab, col in items:
        s += R(lx + off, ly - 8, 11, 11, col, rx=2); s += T(lx + off + 16, ly + 1, lab, "note"); off += 78

    # panel b: steady-state + rarity card
    bL = 440
    s += T(bL, aT - 16, "b", "pl"); s += T(bL + 16, aT - 16, "Steady-state per turn", "lgd", extra=' font-weight="600"')
    bb = pb; bx = bL + 30; bwid = 64
    ymax2 = 1100
    def bsy(v): return bb - (v / ymax2) * (bb - aT)
    for g in [0, 250, 500, 750, 1000]:
        y = bsy(g); s += L(bL, y, bL + 150, y); s += T(bL - 6, y + 3.5, f"{g}", "axis", "end")
    ag = ss["fair_agent_tokens_per_turn"]; lbt = ss["layerB_tokens_per_turn"]
    s += R(bx, bsy(ag), bwid, bb - bsy(ag), AGENT, rx=3)
    s += R(bx, bsy(ag + lbt), bwid, bsy(ag) - bsy(ag + lbt), ECHO, rx=3)
    s += T(bx + bwid/2, bsy(ag) + 16, f"{ag:.0f}", "valsm", "middle", extra=' fill="#5B6B7B"')
    s += T(bx + bwid/2, bsy(ag+lbt) + 14, f"+{lbt:.0f}", "valsm", "middle", extra=' fill="#FFFFFF"')
    s += T(bx + bwid + 12, bsy(ag + lbt) + 4, f"+{ss['steady_overhead_pct_per_turn']:.0f}%", "pl", extra=f' fill="{ECHO_DEEP}" font-size="14"')
    s += T(bx + bwid/2, bb + 22, "per turn", "cat", "middle")
    # rarity card
    cx0, cy0, cw, ch = bL + 30, bb + 44, 250, 84
    s += f'<rect x="{cx0}" y="{cy0}" width="{cw}" height="{ch}" rx="10" fill="#FCF1F4" stroke="{LAYERC}" stroke-opacity="0.5"/>'
    s += T(cx0 + 14, cy0 + 22, "Layer C (judge) is rare", "lgd", extra=' font-weight="600"')
    s += T(cx0 + 14, cy0 + 42, f"{int(inc['total_firings'])} firings / {inc['total_echo_turns']} turns (≈1 per 35)", "note")
    s += T(cx0 + 14, cy0 + 58, f"{inc['runs_with_no_judge']}/{inc['runs_with_no_judge']+inc['runs_with_judge']} runs never fired it", "note")
    s += T(cx0 + 14, cy0 + 74, "planted-bad stress test; normal use ≈ 0", "note")
    (OUT / f"{NAME['overhead']}.svg").write_text(doc(W, H, s))


# ============================================================ 6 · Micrometrics
def micrometrics():
    mm = S["micrometrics"]
    W, H = 760, 520
    s = header(46, 40, "Per-module micro-metrics",
               "Deterministic · planted ground truth · invariant to run scale")
    # 2x2 cells
    cells = [(56, 92), (430, 92), (56, 320), (430, 320)]
    cw, chh = 274, 168

    # (a) M4 scatter
    cxL, cyT = cells[0]
    s += T(cxL, cyT - 8, "a", "pl"); s += T(cxL + 16, cyT - 8, "M4 · confidence tracks usefulness", "lgd", extra=' font-weight="600"')
    px, py, pw, ph = cxL + 36, cyT + 18, 210, 120
    def mx(v): return px + (v - 0.4) / 0.6 * pw
    def my(v): return py + ph - (v - 0.2) / 0.5 * ph
    s += L(px, py + ph, px + pw, py, "#E3E8ED", 1, dash="4,4")  # trend-direction guide (in box)
    for g in [0.2, 0.35, 0.5, 0.65]:
        s += L(px, my(g), px + pw, my(g)); s += T(px - 6, my(g) + 3, f"{g:.2f}", "axis", "end")
    for g in [0.5, 0.7, 0.9]:
        s += T(mx(g), py + ph + 14, f"{g:.1f}", "axis", "middle")
    for p in mm["M4_confidence"]["pairs"]:
        s += C(mx(p["true_usefulness"]), my(p["confidence"]), 5, ECHO, extra=' fill-opacity="0.85"')
    s += T(px + pw, py + 4, f"ρ = +{mm['M4_confidence']['spearman_rho']:.2f}", "valsm", "end", extra=f' fill="{ECHO_DEEP}"')
    s += T(px + pw/2, py + ph + 30, "true usefulness", "note", "middle")

    # (b) M1 grouped bars
    cxL, cyT = cells[1]
    s += T(cxL, cyT - 8, "b", "pl"); s += T(cxL + 16, cyT - 8, "M1 · ties the Hermes rule", "lgd", extra=' font-weight="600"')
    px, py, pw, ph = cxL + 32, cyT + 18, 214, 120
    m1 = mm["M1_trigger"]
    pairs = [("Precision", m1["echo_precision"], m1["hermes_precision"]),
             ("Recall", m1["echo_recall"], m1["hermes_recall"])]
    for g in [0, 0.5, 1.0]:
        y = py + ph - g * ph; s += L(px, y, px + pw, y); s += T(px - 6, y + 3, f"{g:.1f}", "axis", "end")
    gw = pw / 2
    for i, (lab, ev, hv) in enumerate(pairs):
        gx = px + i * gw + gw / 2; bw = 34
        s += R(gx - bw - 4, py + ph - ev * ph, bw, ev * ph, ECHO, rx=3)
        s += R(gx + 4, py + ph - hv * ph, bw, hv * ph, A_GREY, rx=3)
        s += T(gx - bw/2 - 4, py + ph - ev*ph - 6, f"{ev:.2f}", "tag", "middle", extra=f' fill="{ECHO_DEEP}"')
        s += T(gx + bw/2 + 4, py + ph - hv*ph - 6, f"{hv:.2f}", "tag", "middle")
        s += T(gx, py + ph + 14, lab, "note", "middle")
    lyb = py + ph + 34; cxb = px + pw / 2
    s += C(cxb - 66, lyb, 4, ECHO); s += T(cxb - 58, lyb + 3.5, "Echo", "tag")
    s += C(cxb + 8, lyb, 4, A_GREY); s += T(cxb + 16, lyb + 3.5, "Hermes rule", "tag")

    # (c) M3 bars
    cxL, cyT = cells[2]
    s += T(cxL, cyT - 8, "c", "pl"); s += T(cxL + 16, cyT - 8, "M3 · drift detection (small n)", "lgd", extra=' font-weight="600"')
    px, py, pw, ph = cxL + 32, cyT + 18, 214, 110
    m3 = mm["M3_drift"]; vals = [("Prec.", m3["precision"]), ("Recall", m3["recall"]), ("F1", m3["f1"])]
    for g in [0, 0.5, 1.0]:
        y = py + ph - g * ph; s += L(px, y, px + pw, y); s += T(px - 6, y + 3, f"{g:.1f}", "axis", "end")
    bw = 44; gap = (pw - bw*3)/3
    for i, (lab, v) in enumerate(vals):
        x = px + gap/2 + i*(bw+gap)
        s += R(x, py + ph - v*ph, bw, v*ph, ECHO, rx=3)
        s += T(x + bw/2, py + ph - v*ph - 6, f"{v:.2f}", "tag", "middle", extra=f' fill="{ECHO_DEEP}"')
        s += T(x + bw/2, py + ph + 14, lab, "note", "middle")
    s += T(px, py + ph + 30, f"TP {m3['tp']} · FP {m3['fp']} · FN {m3['fn']} · TN {m3['tn']} · {m3['excluded_warmup']} warm-up excl.", "tag")

    # (d) M5 bars
    cxL, cyT = cells[3]
    s += T(cxL, cyT - 8, "d", "pl"); s += T(cxL + 16, cyT - 8, "M5 · weighting uplift null here", "lgd", extra=' font-weight="600"')
    px, py, pw, ph = cxL + 36, cyT + 18, 210, 110
    m5 = mm["M5_retrieval"]; vals = [("no weights", m5["recall_no_weights"], A_GREY), ("+ conf. weights", m5["recall_with_confidence_weights"], ECHO)]
    ymax = 0.5
    for g in [0, 0.25, 0.5]:
        y = py + ph - g/ymax*ph; s += L(px, y, px + pw, y); s += T(px - 6, y + 3, f"{g:.2f}", "axis", "end")
    bw = 64; gap = (pw - bw*2)/2
    for i, (lab, v, col) in enumerate(vals):
        x = px + gap/2 + i*(bw+gap)
        s += R(x, py + ph - v/ymax*ph, bw, v/ymax*ph, col, rx=3)
        s += T(x + bw/2, py + ph - v/ymax*ph - 6, f"{v:.3f}", "tag", "middle")
        s += T(x + bw/2, py + ph + 14, lab, "note", "middle")
    s += T(px, py + ph + 30, "real M5 value: see PersonaMem / PrefEval", "tag", extra=' font-style="italic"')
    (OUT / f"{NAME['micrometrics']}.svg").write_text(doc(W, H, s))


def main():
    personamem(); prefeval(); satisfaction(); error_prop(); overhead(); micrometrics()
    print("svg ->", OUT)
    for p in sorted(OUT.glob("*.svg")):
        print("  ", p.name)


if __name__ == "__main__":
    main()
