# Echo — Experimental Evaluation Report

*Auto-generated experiment run. All numbers are from real model calls (no fabricated
data); where a result is weak or null it is reported as such. This document is
regenerated/updated by `scripts/eval/analyze.py`; figures live in
`scripts/eval/results/figures/`.*

> **Status note for the oral checkpoint.** Every experiment below was actually
> executed against live models. The real-user (Telegram) study from the proposal
> is *not* part of this run — it is explicitly future work. Do not present it as done.

---

## 1. Experimental design

### 1.1 Four-model isolation (avoiding the circularity trap)

Echo's thesis is that *self-evaluation by the same model is biased* (the documented
Hermes flaw). An evaluation that let one model both generate the behaviour **and**
grade it would reproduce exactly that bias. So every role is a **different model
family**, and the metric scorer is independent of both the agent and the simulated
user:

| Role | Model | Why separate |
|---|---|---|
| **Simulated user / persona** | DeepSeek-V4-flash (Aliyun MaaS) | Produces requests, behavioural signals, NL feedback, thumbs |
| **Agent under test** | mimo-v2.5 (Xiaomi) | The system being personalised |
| **Echo's own signal models** | Qwen-plus (DashScope) | Layer B sentiment, Layer C judge, reason scoring |
| **Independent evaluator (the metric)** | GLM-5.2 (Zhipu, thinking off) | Scores outputs against *ground-truth* preference rubrics; never sees Echo's internals or the persona's own grade |

Ground truth is *planted*, not inferred: each persona's preference rules and each
skill's true usefulness are fixed in advance, so metrics are scored against an
external target rather than against another model's opinion.

### 1.2 Conditions (three control groups, per the proposal)

- **Baseline A — no memory.** Plain mimo, stateless. Cannot personalise; a control
  for "what the base model does cold."
- **Baseline B — self-eval + frequency/recency decay.** mimo plus a template memory
  that stores outputs its *own* self-evaluation deems successful, decayed by
  frequency/recency, with **no user signal** — i.e. the Hermes / agentmemory design
  Echo argues against.
- **Echo — full system.** mimo plus Echo's real plugin: M5 preference RAG
  (confidence-weighted neural retrieval), M4 confidence lifecycle, and the Layer B/C
  signal pipeline (Qwen), all driven by the user's signals.

### 1.3 Why idiosyncratic preferences

A pilot showed mimo satisfies generic "be concise/polite" preferences zero-shot — a
**ceiling effect** that hides any value of memory. The closed-loop personas therefore
carry **idiosyncratic, machine-checkable** preferences a strong model will not produce
by default (e.g. *"every email must end with exactly `Onward, R.`, body ≤ 60 words, no
exclamation marks"*; *"summaries must be exactly 3 emoji-led bullets ≤ 8 words"*;
*"always include the phrase `per my last note`, British spelling, no em-dashes"*). These
are (a) unguessable from the request and (b) mechanically verifiable, so memory is
*necessary* to satisfy them and the metric can discriminate.

The **metric scores the agent's first output each turn** (before any revision): *did
the assistant proactively honour what it should already know about this user?* A
revision round is allowed only as the channel through which the user *communicates* the
preference (their feedback names the unmet rule); the satisfying revision is what Echo
learns from, but it does not count toward the proactive-satisfaction score.

---

## 2. Benchmarks (third-party)

Two peer-reviewed personalization benchmarks ground the personas in external data and
defuse the "your simulated user is arbitrary" objection:

- **PersonaMem (COLM 2025)** — 20 personas, multi-session histories with *evolving*
  preferences, multiple-choice probes with the benchmark's own ground-truth answers.
  We test **preference recall**: does Echo's M5 memory help the agent answer correctly?
  No evaluator circularity (answers are graded against the benchmark key).
- **PrefEval (ICLR 2025)** — 1,000 (preference, question) pairs across 20 topics where
  the natural answer violates the stated preference. We test **preference adherence in
  generation**: with the preference held in M5 among a pool of others, does retrieval
  surface the right one so the answer adheres? Adherence judged by the independent
  GLM-5.2 evaluator.

---

## 3. Metrics

- **Metric 1 — satisfaction curve** (closed-loop): GLM-5.2 score (1–5) of each first
  output vs interaction index, per condition. Paired stats: Wilcoxon signed-rank and
  Cliff's δ (Echo vs A, Echo vs B), paired by (persona, seed, turn).
- **Metric 2 — error propagation**: a *silently-wrong* skill is planted; we track how
  long each condition keeps using it. Deterministic version via the built-in harness
  (`error_propagation`), plus the closed-loop's planted bad-preference decay.
