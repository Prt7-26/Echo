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

## Echo's code lives in five places (everything else is Hermes upstream)

- `plugins/echo_signals/` — the plugin proper (schema, hooks, signal collection)
- `DevPlan/` — design docs, the proposal, schema specs
- `tests/plugins/echo_signals/` — unit tests
- `docs/hermes-architecture.html` — onboarding aid
- `tauri-shell/` — Rust/Tauri desktop wrapper around the dashboard (Step 17)
- `scripts/verify_echo.py` — end-to-end smoke check

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

## Launching the agent

Use the `./echo` launcher at the repo root — one entry point for every Hermes + Echo flavor. Run `./echo --help` for the full list.

| Command | What it does |
|---|---|
| `./echo chat [args]` | Hermes CLI chat (Echo collects signals in background). Extra args forwarded (`--resume`, `--continue`, etc.). |
| `./echo tui [args]` | Hermes full-screen TUI. |
| `./echo dash [args]` | Hermes Web Dashboard (foreground). Browser auto-opens to `/echo`. |
| `./echo tauri` | Tauri desktop shell. Auto-starts the dashboard in the background if not already up. Needs Rust toolchain + node. |
| `./echo full` | Dashboard in background + Tauri shell in foreground; stops the background dashboard on exit. |
| `./echo verify` | Runs `pytest tests/plugins/echo_signals/` + `scripts/verify_echo.py`. The same health check used during development. |
| `./echo status` / `./echo stop` | Inspect / stop the background dashboard. |

Env vars: `ECHO_DASH_HOST` (default `127.0.0.1`), `ECHO_DASH_PORT` (default `9119`). Background dashboard logs to `$TMPDIR/echo-dashboard.log`, PID in `$TMPDIR/echo-dashboard.pid`.

## Architecture in one minute

Hermes is three user-facing surfaces (CLI, TUI, multi-platform Gateway) all funneling through `AIAgent.run_conversation()` in `run_agent.py`. State lives in a single SQLite DB at `get_hermes_home() / "sessions.db"`. Skills are folder-based with `SKILL.md` frontmatter; the `≥5 tool call` skill-creation trigger is **guidance to the curator, not enforced in code**. Extension is via plugins that expose `def register(ctx)` and call `ctx.register_hook(...)`. See [docs/hermes-architecture.html](docs/hermes-architecture.html) for the diagram.

**Echo's wedge point:** Layer A behavioral signals collected via `pre_llm_call` / `post_llm_call` / `on_session_*` hooks → written to Echo's own tables (`echo_*`) in the same SQLite DB → consumed by a confidence-decay engine (M4) → surfaced via a web plugin in Hermes' Dashboard (Phase 1) and eventually a Tauri desktop shell with clipboard access (Phase 1, see [memory feedback_ui_strategy](~/.claude/projects/-Users-mac-code-Echo/memory/feedback_ui_strategy.md)).

## Phase 1 status (update this section as steps complete)

- ✅ **Step 1**: SQLite schema — [plugins/echo_signals/schema.py](plugins/echo_signals/schema.py), 15 unit tests, zero regressions on Hermes plugin discovery tests.
- ✅ **Step 2**: Plugin wired into Hermes lifecycle. Monkey-patch over `tools.skill_usage.bump_use` writes `echo_skill_invocation` rows attributed to the active session (carried via contextvars from `on_session_start` hook). Lazy DB connection in [plugins/echo_signals/db.py](plugins/echo_signals/db.py) — schema is created on first hook fire, not at plugin load. 38 Echo tests + 69 Hermes plugin discovery tests, all passing.
- ✅ **Step 3**: Layer A signal collection wired up. Three event types written to `echo_signal_event`:
   - `user_turn` — one row per `pre_llm_call` with `turn_type='user'`. `modification_round_count` is `COUNT(*)` over this.
   - `tool_call` — one row per `post_tool_call`, with `value_text=tool_name`. Success/error parsing deferred.
   - `session_ended` — one row at `on_session_end` while a skill was still active.
   Last-skill-wins attribution via `_current_invocation_id` contextvar (set by `bump_use` wrapper, read by each collector). 51 Echo tests + 69 Hermes regression, all passing.
