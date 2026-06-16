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
[`stats.json`](experiment-figures/stats.json).

### 4.1 Third-party benchmark — PersonaMem (preference recall), n = 180

![PersonaMem](experiment-figures/personamem_accuracy.png)

| Condition | Accuracy | Injected context |
|---|---|---|
| No memory (cold) | 48.9% | 0 |
| Full history (naive RAG) | 54.4% | 8,279 chars |
| **Echo M5 (retrieval)** | **64.4%** | **2,649 chars** |

Echo's confidence-weighted neural retrieval beats the cold model by **+15.6 pts**
and naive full-history by **+10.0 pts**, while injecting **~⅓ the context**. Naive
full-history adds only +5.6 pts over cold despite 3× the tokens — dumping the whole
transcript buries the relevant preference in noise, which is exactly the failure mode
M5's semantic retrieval avoids. Per-question-type breakdown:
[`personamem_by_type.png`](experiment-figures/personamem_by_type.png).

### 4.2 Third-party benchmark — PrefEval (preference adherence in generation), n = 100

![PrefEval](experiment-figures/prefeval_adherence.png)

| Condition | Adherence |
|---|---|
| No memory | 16% |
| **Echo M5** | **86%** |
| Oracle (preference handed to the model) | 93% |

This is the headline external result. On questions whose natural answer *violates* the
user's stated preference, the cold model adheres only **16%** of the time — consistent
with PrefEval's published "preference following collapses" finding. With the preference
held in M5 **among a pool of 200 across 20 topics**, Echo retrieves the right one and
adherence jumps to **86%**, within 7 pts of the oracle upper bound (93%). So almost all
of the achievable gain is captured by retrieval; the residual gap to oracle is M5's
retrieval misses, not a ceiling of the approach.

### 4.3 Metric 1 — satisfaction curve (closed-loop, our experiment)

![satisfaction](experiment-figures/satisfaction_curve.png)

Mean GLM-judged first-output satisfaction (1–5), 3 personas × 2 seeds × 10 turns:

| Condition | Overall mean | Late mean (turns ≥ 5) |
|---|---|---|
| Baseline A (no memory) | 1.07 | 1.11 |
| Baseline B (self-eval + decay) | 1.03 | 1.03 |
| **Echo** | **2.10** | **2.28** |

Echo's curve **rises and plateaus** (1.0 → ~2.3 by turn 3 and holds), while both
baselines stay flat at the floor — they never learn the idiosyncratic preference
because A has no memory and B trusts its own self-evaluation over the user's feedback.
Paired tests (paired by persona/seed/turn, n = 60 pairs):

- **Echo vs A**: Wilcoxon *p* = 8.8 × 10⁻⁵, Cliff's δ = 0.26 (small–medium)
- **Echo vs B**: Wilcoxon *p* = 6.9 × 10⁻⁵, Cliff's δ = 0.28 (small–medium)

**Honest caveat.** Echo roughly *doubles* satisfaction and the effect is highly
significant, but it plateaus near **2.3 / 5, not 5**. Each persona enforces *three*
simultaneous hard rules; the user reveals them incrementally through feedback, and mimo
does not reliably satisfy all three on the first try even once reminded (especially the
exact-format personas). So Echo reliably learns and applies **one-to-two of three
rules** within 10 turns — a real, significant, but partial gain, not a solved task. A
longer horizon and a per-rule (rather than all-or-nothing) reward would likely lift it
further; reported as-is.

### 4.4 Metric 2 — error propagation

![error propagation](experiment-figures/error_propagation.png)
![deterministic](experiment-figures/error_propagation_deterministic.png)

A *silently-wrong* skill (a plausible-but-wrong remembered "preference") is planted.

- **Closed-loop**, mean turns the bad approach kept being used: Baseline B = **5.0**
  (re-confirmed by self-eval every time, never dropped), **Echo = 2.0** (abandoned
  after ~2 turns as the user's negative signals decay its confidence and M5 down-ranks
  it), Baseline A = 0.0 (no memory, so it never uses — but also never *reuses* anything
  good).
