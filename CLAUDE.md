# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

This is **Echo** — a research-flavored secondary-development project built on top of [Hermes Agent](https://github.com/NousResearch/hermes-agent) v0.14.0 by Nous Research. Echo adds a user-signal-driven skill lifecycle management layer to address three documented defects in Hermes' closed learning loop (same-source self-evaluation bias, narrow skill-creation triggers, missing skill applicability boundaries). Project owner: Lingchao Nie + 2 teammates, Westlake University.

**This is NOT a fork that aims to merge upstream.** The maintainer's stated goal is to make Echo useful as a long-term project, not to contribute back to Hermes. Optimize for *Echo's* design coherence and ease of long-term evolution, not for upstream-PR-ability.

## Workflow rules (set by the maintainer, must follow)

1. **Commit after every completed step.** Use the task granularity reflected in the current todo list (e.g. "Step 1: SQLite schema", "Step 2: plugin hooks") as a commit boundary, not finer. Always present the commit diff before running `git commit` so the maintainer can inspect.
2. **Ask before deciding on anything not fully certain.** If a design choice has more than one reasonable answer and the maintainer hasn't picked, surface the options via `AskUserQuestion` rather than picking one and hoping. Examples that should be questions, not assumptions: choice of dependency, schema field naming when ambiguous, UI/UX decisions, how a Hermes-side hook should be invoked when multiple are candidates, what to do when an Echo design choice conflicts with a Hermes convention.
3. **Default communication language: Chinese (中文).** The maintainer writes in Chinese; reply in Chinese unless they switch.

## Where to look first

The repository already contains substantial design documentation. Read before guessing:

| Topic | File |
|---|---|
| **Hermes internal architecture & conventions** (the *most* authoritative reference) | [AGENTS.md](AGENTS.md) — 51KB, official |
| **Echo's research design** (modules M1-M5, motivation, evaluation) | [DevPlan/proposal.tex](DevPlan/proposal.tex) |
| **Echo's SQLite schema design** | [DevPlan/schema.md](DevPlan/schema.md) |
| **Per-platform signal observability matrix** (what Layer A can actually measure on each surface) | [DevPlan/signal-matrix.md](DevPlan/signal-matrix.md) |
| **Hermes architecture visual overview** (15 sections, browser-readable) | [docs/hermes-architecture.html](docs/hermes-architecture.html) |
| **Cross-session memory about user/project** | `~/.claude/projects/-Users-mac-code-Echo/memory/` |

## Echo's code lives in three places (everything else is Hermes upstream)

- `plugins/echo_signals/` — the plugin proper (schema, hooks, signal collection)
- `DevPlan/` — design docs, the proposal, schema specs
- `tests/plugins/echo_signals/` — unit tests
- `docs/hermes-architecture.html` — onboarding aid

A clean `git diff upstream/main` should show only files under these paths plus `.gitignore`, `LICENSE`, and `CLAUDE.md`. If you find yourself wanting to edit a Hermes core file, **stop and ask** — see the hard constraints below.

## Hard constraints (inherited from Hermes; violating them creates real costs)

- **Never modify the four core files**: [run_agent.py](run_agent.py), [cli.py](cli.py), [gateway/run.py](gateway/run.py), [hermes_cli/main.py](hermes_cli/main.py). All Echo features must reach into Hermes through the existing plugin/hook interface ([plugins/observability/langfuse/__init__.py:995](plugins/observability/langfuse/__init__.py#L995) is the cleanest example to model on). Touching these files will silently break upstream-sync hygiene and the project's "what did Echo actually add?" diff story.
- **Never break prompt caching.** Mutations that would alter past system context mid-conversation (e.g. adding skill exclusion conditions) must default to **deferred-until-next-session** semantics. Add an explicit `--now` flag only when the maintainer asks for immediate invalidation.
- **Never hard-code `~/.hermes` paths.** Use `from hermes_constants import get_hermes_home, display_hermes_home`. Profile support depends on this.
- **Never write to `~/.hermes` in tests.** The `_isolate_hermes_home` autouse fixture redirects to a temp dir.
- **No `simple_term_menu`** — known ghost-duplication bug in tmux/iTerm2. Use `hermes_cli/curses_ui.py`.

## Running tests

The official runner is `scripts/run_tests.sh`, which assumes a `.venv` under the repo root with `[all,dev]` extras installed. The pyproject's `addopts` enforces `-n auto --timeout=30 --timeout-method=signal`, which requires `pytest-xdist` and `pytest-timeout`.

**If those aren't installed in the active Python (e.g. the system / conda env)**, override the addopts:

```bash
python3 -m pytest tests/plugins/echo_signals/ -o addopts="" -v
```

Run only Echo tests + a couple of Hermes plugin-discovery tests as a regression sanity check after changes:

```bash
python3 -m pytest tests/plugins/echo_signals/ \
                  tests/providers/test_plugin_discovery.py \
                  tests/hermes_cli/test_plugins_cmd.py \
                  -o addopts="" -v
```

Echo schema tests are deliberately fast (in-memory SQLite, <2s total) so they can be run on every change.

## Architecture in one minute

Hermes is three user-facing surfaces (CLI, TUI, multi-platform Gateway) all funneling through `AIAgent.run_conversation()` in `run_agent.py`. State lives in a single SQLite DB at `get_hermes_home() / "sessions.db"`. Skills are folder-based with `SKILL.md` frontmatter; the `≥5 tool call` skill-creation trigger is **guidance to the curator, not enforced in code**. Extension is via plugins that expose `def register(ctx)` and call `ctx.register_hook(...)`. See [docs/hermes-architecture.html](docs/hermes-architecture.html) for the diagram.

**Echo's wedge point:** Layer A behavioral signals collected via `pre_llm_call` / `post_llm_call` / `on_session_*` hooks → written to Echo's own tables (`echo_*`) in the same SQLite DB → consumed by a confidence-decay engine (M4) → surfaced via a web plugin in Hermes' Dashboard (Phase 1) and eventually a Tauri desktop shell with clipboard access (Phase 1, see [memory feedback_ui_strategy](~/.claude/projects/-Users-mac-code-Echo/memory/feedback_ui_strategy.md)).

## Phase 1 status (update this section as steps complete)

- ✅ **Step 1**: SQLite schema — [plugins/echo_signals/schema.py](plugins/echo_signals/schema.py), 15 unit tests, zero regressions on Hermes plugin discovery tests.
- ✅ **Step 2**: Plugin wired into Hermes lifecycle. Monkey-patch over `tools.skill_usage.bump_use` writes `echo_skill_invocation` rows attributed to the active session (carried via contextvars from `on_session_start` hook). Lazy DB connection in [plugins/echo_signals/db.py](plugins/echo_signals/db.py) — schema is created on first hook fire, not at plugin load. 38 Echo tests + 69 Hermes plugin discovery tests, all passing.
- ✅ **Step 3**: Layer A signal collection wired up. Three event types written to `echo_signal_event`:
   - `user_turn` — one row per `pre_llm_call` with `turn_type='user'`. `modification_round_count` is `COUNT(*)` over this.
   - `tool_call` — one row per `post_tool_call`, with `value_text=tool_name`. Success/error parsing deferred to Step 4.
   - `session_ended` — one row at `on_session_end` while a skill was still active.
   Last-skill-wins attribution via `_current_invocation_id` contextvar (set by `bump_use` wrapper, read by each collector). 51 Echo tests + 69 Hermes regression, all passing.
- ⬜ **Step 4**: Confidence-update logic (M4) reading the signal stream.
- ⬜ **Step 5**: Web Dashboard plugin under `web/src/plugins/echo/`.
- ⬜ **Step 6**: Tauri shell wrapping the Dashboard, exposing clipboard + window-focus IPC.

(Update this list when steps move state — the file is committed and serves as a living changelog for the project.)

## Upstream sync

`upstream` remote points at `https://github.com/NousResearch/hermes-agent.git`. Tags are fetched. Since the four core files (and the plugin interface itself) are untouched, `git merge upstream/<tag>` should cleanly apply for most upstream changes. If a merge conflict reaches into `plugins/echo_signals/` it's almost certainly a real semantic conflict worth surfacing to the maintainer rather than auto-resolving.