- ✅ **Step 4**: M4 confidence-update engine landed as a pure logic layer in [plugins/echo_signals/confidence.py](plugins/echo_signals/confidence.py). Implements all five proposal rules (explicit/NL positive, explicit/NL negative, drift-detected, silence) and the active→pending_review→retired state machine. **NOT wired into the signal-collection path yet** — current raw signals describe activity, not quality, so calling `update_confidence()` from `signals.py` would conflate the two. Wiring waits until Layer B (NL feedback classifier) and Layer A drift detection exist. 81 Echo tests + 69 Hermes regression, all passing.
- ✅ **End-to-end verification**: [scripts/verify_echo.py](scripts/verify_echo.py) runs the plugin against real Hermes runtime objects (PluginManager scan, real `SessionDB`, real `tools.skill_usage` monkey-patch) — 21/21 checks pass. Discovered and fixed two issues: (1) missing `plugin.yaml` manifest (Hermes' discovery requires it); (2) manifest `name:` field was `echo-signals` but Python module is `echo_signals` — Hermes' flat-plugin `key` derivation uses the manifest `name`, so the key shown by `hermes plugins list` would have been inconsistent with the import path. Both fixed.
- ⬜ **Step 4**: Confidence-update logic (M4) reading the signal stream.
- ✅ **Step 5**: Web Dashboard plugin landed.
  - **5a**: Backend dashboard API — [plugins/echo_signals/dashboard/manifest.json](plugins/echo_signals/dashboard/manifest.json) + [plugins/echo_signals/dashboard/plugin_api.py](plugins/echo_signals/dashboard/plugin_api.py). Five REST endpoints (`GET /skills`, `GET /skills/{id}/timeline`, `GET /status-distribution`, `GET /invocations/recent`, `POST /feedback`) auto-mounted by Hermes' `_mount_plugin_api_routes()` at `/api/plugins/echo_signals/*`. 22 endpoint tests. **Requires `fastapi` + `httpx` in the test env** (Hermes `[all]` extras — install with `pip install fastapi httpx` if missing).
  - **5b+5c**: Frontend bundle [plugins/echo_signals/dashboard/dist/index.js](plugins/echo_signals/dashboard/dist/index.js) — hand-written IIFE, no build step. Consumes the SDK at `window.__HERMES_PLUGIN_SDK__`. Implements four widgets on the `/echo` top-level page: confidence ranking (worst-first, click-to-drill), status distribution (stacked-bar substitute for a chart lib), recent invocations table, per-skill timeline. ~470 lines.
  - **5d**: ChatPage `chat:bottom` thumbs widget — same bundle, registered via `registerSlot`. Polls the most-recent invocation every 5s. Two-tier interaction: tap-to-submit thumbs ±1; long-press (≥500ms) opens a textarea for an optional reason that gets POSTed alongside the rating.
  - **5e**: [scripts/verify_echo.py](scripts/verify_echo.py) extended — Hermes' `_discover_dashboard_plugins()` now reports finding `echo_signals` alongside `example`, `hermes-achievements`, `kanban`. 34/34 checks pass.
- ✅ **Step 6**: Layer A drift detection in [plugins/echo_signals/baseline.py](plugins/echo_signals/baseline.py). Welford's online (mean, variance) per (skill, metric) for two metrics — `modification_round_count` and `tool_call_count`. Cold-start guard at n < `N_WARM = 20` (only accumulate). Once `baseline_ready` flips, each new invocation gets a z-score against the *prior* baseline; `|z| ≥ 2.0` emits a `DriftEvent` and feeds `drift_detected` into the confidence engine with severity capped at `3.0`. Baseline keeps updating after drift so genuine workflow shifts get absorbed. Two trigger points: (a) `bump_use` switching skill finalizes the prior invocation; (b) `on_session_end` finalizes the current one. Both routes use the same idempotent `finalize_invocation(invocation_id)` (gated by `echo_skill_invocation.finished_at`). 22 new tests; 125 Echo + 69 Hermes regression all pass.
- ✅ **Step 7**: Layer B NL sentiment classifier landed in [plugins/echo_signals/nl_classifier.py](plugins/echo_signals/nl_classifier.py). Each `pre_llm_call` (turn_type='user') now fires a fire-and-forget daemon thread that runs the user's message through Hermes' auxiliary LLM (`task="echo_classifier"`) with a deliberately conservative prompt that biases ambiguous replies toward `neutral`. Non-neutral labels invoke `confidence.update_confidence(..., "nl_positive"/"nl_negative")` and append a Layer B row to `echo_signal_event`. `skill_id` is pinned at hook time so a later skill switch can't misattribute. Sacred invariant preserved: `neutral` / `classify()` failure / no auxiliary configured → no confidence movement. Tests use `nl_classifier.set_classifier_impl(...)` for deterministic injection. 24 new tests; 149 Echo + 69 Hermes regression all pass.
- ✅ **Step 8**: Layer C judge + signal-event wrapper. Two new modules:
  - [plugins/echo_signals/judge.py](plugins/echo_signals/judge.py): independent LLM auditor (`task="echo_judge"`, fire-and-forget daemon thread). Returns one of three verdicts — `ok` (no-op), `degraded` (apply another `drift_detected severity=2.0`), or `exclusion` (append context to `echo_skill_scope.exclusion_conditions`, JSON array deduped). Tolerant JSON parser handles code-fence / prose-padded responses. Test-injectable via `set_judge_impl`.
  - [plugins/echo_signals/confidence_actions.py](plugins/echo_signals/confidence_actions.py): thin `apply_signal_event(skill_id, event, severity)` wrapper around `update_confidence` that fires the judge only on `active → pending_review` transitions (the canonical "needs review" gate; pending→retired or active→active never re-fire). Migrated `baseline.finalize_invocation`, `signals.on_pre_llm_call` (NL callback), and `dashboard/plugin_api.py` (`POST /feedback`) onto this wrapper. Pure `update_confidence` stays untouched so its 30 unit tests don't need to know about the LLM.
  - Test infrastructure: [tests/plugins/echo_signals/conftest.py](tests/plugins/echo_signals/conftest.py) autouse-stubs `start_judge_async` so incidental `pending_review` transitions don't spawn real LLM threads at test time. The handful of tests that exercise the judge lifecycle proper opt out via a `real_judge` marker fixture. 23 new judge tests; 172 Echo + 69 Hermes regression all pass.
- ✅ **Steps 9 + 10 (M2 scope confirmation)**: Surveyed Hermes' skill-creation path first (no `pre/post_skill_create` hook exists; SKILL.md is written by [tools/skill_manager_tool.py:407](tools/skill_manager_tool.py#L407) without any `invoke_hook` calls). Wedge point: `post_tool_call` filtered to `tool_name=='skill_manage' AND args.action=='create'`.
  - **Step 9 (backend)**: [plugins/echo_signals/scope_dialog.py](plugins/echo_signals/scope_dialog.py) — `on_post_tool_call` handler writes a `scope_level='unknown'` row into `echo_skill_scope` whenever Hermes creates a skill (idempotent: re-create won't clobber a user's prior choice). Also seeds an `echo_skill_confidence` anchor so the new skill appears in the dashboard ranking before its first `bump_use`. Dashboard API gains `GET /scope/pending` and `POST /scope`. 27 new tests.
  - **Step 10 (frontend)**: Dashboard bundle gains a `ScopeQuestion` widget that takes priority over `ThumbsBar` in the `chat:bottom` slot — when there's a pending scope row, the user sees the binary `A · reuse the whole approach / B · only the general idea` question instead of thumbs. Local "answered this session" set prevents the question from flashing back during the poll cycle.
- ✅ **Steps 11 + 12 (M5 preference RAG)**: M5 end-to-end live.
  - **Step 11 (storage + retrieval)**: [plugins/echo_signals/preference_rag.py](plugins/echo_signals/preference_rag.py) — pure-stdlib hashing embedding (256-dim SHA-256 buckets, no numpy), float32 BLOB pack/unpack, `store_preference()` + capacity-bounded LRU eviction by composite_score (rating × time-decay × use_count), `retrieve_topk()` with two-stage pipeline (cosine candidate pool → MMR re-rank). M4↔M5 coupling: `confidence_weights` argument multiplies each candidate's relevance by its skill's confidence, so degraded skills' examples naturally downrank. Schema bumped to v2 with new ephemeral `echo_turn_cache` table.
  - **Step 12 (hooks + /feedback wire-up)**: `on_post_llm_call_cache` upserts each turn's `(user_message, assistant_response, skill_id)` into `echo_turn_cache` keyed by `session_id`. `on_pre_llm_call_inject` retrieves top-3 examples via cosine+MMR with confidence weights, returns `{"context": ...}` which Hermes appends to the user message (system prompt untouched — cache-safe per [conversation_loop.py:495-522](agent/conversation_loop.py#L495)). Dashboard `POST /feedback` with `rating=+1` reads `echo_turn_cache` by skill_id → calls `store_preference(rating=5 if reason else 4)`. Total Echo hook surface now 5 channels (`on_session_start/end`, `pre_llm_call` ×2, `post_llm_call`, `post_tool_call` ×2). 77 new tests (57 unit + 20 integration). 262 Echo + 69 Hermes regression all pass.
- ✅ **Step 13 (M1 adaptive trigger — 3/4 conditions)**: [plugins/echo_signals/m1_trigger.py](plugins/echo_signals/m1_trigger.py). Echo nominates invocations as skill-worthy via three of the four proposal conditions:
  - **save intent**: regex set (English + Chinese) over user messages from `signals.on_pre_llm_call` → writes Layer B `m1_save_intent` event. Pattern set is conservative (negative tests for "save the file to disk" etc.).
  - **tool-call complexity** ≥ 5 — already-collected `tool_call` signal counts.
  - **modification investment** ≥ 3 — already-collected `user_turn` signal counts.
  - **Skipped**: "task similarity recurrence over N days" — needs cross-session semantic embedding, which Hermes does not ship. Echo's hashing embedding (M5) is too coarse for clustering; documented as a known limitation.

  Scoring: `save_intent`=100, `tool ≥ 5`=30, `modif ≥ 3`=30. Threshold 30. `list_candidates()` joins invocations with per-row signal counts (one query); dashboard exposes via `GET /api/plugins/echo_signals/candidates` (limit + min_score query params). Echo is the *nominator* — the actual create-skill decision stays with the user/curator. 42 new tests including regex matrix (9 English positives + 7 Chinese positives + 7 negatives) and end-to-end through `sig.on_pre_llm_call`. 304 Echo + 69 Hermes regression all pass.
- ✅ **Step 14 (dashboard widgets for M1 + M5)**: EchoPage layout extended to a 6-widget grid:
  - **CandidateQueue** — `GET /candidates` displayed as a list of nominated invocations with score + reasons.
  - **PreferenceLibrary** — `GET /preferences` (new endpoint, sorted by composite_score DESC) shows the M5 corpus with expand/collapse and per-row Delete (`DELETE /preferences/{id}` — idempotent).
  Dashboard router now 9 endpoints. Bundle grew to ~940 lines, still hand-written IIFE no-build. 8 new endpoint tests; 312 Echo + 69 Hermes regression all pass.
- ✅ **Step 15 (M1 condition 4 — semantic recurrence, lexical proxy)**: Schema v3 adds `echo_user_request_log` (append-only stream of user_message + hashing embedding + ts). [plugins/echo_signals/m1_trigger.py](plugins/echo_signals/m1_trigger.py) gains `log_user_request()` (writes per turn), `detect_semantic_recurrence()` (cosine match against the lookback window, excluding self-correlation by invocation_id + 60-second window), `record_semantic_recurrence_signal()` (Layer B `m1_semantic_recurrence` with value_real=similarity), and `gc_old_requests()` for retention. `signals.on_pre_llm_call` integrated: every user turn first checks recurrence against prior log, THEN appends itself to the log (order matters — otherwise self-match). Score weight 50; threshold 0.6 for the hashing embedding.

  **Important caveat**: this is a **lexical proxy** for proposal §M1's "embedding 余弦相似度". Hermes ships no neural embedding provider, so adding a per-turn LLM call would violate Echo's near-zero cost design. The hashing embedding catches lexical repetition strongly (e.g. "write me a marketing email" matches "marketing email for our launch") but misses paraphrase ("draft a promotional message" wouldn't match). Documented in m1_trigger.py and signal_type name (`m1_semantic_recurrence`) preserved for proposal alignment. Upgrading to neural embeddings is a one-line swap in `preference_rag.set_encoder()`. 14 new tests; 326 Echo + 69 Hermes regression all pass.
- ✅ **Step 16 (neural embedding upgrade)**: [plugins/echo_signals/embeddings.py](plugins/echo_signals/embeddings.py) plugs an OpenAI-compatible embeddings API into the same `preference_rag.set_encoder()` slot the hashing encoder uses. Configuration via env vars (Echo-specific to avoid coupling to Hermes' private `_resolve_task_provider_model`):
  - `ECHO_EMBEDDING_PROVIDER=openai` enables; anything else stays on hashing.
  - `ECHO_EMBEDDING_MODEL` defaults to `text-embedding-3-small`.
  - `ECHO_EMBEDDING_API_KEY` falls back to `OPENAI_API_KEY`.
  - `ECHO_EMBEDDING_BASE_URL` honors OpenAI-compatible proxies (OpenRouter, self-hosted).
  - LRU cache (2048 entries) dedupes repeat queries within a process.
  - **Sticky kill-switch**: first failure logs once + permanently falls back to hashing for the remainder of the process. Stops outages from spamming retries on every user turn.
  - `clear_embedding_corpus()` wipes `echo_preference_example` + `echo_user_request_log` for safe provider switching (`cosine()` already returns 0 on dim mismatch so stale rows silently never match — clearing just frees space). `register(ctx)` calls `install_active_encoder()` automatically. 22 new tests. M1 condition 4 and M5 retrieval now lift from lexical proxy to true neural semantic when configured.
- ✅ **Step 17 (Tauri desktop shell)**: Native wrapper around the Hermes Echo dashboard. Two new signal sources the browser sandbox cannot capture:
  - **OS clipboard** — polled every 2 s via `tauri-plugin-clipboard-manager`; on change, POSTs `clipboard_copy` to a new dashboard endpoint with length + 200-char preview (raw text never persisted server-side).
  - **Window focus** — `tauri::WindowEvent::Focused` emits `window_focus` / `window_blur` events on the same endpoint.
  - Backend addition: `POST /api/plugins/echo_signals/clipboard-signal` ([plugins/echo_signals/dashboard/plugin_api.py](plugins/echo_signals/dashboard/plugin_api.py)) records to `echo_signal_event` (Layer A) attributed to the most recent invocation. Body capped at 8 KB; `value_text` truncated to 200 chars; full clipboard contents are never stored. 7 new endpoint tests.
  - Rust project under [tauri-shell/](tauri-shell/) — Cargo.toml + tauri.conf.json + main.rs/lib.rs/clipboard.rs + bootstrap index.html. Build instructions in [tauri-shell/README.md](tauri-shell/README.md). Build/run not exercised in CI (no Rust toolchain in the conda test env); user runs `npm run dev` / `npm run build` on their own machine.
- ✅ **Step 18 (polish pass)**: Three small finishing touches.
  - **Periodic GC**: [plugins/echo_signals/maintenance.py](plugins/echo_signals/maintenance.py) — `maybe_run_gc()` piggybacks on `on_session_start`; once per process per 24h it fire-and-forgets a daemon thread that prunes `echo_user_request_log` (past M1 lookback) and `echo_turn_cache` (orphaned sessions > 7 days). Idempotent on restart; no Hermes cron coupling.
  - **`GET /status` endpoint + dashboard StatusStrip widget**: schema version, active encoder (neural vs hashing — reflects sticky-fallback live), and per-table row counts (collapsible "table breakdown" details). Polls on dashboard refresh.
  - **SkillTimeline badges**: bundle's `SIGNAL_BADGES` map grew from 5 entries to 12, covering desktop-shell signals (`clipboard_copy/paste`, `window_focus/blur` cyan/zinc), Layer B NL classifier (`nl_positive/negative` emerald/rose dimmer than explicit), and M1 nominations (`m1_save_intent`, `m1_semantic_recurrence` amber).
  14 new tests; 376 Echo + 69 Hermes regression all pass.
- ⬜ **Step 19+**: Open work — proposal §4 evaluation suite (datasets, simulated personas, baselines, metrics), real-runtime UI walkthrough (defer until report-writing time per maintainer's preference), final app icon for tauri-shell.

(Update this list when steps move state — the file is committed and serves as a living changelog for the project.)

## Upstream sync

`upstream` remote points at `https://github.com/NousResearch/hermes-agent.git`. Tags are fetched. Since the four core files (and the plugin interface itself) are untouched, `git merge upstream/<tag>` should cleanly apply for most upstream changes. If a merge conflict reaches into `plugins/echo_signals/` it's almost certainly a real semantic conflict worth surfacing to the maintainer rather than auto-resolving.