- **Metric 3 — system overhead**: real token counts per condition (agent tokens +
  Echo's Qwen signal tokens, instrumented by wrapping the auxiliary client). Latency is
  not user-facing because Echo's Layer B/C run fire-and-forget off the hot path.
- **Per-module micro-metrics** (deterministic, no LLM, planted ground truth):
  M1 trigger precision/recall vs the Hermes ≥-tool-call rule; M3 drift precision/recall/F1;
  M4 confidence↔true-usefulness Spearman ρ; M5 retrieval recall@k ± confidence weights.

Statistics use **non-parametric** tests (Wilcoxon) and **effect sizes** (Cliff's δ), and
treat each (persona, seed) as a unit — *not* the within-run n — to avoid the
"infinite-n → everything significant" trap of simulated data.

---

## 4. Results

Figures: [`DevPlan/experiment-figures/`](experiment-figures/). Raw stats:
[`stats.json`](experiment-figures/stats.json). Full per-shard logs (supplementary
material): [`DevPlan/experiment-logs/`](experiment-logs/).

**Scale this run** (process-level parallel shards; all completed, none missing):
closed-loop **15 personas × 3 seeds × 3 conditions × 10 turns = 1350 turns**; both
benchmarks **× 3 seeds**; Metric 2 deterministic **n_bad ∈ {3,10} × 5 seeds**. Far
more samples than the previous version (3 personas, single seed), so the statistics
are much stronger.

**Key upgrade**: Echo now uses the new in-plugin **M5 consolidated preference
profile + always-inject** feature (schema v11) — the main reason satisfaction
jumped from ~2.3 (previous version) to ~4.5.

### 4.1 PersonaMem (preference recall), 3 seeds, n = 540

![PersonaMem](experiment-figures/personamem_accuracy.png)

| Condition | Accuracy (mean ± SD) | Injected context |
|---|---|---|
| No memory (cold) | 46.8% ± 1.7% | 0 |
| Full history (naive RAG) | 55.2% ± 3.0% | 8,254 chars |
| **Echo M5** | **64.6% ± 1.0%** | **2,653 chars** |

Tight error bars across 3 seeds, clean separation. Echo beats cold by **+17.8 pts**
and naive full-history by **+9.4 pts** at ~⅓ the context.

### 4.2 PrefEval (preference adherence in generation), 3 seeds, n = 300

![PrefEval](experiment-figures/prefeval_adherence.png)

| Condition | Adherence (mean ± SD) |
|---|---|
| No memory | 13% ± 1.4% |
| **Echo M5** | **82% ± 3.7%** |
| Oracle (preference handed over) | 90% ± 2.2% |

Cold model adheres only 13% (matching PrefEval's "preference following collapses").
Echo retrieves the right preference out of a 200-preference haystack and reaches
**82%**, within 8 pts of the oracle ceiling.

### 4.3 Metric 1 — satisfaction curve (closed-loop, 15 personas)

![satisfaction](experiment-figures/satisfaction_curve.png)

| Condition | Overall mean | Late mean (turns ≥ 5) |
|---|---|---|
| Baseline A (no memory) | 1.45 | 1.42 |
| Baseline B (self-eval + decay) | 1.29 | 1.29 |
| **Echo** | **4.48** | **4.69** |

Echo climbs fast and holds at **4.5–4.9**; both baselines stay on the floor. Paired
tests (paired by persona/seed/turn, **n = 450 pairs**):

- **Echo vs A**: Wilcoxon *p* = 4×10⁻⁷², **Cliff's δ = 0.84 (large)**
- **Echo vs B**: Wilcoxon *p* = 4×10⁻⁷⁵, **Cliff's δ = 0.86 (large)**

Versus the previous version (δ≈0.27, echo ~2.3), the M5 profile consolidation moved
the result from "significant but partial" to "**large effect, near ceiling**". The
residual gap is occasional multi-rule personas (e.g. the British-spelling triple
rule) where mimo drops one rule — a base-model instruction-following limit, reported
honestly.

### 4.4 Metric 2 — error propagation

![deterministic](experiment-figures/error_propagation_deterministic.png)

**Deterministic harness (5 seeds, 15% noise, planted ground truth) — primary result**:

| Planted bad skills | Echo caught (mean of 5 seeds) | Baseline B caught |
|---|---|---|
| 3 | **3 / 3** (every seed) | 0 / 3 |
| 10 | **10 / 10** (min also 10) | 0 / 10 |

Even with 15% signal noise Echo robustly retires every bad skill (0 false positives);
the frequency-decay Baseline B catches none — the cleanest proof of the thesis.

**Closed-loop view (an honest confound)**: the closed-loop "bad-approach used turns"
count is **confounded by the new profile feature** — once the profile is injected
every turn, the planted bad example **can no longer degrade the output**, so it stays
"present but harmless" and is never punished (the count is actually high for echo, but
that is harmless presence, not error propagation). The real closed-loop signal is
**satisfaction on the planted bad task**: Baseline B stays at **1.16** (error
persists), Echo reaches **4.44** (overcomes the planted bad approach). So Metric 2
relies on the **deterministic harness** as primary, with the satisfaction gap as
corroboration; the misleading "used-turns" chart is **deliberately not drawn**.

### 4.5 Metric 3 — system overhead (fair version + a correction to the proposal)

![overhead](experiment-figures/overhead.png)

This version fixes the earlier unfairness: **Baseline A now also revises**, so agent
tokens are apples-to-apples; and Layer B / Layer C are split **exactly by task**.

- **Fair agent-token comparison**: Echo is only **+5.3%** vs A (A 4,700 / Echo 4,947).
  The earlier +322% was an artifact of A never revising; gone.
- **Steady-state overhead (no Layer C) = Layer B only**: ~201 tokens/turn, ~**+25%**
  of an agent reply (~803/turn). **The proposal's "<15%" does NOT hold** (Layer B runs
  every turn) — corrected honestly; but these tokens are on a cheap aux tier and are
  fire-and-forget off the user-facing latency path.
- **Layer C is a rare event**: only **13 firings over 450 turns (≈1 per 35 turns)**,
  ~2,039 tokens each. **36 of 45 echo runs never fired the judge**; only 9 did —
  confirming "on-demand, very low frequency". And this is under the high-pressure
  setting where **every run had a planted bad skill**; normal use ≈ 0.

Honest note: overhead accounting has small ±noise from the judge's async thread
landing across run boundaries (A is credited a tiny amount of Layer C tokens for this
reason).

### 4.6 Per-module micro-metrics (deterministic, planted ground truth; scale-invariant)

| Module | Metric | Result |
|---|---|---|
| M1 trigger | precision/recall vs Hermes rule | P=1.00, R=0.67 (ties Hermes on the built-in scenarios) |
| M3 drift | precision/recall/F1 | 1.00/1.00/1.00 (small n) |
| M4 confidence | Spearman ρ | +0.67 |
| M5 retrieval | recall@k weighting uplift | 0 (this built-in case; M5's real value is §4.1/4.2) |

## 4.7 One-paragraph summary for the talk

On two published benchmarks (3 seeds each), Echo's preference memory lifts preference
**recall** 47% → 65% (PersonaMem, at ⅓ the context) and preference **adherence**
13% → 82% (PrefEval, oracle 90%). In a controlled closed-loop over 15 idiosyncratic
personas with an independent GLM evaluator (**n = 450 pairs**), Echo raises proactive
satisfaction from the baselines' ~1.3–1.5 to **4.48** — a **large effect (Cliff's δ ≈
0.85), p < 10⁻⁷²**. On error propagation, the deterministic test has Echo catching
**3/3 and 10/10** bad skills under 15% noise while the frequency-decay baseline catches
**0**; in the closed-loop, bad-task satisfaction is Echo 4.44 vs Baseline B 1.16. Two
honest costs: (1) the proposal's "<15% overhead" does not hold — Layer B runs every
turn at ~+25%, though on a cheap tier and off the latency path, and the fair agent-token
delta is only +5.3%; (2) the residual satisfaction gap is mimo's multi-constraint
instruction-following ceiling.


## 5. Reproducibility

```bash
PY=/Users/mac/.hermes/hermes-agent/venv/bin/python
# four-model connectivity
$PY -m scripts.eval.llm_clients
# third-party benchmarks
$PY -m scripts.eval.exp_personamem --limit 180
$PY -m scripts.eval.exp_prefeval  --limit 100 --pool 200
# our closed-loop experiment
$PY -m scripts.eval.exp_closedloop --turns 10 --seeds 2
# deterministic micro-metrics
$PY -m scripts.eval.run_micrometrics
# figures + stats
$PY -m scripts.eval.analyze
```

Credentials live in `~/.hermes/.env` + `~/.hermes/config.yaml` (never committed).
Benchmark data and result artifacts are git-ignored under `scripts/eval/data|results/`.