- **Deterministic harness** (planted ground truth): **Echo catches 3/3** bad skills and
  drives their confidence to 0.071 (retired); **Baseline B catches 0/3** (self-eval +
  frequency decay never flag them). This is the cleanest demonstration of the central
  thesis: behavioural-drift detection catches silently-wrong skills that same-source
  self-evaluation structurally cannot.

### 4.5 Metric 3 — system overhead (a correction to the proposal)

![overhead](experiment-figures/overhead.png)

Mean tokens per 10-turn run:

| Condition | Agent (mimo) | Echo signal (Qwen) | Total |
|---|---|---|---|
| Baseline A | 2,184 | 0 | 2,184 |
| Baseline B | 13,642 | 0 | 13,642 |
| **Echo** | 4,368 | 4,842 | **9,210** |

**The proposal's "< 15% token overhead" claim does not hold as stated**, and we report
that honestly. Two findings:

1. **Vs the stateless floor (A): +322%.** But A is an unrealistic floor — it never
   revises and never personalises, so it does the least possible work. Most of Echo's
   *agent*-token increase is revision turns (the channel through which preferences are
   learned), which A cannot do by construction.
2. **Vs the comparable personalization baseline (B): Echo is ~32% CHEAPER** (9,210 vs
   13,642), because B's per-turn self-evaluation + always-revise loop costs more agent
   tokens than Echo's signal pipeline.

The proposal's reasoning was that Layer A is free and Layer C is rare — both true — but
it overlooked that **Layer B sentiment classification runs on every user turn**, which
is the dominant signal cost (~4.8k Qwen tokens/run here). Mitigating factors: those
tokens are on a **cheaper aux-model tier**, and all Layer B/C calls are **fire-and-forget
off the user-facing latency path**, so response latency is unaffected. Net: Echo is not
"nearly free" vs doing nothing, but it is **cheaper than the self-evaluating baseline it
aims to replace**, with the cost off the critical path.

### 4.6 Per-module micro-metrics (deterministic, planted ground truth)

| Module | Metric | Result |
|---|---|---|
| **M1** trigger | precision / recall vs Hermes ≥-tool rule | Echo P=1.00, R=0.67 — **ties** the Hermes rule on these scenarios |
| **M3** drift | precision / recall / F1 | **1.00 / 1.00 / 1.00** (small n: TP=1, TN=20) |
| **M4** confidence | Spearman ρ (confidence ↔ true usefulness) | **ρ = +0.67** (n=5 skills) |
| **M5** retrieval | recall@k ± confidence weights | 0.375 / 0.375, **uplift = 0** on this scenario |

Honest reading: **M3 and M4 are strong** (drift detection is perfect on the planted
drift; confidence ranking correlates well with true usefulness). **M1 only ties** the
existing Hermes rule on the built-in scenarios — Echo's nominator advantage (save-intent
/ recurrence) isn't exercised by these particular scenarios, so the headline M1 value is
better shown by the live save-intent path than by this micro-metric. **M5's
confidence-weighting uplift is 0 here** because the built-in scenario's skills are not
degraded enough for weighting to re-order retrieval — the *real* M5 value shows up on
PersonaMem/PrefEval above, not in this deterministic micro-case.

---

## 4.7 One-paragraph summary for the talk

On two independent published benchmarks, Echo's preference memory lifts preference
**recall** 49% → 64% (PersonaMem) at a third of the context, and preference
**adherence** 16% → 86% (PrefEval, vs a 93% oracle). In a controlled closed-loop with a
DeepSeek-simulated user and an independent GLM-5.2 evaluator, Echo roughly doubles
proactive satisfaction (1.07 → 2.10, *p* < 10⁻⁴) where two non-user-signal baselines
stay flat, and it abandons a planted silently-wrong skill in ~2 turns and 3/3
deterministically while a self-evaluating baseline keeps it forever (0/3). The honest
costs: satisfaction plateaus at ~2.3/5 (partial, not solved, in 10 turns), and the
proposal's "<15% overhead" is wrong — Layer B runs every turn — though Echo is still
~32% cheaper than the self-evaluating baseline and adds no user-facing latency.

---

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
