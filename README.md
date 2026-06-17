<p align="center">
  <img src="DevPlan/Echo.png" alt="Echo" width="320">
</p>

<h1 align="center">Echo</h1>

<p align="center">
  <b>A self-improving agent that learns which of its skills actually work from how you react to them.</b>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/status-alpha-orange?style=for-the-badge" alt="Status: alpha">
  <a href="https://github.com/NousResearch/hermes-agent"><img src="https://img.shields.io/badge/forked%20from-Hermes%20v0.14.0-blueviolet?style=for-the-badge" alt="Forked from Hermes v0.14.0">
  <a href="README.zh-CN.md"><img src="https://img.shields.io/badge/Lang-中文-lightgrey?style=for-the-badge" alt="中文"></a>
</p>

> 🌐 **中文说明见 [README.zh-CN.md](README.zh-CN.md)。**

---

## What is Echo?

Echo is an agent forked from [Hermes Agent](https://github.com/NousResearch/hermes-agent)
v0.14.0. Hermes already learns: it writes skills from experience, reuses them, and keeps
memory across sessions. But that loop grades itself. The model that wrote a skill is the
same one that decides whether the skill worked; skills only get created when the agent
makes 5+ tool calls; and once a skill exists it's offered everywhere. Echo's idea is to
stop trusting the agent's opinion of its own work and use the user's reactions instead.

| Problem in Hermes | What Echo does |
|---|---|
| The model that writes a skill also grades it. | A separate model audits it, plus drift detection that uses no LLM at all. Both judge from the user's side, not the agent's. |
| Skills are only created on a fixed "5+ tool calls" rule. | Echo also triggers on save-intent phrasing, how much you edited the output, and repeated requests — and asks before creating instead of doing it silently. |
| Any skill, once made, is offered everywhere. | Echo asks where a skill applies, and the auditor can exclude it from the contexts where it does badly. |

This started as a Hermes plugin. It isn't anymore — making the above work meant changing
Hermes' core, not just hooking into it. Echo is now a standalone agent pinned to Hermes
v0.14.0; it doesn't track upstream releases. The Hermes base it's built on (multi-platform
gateway, terminal backends, MCP, cron, the skill and memory system) is still all there.

## How it works

Echo hooks the agent loop and writes its own `echo_*` tables into the SQLite DB the agent
already uses. Three signal layers feed five modules.

**Signal layers**
- **Layer A — behavioural, no LLM.** Per-skill running baselines (Welford mean/variance) over
  modification rounds, tool-call counts, and tool errors. A z-score flags drift.
- **Layer B — sentiment.** Each user turn is classified positive / negative / neutral by an
  auxiliary model, tuned to fall back to neutral when it's unsure.
- **Layer C — judge, on demand.** When a skill gets flagged, a separate model votes on whether
  it's fine, degraded, or should be excluded from a context.

**Modules**
- **M1 — skill-creation trigger.** Save-intent, complexity, edit investment, recurrence; asks before creating.
- **M2 — scope confirmation.** Asks in-conversation (via `clarify`) where a skill should apply.
- **M3 — judge & exclusion.** The Layer C auditor, plus injecting exclusion conditions back into the agent.
- **M4 — confidence engine.** A decay state machine: `active → pending_review → retired`.
- **M5 — preference RAG.** A consolidated preference profile plus example retrieval, weighted by skill confidence.

## Surfaces

Echo runs on the same surfaces as Hermes, plus a native app:

- **CLI / TUI** — chat with the teal Echo skin; signals collected in the background.
- **Web dashboard** — an `/echo` page: confidence ranking, status distribution, the
  skill-candidate queue, the preference library, and an in-chat rating widget.
- **Native macOS app** (`desktop/Echo/`) — a SwiftUI front end that spawns the gateway as a
  subprocess and captures signals the browser can't see (clipboard, window focus).

## Quick start

Everything goes through one launcher at the repo root:

```bash
./echo chat      # CLI chat — signals collected in the background
./echo tui       # full-screen TUI
./echo dash      # web dashboard (opens the browser to /echo)
./echo app       # native macOS app
./echo verify    # run the test suite + end-to-end smoke check
./echo --help    # the rest
```

Models, providers, and API keys are configured the same way as in Hermes — see the
[Hermes docs](https://hermes-agent.nousresearch.com/docs/). Echo adds one extra setup step
for the optional auditor model.

## Repo layout

Most of the tree is the Hermes codebase Echo is built on. Echo's own code is mostly here:

| Path | What |
|---|---|
| [`plugins/echo_signals/`](plugins/echo_signals/) | the bulk of Echo — schema, hooks, signal collection, all five modules |
| [`tests/plugins/echo_signals/`](tests/plugins/echo_signals/) | unit tests |
| [`desktop/Echo/`](desktop/Echo/) | the native macOS app |
| [`scripts/eval/`](scripts/eval/) | evaluation harness and metric scripts |
| [`DevPlan/`](DevPlan/) | the proposal, schema spec, design docs, and experiment report |
| [`docs/hermes-architecture.html`](docs/hermes-architecture.html) | a reading aid for the Hermes internals |

Echo also edits Hermes itself in a few places (the gateway and the web/TUI front ends), so
this is a fork — it won't diff cleanly against upstream and isn't meant to merge back. The
rest of the tree is Hermes, included under its MIT license so the project runs on its own.

## Evaluation

To avoid the agent grading itself, the evaluation uses four separate models — one for the
simulated user, one as an independent scorer, one as the agent under test, and Echo's own
signal models. It runs against two public preference benchmarks
([PersonaMem](https://huggingface.co/datasets/bowen-upenn/PersonaMem) and
[PrefEval](https://huggingface.co/datasets/siyanzhao/prefeval_explicit)) and a simulated-user
closed loop. The full method and numbers — including where the original cost target wasn't met
— are in [`DevPlan/experiment-report.md`](DevPlan/experiment-report.md) (English) and
[`experiment-report-zh.md`](DevPlan/experiment-report-zh.md) (中文).

## Credits & license

Echo is forked from **[Hermes Agent](https://github.com/NousResearch/hermes-agent)** by
[Nous Research](https://nousresearch.com). The whole base — gateway, terminal backends, MCP,
cron, and the skill/memory system Echo builds on — comes from there.

Echo is developed by Lingchao Nie, Fanghui Xu, and Yuing Zhou at Westlake University.

MIT licensed — see [LICENSE](LICENSE). Copyright © 2025 Nous Research; modifications and
derivative work © 2026 the Echo authors.
