<p align="center">
  <img src="DevPlan/Echo.png" alt="Echo" width="320">
</p>

<h1 align="center">Echo</h1>

<p align="center">
  <b>A user-signal-driven skill-lifecycle layer for the <a href="https://github.com/NousResearch/hermes-agent">Hermes Agent</a>.</b>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/status-alpha-orange?style=for-the-badge" alt="Status: alpha">
  <a href="https://github.com/NousResearch/hermes-agent"><img src="https://img.shields.io/badge/built%20on-Hermes%20v0.14.0-blueviolet?style=for-the-badge" alt="Built on Hermes">
  <a href="README.zh-CN.md"><img src="https://img.shields.io/badge/Lang-中文-lightgrey?style=for-the-badge" alt="中文"></a>
</p>

> 🌐 **中文说明见 [README.zh-CN.md](README.zh-CN.md)。**

---

## What is Echo?

Hermes Agent has a *closed* learning loop: it creates skills from experience, refines
them in use, and persists knowledge across sessions. Echo is a research project that
identifies three documented weaknesses in that loop and closes them with a thin
plugin layer driven by **user behavioural signals** rather than the agent's own
self-assessment.

| Hermes weakness | Echo's fix |
|---|---|
| **Same-source self-evaluation bias** — the same model that writes a skill also judges whether it worked. | An **independent LLM auditor** (a separately-configured model) plus zero-LLM behavioural drift detection score skills from the *user's* side. |
| **Narrow skill-creation trigger** — skills are only born from the hard-coded "≥5 tool calls" heuristic. | An **adaptive trigger** that also reacts to save-intent language, modification investment, and recurring requests — and *asks* the user before creating, rather than firing silently. |
| **No applicability boundaries** — a skill, once made, is offered everywhere. | **Scope confirmation** + **exclusion conditions**: Echo asks in-conversation where a skill should apply, and the auditor can fence it off from contexts where it underperforms. |

Echo is **not** a fork that aims to merge upstream. It is a long-term secondary-development
project that keeps the four Hermes core files untouched and reaches into the agent only
through the existing plugin/hook interface, so the "what did Echo add?" diff stays clean.

## How it works

Echo observes Hermes through lifecycle hooks and writes its own `echo_*` tables into the
same SQLite database. Three signal layers feed five modules:

**Signal layers**
- **Layer A — behavioural (zero-LLM).** Per-skill online baselines (Welford mean/variance)
  over modification rounds, tool-call counts and tool errors; a z-score test flags drift.
- **Layer B — natural-language sentiment.** Each user turn is classified (positive /
  negative / neutral) by an auxiliary model, biased conservatively toward `neutral`.
- **Layer C — on-demand judge.** When a skill is flagged for review, an independent
  auditor multi-votes on whether it is `ok`, `degraded`, or should be `excluded` from a
  context.

**Modules**
- **M1 — adaptive skill-creation trigger** (save-intent, complexity, modification, recurrence; asks before creating)
- **M2 — scope confirmation** (in-conversation `clarify` with concrete applicability options)
- **M3 — judge & exclusion** (Layer C auditor + exclusion-condition injection)
- **M4 — confidence engine** (decay state machine: `active → pending_review → retired`)
- **M5 — preference RAG** (consolidated preference profile + neural-embedding example retrieval, confidence-weighted)

## Surfaces

Echo rides on every Hermes surface, plus a native app:

- **CLI / TUI** — Hermes chat with the sonar-teal *Echo* skin; signals collected in the background.
- **Web Dashboard** — an `/echo` plugin page: confidence ranking, status distribution,
  candidate queue, preference library, and an in-chat rating widget.
- **Native macOS app** (`desktop/Echo/`) — a SwiftUI Liquid-Glass front end that spawns the
  gateway as a stdio subprocess and captures OS-level signals (clipboard, window focus).

## Quick start

Echo runs through a single launcher at the repo root:

```bash
./echo chat      # Hermes CLI chat — Echo collects signals in the background
./echo tui       # full-screen TUI
./echo dash      # Web Dashboard (browser opens to /echo)
./echo app       # native macOS app (live backend)
./echo verify    # run the Echo test suite + end-to-end smoke check
./echo --help    # every flavor
```

The underlying Hermes install, model providers, and API keys are configured exactly as in
upstream Hermes — see the [Hermes docs](https://hermes-agent.nousresearch.com/docs/).
Echo adds its own first-run setup step for the optional independent-auditor model.

## Where Echo's code lives

A clean `git diff upstream/main` shows only these paths (plus `.gitignore`, `LICENSE`, `README*`, `CLAUDE.md`):

| Path | What |
|---|---|
| [`plugins/echo_signals/`](plugins/echo_signals/) | the plugin proper — schema, hooks, signal collection, all five modules |
| [`tests/plugins/echo_signals/`](tests/plugins/echo_signals/) | unit tests |
| [`desktop/Echo/`](desktop/Echo/) | native macOS SwiftUI app |
| [`scripts/eval/`](scripts/eval/) | evaluation harness + metric scripts |
| [`DevPlan/`](DevPlan/) | the research proposal, schema spec, design docs, experiment report |
| [`docs/hermes-architecture.html`](docs/hermes-architecture.html) | onboarding aid |

Everything else in the tree is **Hermes upstream**, included under its MIT license so the
project runs standalone.

## Evaluation

Echo is evaluated with four-model isolation (separate models for the persona/simulated user,
the independent evaluator, the agent under test, and Echo's own signal models) to avoid
self-evaluation circularity, against public preference benchmarks
([PersonaMem](https://huggingface.co/datasets/bowen-upenn/PersonaMem),
[PrefEval](https://huggingface.co/datasets/siyanzhao/prefeval_explicit)) and a simulated-user
closed loop. See [`DevPlan/experiment-report.md`](DevPlan/experiment-report.md) (English) /
[`experiment-report-zh.md`](DevPlan/experiment-report-zh.md) (中文) for the full method,
results, and an honest account of where the design's original cost target was *not* met.

## Credits & license

Echo is built on **[Hermes Agent](https://github.com/NousResearch/hermes-agent)** by
[Nous Research](https://nousresearch.com). All of Hermes' capabilities — multi-platform
gateway, terminal backends, MCP, cron, and the skill/memory system Echo extends — come from
the upstream project.

Echo (the skill-lifecycle layer and its research) is developed by Lingchao Nie, Fanghui Xu,
and Yuing Zhou at Westlake University.

Licensed under the **MIT License** — see [LICENSE](LICENSE).
Copyright © 2025 Nous Research; modifications and derivative work © 2026 the Echo authors.
