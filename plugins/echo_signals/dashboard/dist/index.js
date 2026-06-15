/*
 * Echo dashboard plugin — front-end bundle.
 *
 * Hand-written IIFE; consumes the React + UI component SDK that
 * Hermes' web/src/plugins/registry.ts exposes on
 *   window.__HERMES_PLUGIN_SDK__
 * and registers the page component via
 *   window.__HERMES_PLUGINS__.register("echo_signals", EchoPage).
 *
 * No build step. Edits to this file are reflected immediately on
 * the next dashboard reload — Hermes serves it from
 *   /dashboard-plugins/echo_signals/dist/index.js
 *
 * Four widgets stack on the Echo top-level page:
 *   1. Skill confidence ranking (worst-first, the page's headline)
 *   2. Status distribution (active / pending / retired bucket counts)
 *   3. Recent invocations (each skill load, signal counts)
 *   4. Per-skill timeline (revealed when a row in #1 is clicked)
 *
 * Conventions used throughout:
 *   - All API calls go through SDK.fetchJSON("/api/plugins/echo_signals/...")
 *     which carries the Hermes session token automatically.
 *   - No external dependencies. No JSX (no build step). All elements
 *     created via React.createElement.
 *   - Tailwind utility classes for styling (the dashboard ships with
 *     Tailwind v4; arbitrary class names work as expected).
 */

(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK || !window.__HERMES_PLUGINS__) {
    // Older host or pre-SDK plugin manager — bail cleanly.
    return;
  }

  const React = SDK.React;
  const { useState, useEffect, useCallback, useRef } = SDK.hooks;
  const C = SDK.components;
  const cn = SDK.utils && SDK.utils.cn ? SDK.utils.cn : (...xs) => xs.filter(Boolean).join(" ");
  const fetchJSON = SDK.fetchJSON;
  const h = React.createElement;

  // ---------------------------------------------------------------------
  // Echo rating buttons — styling
  //
  // The chat:bottom thumbs widget can't lean on Tailwind colour classes:
  // the dashboard's Tailwind JIT only scans web/src, NOT this bundle, so
  // any `text-teal-400` / `bg-rose-950` we write here never makes it into
  // the compiled CSS. We therefore inject a small self-contained stylesheet
  // once and drive the accent per-button via a `--echo-accent` custom prop.
  // color-mix + custom props are already used by the dashboard's index.css,
  // so they're safe in every browser the dashboard targets.
  // ---------------------------------------------------------------------
  const ECHO_TEAL = "#14b8a6"; // Echo's sonar-teal identity (positive)
  const ECHO_CORAL = "#f43f5e"; // warm coral (negative) — reads on light + dark

  (function injectRateStyles() {
    if (typeof document === "undefined") return;
    if (document.getElementById("echo-rate-style")) return;
    const css =
      ".echo-rate-btn{display:inline-grid;place-items:center;width:2.25rem;" +
      "height:2.25rem;border-radius:.5rem;border:1px solid var(--echo-accent);" +
      "color:var(--echo-accent);background:color-mix(in srgb,var(--echo-accent) 12%,transparent);" +
      "box-shadow:0 0 10px -4px var(--echo-accent);cursor:pointer;" +
      "transition:transform .12s ease,box-shadow .15s ease,background .15s ease;}" +
      ".echo-rate-btn:hover{background:color-mix(in srgb,var(--echo-accent) 22%,transparent);" +
      "box-shadow:0 0 16px -2px var(--echo-accent);transform:translateY(-1px);}" +
      ".echo-rate-btn:active{transform:translateY(0);}" +
      ".echo-rate-btn:disabled{opacity:.45;cursor:default;box-shadow:none;transform:none;}" +
      ".echo-rate-btn svg{width:1.2rem;height:1.2rem;display:block;}" +
      ".echo-rate-chip{display:inline-flex;align-items:center;gap:.3rem;white-space:nowrap;}" +
      ".echo-rate-chip svg{width:.95rem;height:.95rem;}" +
      // Secondary (undo / reason / cancel) + accent (submit) mini-buttons and
      // the single-line reason input — all keep the bar at a constant height.
      ".echo-mini-btn{display:inline-flex;align-items:center;gap:.25rem;height:1.75rem;" +
      "padding:0 .6rem;border-radius:.375rem;white-space:nowrap;cursor:pointer;font-size:.75rem;" +
      "border:1px solid var(--color-border,rgba(255,255,255,.15));" +
      "color:var(--color-muted-foreground,#9aa);background:transparent;" +
      "transition:color .15s ease,border-color .15s ease;}" +
      ".echo-mini-btn:hover{color:var(--color-foreground,#e6fbf7);" +
      "border-color:color-mix(in srgb,var(--color-foreground,#e6fbf7) 40%,transparent);}" +
      ".echo-accent-btn{display:inline-flex;align-items:center;height:1.75rem;padding:0 .75rem;" +
      "border-radius:.375rem;white-space:nowrap;cursor:pointer;font-size:.75rem;" +
      "border:1px solid var(--echo-accent);color:var(--echo-accent);" +
      "background:color-mix(in srgb,var(--echo-accent) 12%,transparent);" +
      "transition:background .15s ease,box-shadow .15s ease;}" +
      ".echo-accent-btn:hover{background:color-mix(in srgb,var(--echo-accent) 22%,transparent);" +
      "box-shadow:0 0 12px -3px var(--echo-accent);}" +
      ".echo-accent-btn:disabled{opacity:.45;cursor:default;box-shadow:none;}" +
      // color:inherit so the text uses the bar's ambient (theme-correct,
      // readable) colour instead of --color-foreground, which resolves to a
      // near-white in the Echo Light theme and made typed text invisible.
      ".echo-reason-input{height:1.75rem;padding:0 .55rem;border-radius:.375rem;font-size:.75rem;" +
      "outline:none;border:1px solid var(--color-border,rgba(255,255,255,.15));" +
      "color:inherit;background:color-mix(in srgb,currentColor 6%,transparent);}" +
      ".echo-reason-input::placeholder{color:inherit;opacity:.45;}" +
      ".echo-reason-input:focus{border-color:var(--echo-accent);" +
      "box-shadow:0 0 0 1px var(--echo-accent);}";
    const el = document.createElement("style");
    el.id = "echo-rate-style";
    el.textContent = css;
    document.head.appendChild(el);
  })();

  // Line-art thumb glyph (lucide geometry). `up=false` renders thumbs-down.
  function thumbIcon(up) {
    const paths = up
      ? ["M7 10v12",
         "M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z"]
      : ["M17 14V2",
         "M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z"];
    return h("svg", {
      viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
      strokeWidth: 2, strokeLinecap: "round", strokeLinejoin: "round",
      "aria-hidden": "true",
    }, paths.map((d, i) => h("path", { key: i, d })));
  }

  // Small inline accent chip (icon + text) for the rated / reason labels.
  function thumbChip(up, text) {
    return h("span", {
      className: "echo-rate-chip",
      style: { color: up ? ECHO_TEAL : ECHO_CORAL },
    }, thumbIcon(up), text);
  }

  // ---------------------------------------------------------------------
  // API helpers
  // ---------------------------------------------------------------------

  const API_BASE = "/api/plugins/echo_signals";

  function apiGet(path) {
    return fetchJSON(API_BASE + path);
  }

  function apiPost(path, body) {
    return fetchJSON(API_BASE + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  // ---------------------------------------------------------------------
  // Visual primitives
  // ---------------------------------------------------------------------

  function StatusBadge({ status }) {
    const palette = {
      active: "bg-emerald-900/40 text-emerald-300 border-emerald-700/50",
      pending_review: "bg-amber-900/40 text-amber-300 border-amber-700/50",
      retired: "bg-zinc-800 text-zinc-400 border-zinc-700",
    };
    const label = {
      active: "Active",
      pending_review: "Pending Review",
      retired: "Retired",
    }[status] || status;
    return h("span", {
      className: cn(
        "inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border",
        palette[status] || "bg-zinc-800 text-zinc-300 border-zinc-700",
      ),
    }, label);
  }

  function ConfidenceBar({ value }) {
    // Color band reflects severity. Width is the raw confidence.
    const pct = Math.round((value || 0) * 100);
    let color = "bg-emerald-500";
    if (value < 0.3) color = "bg-rose-500";
    else if (value < 0.5) color = "bg-amber-500";
    return h("div", { className: "flex items-center gap-2" },
      h("div", { className: "flex-1 h-2 rounded bg-zinc-800 overflow-hidden" },
        h("div", { className: cn("h-full", color), style: { width: pct + "%" } }),
      ),
      h("span", { className: "tabular-nums text-xs text-zinc-400 w-10 text-right" }, pct + "%"),
    );
  }

  function ErrorBlock({ message }) {
    return h("div", {
      className: "p-4 border border-rose-900/50 bg-rose-950/30 rounded text-rose-300 text-sm",
    }, "Error: " + message);
  }

  function EmptyBlock({ message }) {
    return h("div", {
      className: "p-6 text-center text-zinc-500 text-sm border border-dashed border-zinc-800 rounded",
    }, message);
  }

  function LoadingBlock() {
    return h("div", { className: "p-4 text-zinc-500 text-sm" }, "Loading…");
  }

  // ---------------------------------------------------------------------
  // Widget 1 — Skill confidence ranking
  // ---------------------------------------------------------------------

  function SkillRanking({ onSkillClick, selectedSkillId, refreshKey }) {
    const [skills, setSkills] = useState(null);
    const [error, setError] = useState(null);

    useEffect(() => {
      setError(null);
      apiGet("/skills?limit=200")
        .then((d) => setSkills(d.skills || []))
        .catch((e) => setError(e.message || String(e)));
    }, [refreshKey]);

    const body = (() => {
      if (error) return h(ErrorBlock, { message: error });
      if (skills === null) return h(LoadingBlock);
      if (skills.length === 0)
        return h(EmptyBlock, {
          message:
            "No skills tracked yet. Echo records a skill the first time a Hermes session calls bump_use on it.",
        });

      return h("div", { className: "overflow-x-auto" },
        h("table", { className: "w-full text-sm" },
          h("thead", { className: "text-xs uppercase text-zinc-500" },
            h("tr", null,
              h("th", { className: "text-left py-2 px-2" }, "Skill"),
              h("th", { className: "text-left py-2 px-2 w-56" }, "Confidence"),
              h("th", { className: "text-left py-2 px-2" }, "Status"),
              h("th", { className: "text-right py-2 px-2" }, "Invocations"),
              h("th", { className: "text-right py-2 px-2" }, "Signals"),
            ),
          ),
          h("tbody", null,
            skills.map((s) => h("tr", {
              key: s.skill_id,
              className: cn(
                "border-t border-zinc-800 cursor-pointer hover:bg-zinc-900/50",
                selectedSkillId === s.skill_id && "bg-zinc-900",
              ),
              onClick: () => onSkillClick && onSkillClick(s.skill_id),
            },
              h("td", { className: "py-2 px-2 font-mono text-xs" },
                s.skill_id,
                s.locked
                  ? h("span", { className: "ml-2 text-zinc-500", title: "Locked — user-edited" }, "🔒")
                  : null,
              ),
              h("td", { className: "py-2 px-2" }, h(ConfidenceBar, { value: s.confidence })),
              h("td", { className: "py-2 px-2" }, h(StatusBadge, { status: s.status })),
              h("td", { className: "py-2 px-2 text-right tabular-nums text-zinc-400" }, s.n_invocations),
              h("td", { className: "py-2 px-2 text-right tabular-nums text-zinc-400" }, s.n_signals),
            )),
          ),
        ),
      );
    })();

    return h(C.Card, null,
      h(C.CardHeader, null, h(C.CardTitle, null, "Skill Confidence Ranking")),
      h(C.CardContent, null, body),
    );
  }

  // ---------------------------------------------------------------------
  // Widget 2 — Status distribution (donut-ish, no chart lib)
  // ---------------------------------------------------------------------

  function StatusDistribution({ refreshKey }) {
    const [data, setData] = useState(null);
    const [error, setError] = useState(null);

    useEffect(() => {
      setError(null);
      apiGet("/status-distribution")
        .then((d) => setData(d.distribution || []))
        .catch((e) => setError(e.message || String(e)));
    }, [refreshKey]);

    if (error) return h(C.Card, null,
      h(C.CardHeader, null, h(C.CardTitle, null, "Status Distribution")),
      h(C.CardContent, null, h(ErrorBlock, { message: error })),
    );
    if (data === null) return h(C.Card, null,
      h(C.CardHeader, null, h(C.CardTitle, null, "Status Distribution")),
      h(C.CardContent, null, h(LoadingBlock)),
    );

    const total = data.reduce((acc, b) => acc + b.count, 0);
    const palette = {
      active: "bg-emerald-500",
      pending_review: "bg-amber-500",
      retired: "bg-zinc-500",
    };
    const label = {
      active: "Active",
      pending_review: "Pending Review",
      retired: "Retired",
    };

    // A compact stacked-bar "donut substitute" — no chart lib needed.
    return h(C.Card, null,
      h(C.CardHeader, null, h(C.CardTitle, null, "Status Distribution")),
      h(C.CardContent, null,
        total === 0
          ? h(EmptyBlock, { message: "No skills tracked yet." })
          : h("div", { className: "space-y-3" },
            h("div", { className: "flex h-3 rounded overflow-hidden bg-zinc-800" },
              data.map((b) => b.count > 0
                ? h("div", {
                    key: b.status,
                    className: palette[b.status] || "bg-zinc-600",
                    style: { width: ((b.count / total) * 100).toFixed(2) + "%" },
                    title: label[b.status] + ": " + b.count,
                  })
                : null
              ),
            ),
            h("div", { className: "grid grid-cols-3 gap-2 text-sm" },
              data.map((b) => h("div", { key: b.status, className: "flex items-center gap-2" },
                h("span", {
                  className: cn("w-3 h-3 rounded", palette[b.status] || "bg-zinc-600"),
                }),
                h("div", { className: "flex-1" },
                  h("div", { className: "text-zinc-300" }, label[b.status]),
                  h("div", { className: "text-xs text-zinc-500 tabular-nums" },
                    b.count + " (" + (total > 0 ? Math.round((b.count / total) * 100) : 0) + "%)",
                  ),
                ),
              )),
            ),
          ),
      ),
    );
  }

  // ---------------------------------------------------------------------
  // Widget 3 — Recent invocations
  // ---------------------------------------------------------------------

  function RecentInvocations({ refreshKey }) {
    const [invocations, setInvocations] = useState(null);
    const [error, setError] = useState(null);

    useEffect(() => {
      setError(null);
      apiGet("/invocations/recent?limit=30")
        .then((d) => setInvocations(d.invocations || []))
        .catch((e) => setError(e.message || String(e)));
    }, [refreshKey]);

    const body = (() => {
      if (error) return h(ErrorBlock, { message: error });
      if (invocations === null) return h(LoadingBlock);
      if (invocations.length === 0)
        return h(EmptyBlock, {
          message: "No skill invocations recorded yet.",
        });

      const fmtTime = (ts) => {
        if (!ts) return "—";
        try {
          return new Date(ts * 1000).toLocaleString();
        } catch {
          return String(ts);
        }
      };

      return h("div", { className: "overflow-x-auto" },
        h("table", { className: "w-full text-sm" },
          h("thead", { className: "text-xs uppercase text-zinc-500" },
            h("tr", null,
              h("th", { className: "text-left py-2 px-2" }, "Started"),
              h("th", { className: "text-left py-2 px-2" }, "Skill"),
              h("th", { className: "text-left py-2 px-2" }, "Platform"),
              h("th", { className: "text-left py-2 px-2" }, "Session"),
              h("th", { className: "text-right py-2 px-2" }, "Signals"),
            ),
          ),
          h("tbody", null,
            invocations.map((i) => h("tr", { key: i.invocation_id, className: "border-t border-zinc-800" },
              h("td", { className: "py-2 px-2 text-zinc-400 text-xs" }, fmtTime(i.started_at)),
              h("td", { className: "py-2 px-2 font-mono text-xs" }, i.skill_id),
              h("td", { className: "py-2 px-2 text-xs text-zinc-400" }, i.platform),
              h("td", { className: "py-2 px-2 font-mono text-xs text-zinc-500" },
                i.session_id ? i.session_id.slice(0, 8) + "…" : "—"),
              h("td", { className: "py-2 px-2 text-right tabular-nums text-zinc-400" }, i.signal_count),
            )),
          ),
        ),
      );
    })();

    return h(C.Card, null,
      h(C.CardHeader, null, h(C.CardTitle, null, "Recent Invocations")),
      h(C.CardContent, null, body),
    );
  }

  // ---------------------------------------------------------------------
  // Widget 4 — Per-skill timeline (revealed on click)
  // ---------------------------------------------------------------------

  const SIGNAL_BADGES = {
    // Layer A — pure behavior
    user_turn: { color: "bg-blue-900/40 text-blue-300 border-blue-700/50", label: "user turn" },
    tool_call: { color: "bg-violet-900/40 text-violet-300 border-violet-700/50", label: "tool call" },
    tool_error: { color: "bg-rose-900/40 text-rose-300 border-rose-700/50", label: "✗ tool error" },
    session_ended: { color: "bg-zinc-800 text-zinc-400 border-zinc-700", label: "session ended" },
    // Desktop-shell signals (Step 17)
    clipboard_copy: { color: "bg-cyan-900/40 text-cyan-300 border-cyan-700/50", label: "📋 clipboard copy" },
    clipboard_paste: { color: "bg-cyan-900/40 text-cyan-300 border-cyan-700/50", label: "📋 clipboard paste" },
    window_focus: { color: "bg-zinc-800 text-zinc-400 border-zinc-700", label: "window focus" },
    window_blur: { color: "bg-zinc-800 text-zinc-500 border-zinc-700", label: "window blur" },
    // Layer A — drift detection (M3)
    drift_detected: { color: "bg-orange-900/40 text-orange-300 border-orange-700/50", label: "⚠ drift detected" },
    // Layer B — explicit
    explicit_positive: { color: "bg-emerald-900/40 text-emerald-300 border-emerald-700/50", label: "👍 positive" },
    explicit_negative: { color: "bg-rose-900/40 text-rose-300 border-rose-700/50", label: "👎 negative" },
    // Layer B — NL classifier (Step 7)
    nl_positive: { color: "bg-emerald-900/30 text-emerald-300 border-emerald-700/40", label: "NL positive" },
    nl_negative: { color: "bg-rose-900/30 text-rose-300 border-rose-700/40", label: "NL negative" },
    // Layer B+ — LLM-scored reason (signed −5..+5 in value_real)
    reason_score: { color: "bg-sky-900/40 text-sky-300 border-sky-700/50", label: "reason score" },
    // M1 nomination signals (Step 13 + 15)
    m1_save_intent: { color: "bg-amber-900/40 text-amber-300 border-amber-700/50", label: "save intent" },
    m1_semantic_recurrence: { color: "bg-amber-900/30 text-amber-300 border-amber-700/40", label: "task recurrence" },
  };

  function SkillTimeline({ skillId, onClose }) {
    const [data, setData] = useState(null);
    const [error, setError] = useState(null);

    useEffect(() => {
      if (!skillId) return;
      setError(null);
      setData(null);
      apiGet("/skills/" + encodeURIComponent(skillId) + "/timeline?limit=300")
        .then(setData)
        .catch((e) => setError(e.message || String(e)));
    }, [skillId]);

    if (!skillId) return null;

    const body = (() => {
      if (error) return h(ErrorBlock, { message: error });
      if (data === null) return h(LoadingBlock);
      if (!data.events || data.events.length === 0)
        return h(EmptyBlock, { message: "No signals recorded for this skill yet." });

      const fmtTime = (ts) => {
        try {
          return new Date(ts * 1000).toLocaleString();
        } catch {
          return String(ts);
        }
      };

      return h("div", { className: "space-y-3" },
        h("div", { className: "flex flex-wrap gap-4 text-sm border border-zinc-800 rounded p-3 bg-zinc-900/40" },
          h("div", null,
            h("div", { className: "text-zinc-500 text-xs uppercase" }, "Confidence"),
            h("div", { className: "tabular-nums" }, (data.skill.confidence * 100).toFixed(1) + "%"),
          ),
          h("div", null,
            h("div", { className: "text-zinc-500 text-xs uppercase" }, "Status"),
            h(StatusBadge, { status: data.skill.status }),
          ),
          h("div", null,
            h("div", { className: "text-zinc-500 text-xs uppercase" }, "Invocations"),
            h("div", { className: "tabular-nums" }, data.skill.n_invocations),
          ),
          h("div", null,
            h("div", { className: "text-zinc-500 text-xs uppercase" }, "Signals"),
            h("div", { className: "tabular-nums" }, data.skill.n_signals),
          ),
        ),
        h("ol", { className: "space-y-1" },
          data.events.map((e) => {
            const badge = SIGNAL_BADGES[e.signal_type] || {
              color: "bg-zinc-800 text-zinc-400 border-zinc-700",
              label: e.signal_type,
            };
            const detail = e.value_text
              ? " — " + e.value_text
              : e.value_int !== null && e.value_int !== undefined
              ? " — " + e.value_int
              : "";
            return h("li", { key: e.event_id, className: "flex items-center gap-3 text-xs" },
              h("span", { className: "text-zinc-500 tabular-nums w-44 shrink-0" }, fmtTime(e.ts)),
              h("span", {
                className: cn(
                  "inline-flex items-center px-2 py-0.5 rounded-full font-medium border",
                  badge.color,
                ),
              }, "L" + e.layer + " · " + badge.label),
              detail ? h("span", { className: "text-zinc-400" }, detail) : null,
              h("span", { className: "text-zinc-600 text-xs ml-auto" }, "inv#" + e.invocation_id),
            );
          }),
        ),
      );
    })();

    return h(C.Card, null,
      h(C.CardHeader, { className: "flex flex-row items-center justify-between" },
        h(C.CardTitle, null, "Timeline — ", h("span", { className: "font-mono text-base" }, skillId)),
        onClose ? h("button", {
          className: "text-zinc-500 hover:text-zinc-300 text-xs",
          onClick: onClose,
        }, "Close ✕") : null,
      ),
      h(C.CardContent, null, body),
    );
  }

  // ---------------------------------------------------------------------
  // Widget 5 — M1 candidate queue (nominated skill-worthy invocations)
  // ---------------------------------------------------------------------

  function CandidateQueue({ refreshKey }) {
    const [candidates, setCandidates] = useState(null);
    const [error, setError] = useState(null);

    useEffect(() => {
      setError(null);
      apiGet("/candidates?limit=10")
        .then((d) => setCandidates(d.candidates || []))
        .catch((e) => setError(e.message || String(e)));
    }, [refreshKey]);

    const body = (() => {
      if (error) return h(ErrorBlock, { message: error });
      if (candidates === null) return h(LoadingBlock);
      if (candidates.length === 0)
        return h(EmptyBlock, {
          message:
            "No skill candidates yet. Echo nominates invocations as " +
            "skill-worthy when the user expresses save intent, runs ≥ 5 " +
            "tool calls, or iterates ≥ 3 user turns.",
        });

      return h("ul", { className: "space-y-2" },
        candidates.map((c) => h("li", {
          key: c.invocation_id,
          className: "p-3 border border-zinc-800 rounded space-y-1",
        },
          h("div", { className: "flex items-center justify-between" },
            h("div", { className: "flex items-center gap-2" },
              h("span", {
                className:
                  "inline-flex items-center px-2 py-0.5 rounded-full " +
                  "text-xs font-medium border bg-violet-900/40 text-violet-300 border-violet-700/50",
              }, "score " + c.score),
              h("span", { className: "font-mono text-sm text-zinc-200" }, c.skill_id || "(unattributed)"),
            ),
            h("span", { className: "text-xs text-zinc-500 tabular-nums" },
              "inv#" + c.invocation_id,
            ),
          ),
          h("ul", { className: "text-xs text-zinc-400 list-disc pl-5" },
            c.reasons.map((r, i) => h("li", { key: i }, r)),
          ),
          h("div", { className: "text-xs text-zinc-500 tabular-nums" },
            "user turns: ", c.user_turns,
            "  ·  tool calls: ", c.tool_calls,
            c.has_save_intent ? "  ·  save intent ✓" : "",
          ),
        )),
      );
    })();

    return h(C.Card, null,
      h(C.CardHeader, null, h(C.CardTitle, null, "Skill Candidates (M1)")),
      h(C.CardContent, null, body),
    );
  }

  // ---------------------------------------------------------------------
  // Widget 5b — M1 session candidates: SKILL-LESS conversations Echo
  // nominates as worth turning into a brand-new skill (proposal §M1 孵化).
  // ---------------------------------------------------------------------

  function SessionCandidateQueue({ refreshKey }) {
    const [candidates, setCandidates] = useState(null);
    const [error, setError] = useState(null);

    useEffect(() => {
      setError(null);
      apiGet("/candidates/sessions?limit=10")
        .then((d) => setCandidates(d.candidates || []))
        .catch((e) => setError(e.message || String(e)));
    }, [refreshKey]);

    const body = (() => {
      if (error) return h(ErrorBlock, { message: error });
      if (candidates === null) return h(LoadingBlock);
      if (candidates.length === 0)
        return h(EmptyBlock, {
          message:
            "No new-skill candidates yet. Echo nominates a conversation " +
            "that never used a skill when the user expresses save intent, " +
            "repeats a past request, runs ≥ 5 tool calls, or iterates ≥ 3 turns.",
        });

      return h("ul", { className: "space-y-2" },
        candidates.map((c) => h("li", {
          key: c.session_id,
          className: "p-3 border border-zinc-800 rounded space-y-1",
        },
          h("div", { className: "flex items-center justify-between" },
            h("div", { className: "flex items-center gap-2" },
              h("span", {
                className:
                  "inline-flex items-center px-2 py-0.5 rounded-full " +
                  "text-xs font-medium border bg-amber-900/40 text-amber-300 border-amber-700/50",
              }, "score " + c.score),
              h("span", { className: "text-sm text-zinc-200 truncate max-w-[20rem]" },
                c.first_message || "(empty)"),
            ),
            h("span", { className: "text-xs text-zinc-500 tabular-nums" },
              c.session_id),
          ),
          h("ul", { className: "text-xs text-zinc-400 list-disc pl-5" },
            c.reasons.map((r, i) => h("li", { key: i }, r)),
          ),
          h("div", { className: "text-xs text-zinc-500 tabular-nums" },
            "turns: ", c.user_turns,
            "  ·  tool calls: ", c.tool_calls || 0,
            c.has_save_intent ? "  ·  save intent ✓" : "",
            c.has_recurrence ? "  ·  recurrence " + c.top_similarity : "",
          ),
        )),
      );
    })();

    return h(C.Card, null,
      h(C.CardHeader, null, h(C.CardTitle, null, "New-Skill Candidates (M1 · skill-less)")),
      h(C.CardContent, null, body),
    );
  }

  // ---------------------------------------------------------------------
  // Widget 6 — M5 preference library browser (with per-row delete)
  // ---------------------------------------------------------------------

  function PreferenceLibrary({ refreshKey }) {
    const [prefs, setPrefs] = useState(null);
    const [error, setError] = useState(null);
    const [expandedId, setExpandedId] = useState(null);
    const [deleting, setDeleting] = useState(null);

    const reload = useCallback(() => {
      apiGet("/preferences?limit=50")
        .then((d) => setPrefs(d.preferences || []))
        .catch((e) => setError(e.message || String(e)));
    }, []);

    useEffect(() => {
      setError(null);
      reload();
    }, [refreshKey, reload]);

    const deletePref = useCallback((eid) => {
      setDeleting(eid);
      fetchJSON("/api/plugins/echo_signals/preferences/" + eid, {
        method: "DELETE",
      })
        .then(() => {
          setPrefs((cur) => (cur || []).filter((p) => p.example_id !== eid));
        })
        .catch((e) => setError(e.message || String(e)))
        .finally(() => setDeleting(null));
    }, []);

    if (error) return h(C.Card, null,
      h(C.CardHeader, null, h(C.CardTitle, null, "Preference Library (M5)")),
      h(C.CardContent, null, h(ErrorBlock, { message: error })),
    );
    if (prefs === null) return h(C.Card, null,
      h(C.CardHeader, null, h(C.CardTitle, null, "Preference Library (M5)")),
      h(C.CardContent, null, h(LoadingBlock)),
    );

    if (prefs.length === 0)
      return h(C.Card, null,
        h(C.CardHeader, null, h(C.CardTitle, null, "Preference Library (M5)")),
        h(C.CardContent, null, h(EmptyBlock, {
          message:
            "No preferences saved yet. Thumbs-up an agent reply (in the " +
            "chat bar at the bottom) to teach Echo what you like — Echo " +
            "will surface similar past examples as few-shots on similar " +
            "future requests.",
        })),
      );

    const fmtTime = (ts) => {
      if (!ts) return "—";
      try { return new Date(ts * 1000).toLocaleString(); }
      catch { return String(ts); }
    };

    return h(C.Card, null,
      h(C.CardHeader, null,
        h(C.CardTitle, null,
          "Preference Library (M5) · ",
          h("span", { className: "text-zinc-400 text-base" }, prefs.length),
        ),
      ),
      h(C.CardContent, null,
        h("ul", { className: "space-y-2" },
          prefs.map((p) => {
            const expanded = expandedId === p.example_id;
            return h("li", {
              key: p.example_id,
              className: "p-3 border border-zinc-800 rounded",
            },
              h("div", { className: "flex items-start justify-between gap-3" },
                h("div", { className: "flex-1 min-w-0" },
                  h("div", { className: "flex items-center gap-2 mb-1" },
                    h("span", {
                      className:
                        "inline-flex items-center px-2 py-0.5 rounded-full " +
                        "text-xs font-medium border bg-emerald-900/40 " +
                        "text-emerald-300 border-emerald-700/50",
                    }, "★ " + p.rating + "/5"),
                    p.skill_id ? h("span", { className: "font-mono text-xs text-zinc-300" }, p.skill_id) : null,
                    h("span", { className: "text-xs text-zinc-500" },
                      "used " + p.use_count + "×",
                    ),
                  ),
                  h("div", {
                    className: cn(
                      "text-sm text-zinc-200",
                      !expanded && "line-clamp-1 truncate",
                    ),
                  }, p.task_request),
                  expanded
                    ? h("div", { className: "mt-2 text-xs text-zinc-400 whitespace-pre-wrap" },
                        h("div", { className: "text-zinc-500 uppercase text-xs mb-1" }, "Reply"),
                        p.agent_output,
                      )
                    : null,
                  expanded
                    ? h("div", { className: "mt-2 text-xs text-zinc-500" },
                        "Created " + fmtTime(p.created_at),
                        p.last_used_at ? " · last used " + fmtTime(p.last_used_at) : "",
                      )
                    : null,
                ),
                h("div", { className: "flex flex-col gap-1 shrink-0" },
                  h("button", {
                    className: "px-2 py-1 text-xs text-zinc-400 hover:text-zinc-200 border border-zinc-800 rounded",
                    onClick: () => setExpandedId(expanded ? null : p.example_id),
                  }, expanded ? "Collapse" : "Expand"),
                  h("button", {
                    className: cn(
                      "px-2 py-1 text-xs rounded border",
                      "border-rose-900/50 text-rose-400 hover:bg-rose-950/30",
                      deleting === p.example_id && "opacity-50 pointer-events-none",
                    ),
                    disabled: deleting === p.example_id,
                    onClick: () => deletePref(p.example_id),
                    title: "Forget this preference",
                  }, deleting === p.example_id ? "…" : "Delete"),
                ),
              ),
            );
          }),
        ),
      ),
    );
  }

  // ---------------------------------------------------------------------
  // Status strip — schema version, encoder, table row counts
  // ---------------------------------------------------------------------

  function StatusStrip({ refreshKey }) {
    const [status, setStatus] = useState(null);

    useEffect(() => {
      apiGet("/status")
        .then(setStatus)
        .catch(() => setStatus(null));
    }, [refreshKey]);

    if (!status) return null;

    const counts = status.table_row_counts || {};
    const total = Object.values(counts).reduce(
      (acc, v) => acc + (typeof v === "number" && v > 0 ? v : 0),
      0,
    );

    return h("div", {
      className:
        "flex flex-wrap items-center gap-3 px-3 py-1.5 text-xs " +
        "text-zinc-500 border border-zinc-800 rounded bg-zinc-950/40",
    },
      h("span", null,
        "encoder: ",
        h("span", {
          className: status.encoder === "neural"
            ? "text-emerald-400" : "text-zinc-300",
        }, status.encoder),
      ),
      h("span", null, "schema v" + status.schema_version),
      h("span", null, total + " total rows"),
      h("details", { className: "ml-auto" },
        h("summary", { className: "cursor-pointer hover:text-zinc-300" }, "table breakdown"),
        h("div", { className: "mt-1 grid grid-cols-2 gap-x-4 gap-y-0.5 tabular-nums" },
          Object.entries(counts).map(([t, n]) => h("div", {
            key: t,
            className: "flex justify-between gap-3",
          },
            h("span", { className: "text-zinc-600 font-mono" }, t),
            h("span", null, n),
          )),
        ),
      ),
    );
  }

  // ---------------------------------------------------------------------
  // Page composition
  // ---------------------------------------------------------------------

  function EchoPage() {
    const [selectedSkillId, setSelectedSkillId] = useState(null);
    const [refreshKey, setRefreshKey] = useState(0);

    const refresh = useCallback(() => setRefreshKey((k) => k + 1), []);

    return h("div", { className: "p-6 space-y-6 max-w-7xl mx-auto" },
      // Header — Echo sonar-teal identity (matches the CLI/TUI skin).
      h("div", { className: "flex items-end justify-between border-b border-teal-900/40 pb-4" },
        h("div", null,
          h("div", { className: "flex items-center gap-2" },
            h("span", { className: "text-teal-400 text-lg", title: "Echo" }, "◉"),
            h("h1", {
              className: "text-2xl font-bold text-teal-300 tracking-tight",
            }, "Echo"),
            h("span", { className: "text-teal-700 text-xs tabular-nums" }, "·· ● ··"),
          ),
          h("p", { className: "text-sm text-zinc-400 mt-1" },
            "User-signal-driven skill lifecycle. Confidence updates flow from explicit feedback, language sentiment, and behavior-drift detection."),
        ),
        h("button", {
          className: "text-xs text-teal-400 hover:text-teal-200 px-3 py-1.5 border border-teal-900/50 rounded hover:border-teal-600",
          onClick: refresh,
        }, "↻ Refresh"),
      ),
      // Status strip — diagnostic info
      h(StatusStrip, { refreshKey }),
      // Two-column responsive layout for the top row
      h("div", { className: "grid grid-cols-1 lg:grid-cols-2 gap-4" },
        h(SkillRanking, {
          onSkillClick: setSelectedSkillId,
          selectedSkillId,
          refreshKey,
        }),
        h(StatusDistribution, { refreshKey }),
      ),
      // Timeline panel — only when a skill is selected
      selectedSkillId
        ? h(SkillTimeline, {
            skillId: selectedSkillId,
            onClose: () => setSelectedSkillId(null),
          })
        : null,
      // Two-column responsive layout for the lower row
      h("div", { className: "grid grid-cols-1 lg:grid-cols-2 gap-4" },
        h(CandidateQueue, { refreshKey }),
        h(SessionCandidateQueue, { refreshKey }),
      ),
      h("div", { className: "grid grid-cols-1 lg:grid-cols-2 gap-4" },
        h(PreferenceLibrary, { refreshKey }),
      ),
      // Recent invocations full-width at the bottom
      h(RecentInvocations, { refreshKey }),
    );
  }

  // ---------------------------------------------------------------------
  // ChatPage slot: current-invocation thumbs widget
  // ---------------------------------------------------------------------
  //
  // Hermes' ChatPage is a PTY/xterm pane — there are no per-message
  // hooks. Instead we mount a rating bar at chat:bottom that walks a FIFO
  // QUEUE of this conversation's un-rated skill invocations, one at a time.
  //
  // Per-item state machine (see ThumbsBar):
  //   idle   — skill shown with 👍/👎; NO timer; waiting for the user.
  //   rated  — a thumb was tapped; a RATE_WINDOW_MS countdown runs. The user
  //            may 撤销 (→ idle, timer stops) or ✎理由 (→ reason, timer stops).
  //            If the window elapses untouched the rating COMMITS (POSTed) and
  //            the queue advances to the next invocation.
  //   reason — textarea open; no timer; submit commits {rating, reason} and
  //            advances; cancel returns to the rated window.
  // Both 👍 and 👎 can carry a reason. When the queue drains, the whole bar
  // fades out; a fresh invocation fades it back in. The rating only reaches
  // the backend on commit, so undo is purely client-side (no reversal).

  const RATE_WINDOW_MS = 10000; // 10s undo / add-reason grace per rating
  const FADE_MS = 320;
  const POLL_INTERVAL_MS = 5000;

  // -------------------------------------------------------------------
  // ScopeQuestion — appears in the chat:bottom slot when a new skill
  // needs scope_level confirmation. Takes precedence over ThumbsBar so
  // the user resolves the more time-sensitive question first.
  // -------------------------------------------------------------------

  function ScopeQuestion({ skill, onAnswered }) {
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState(null);

    const submit = useCallback((level) => {
      if (submitting) return;
      setSubmitting(true);
      setError(null);
      apiPost("/scope", { skill_id: skill.skill_id, scope_level: level })
        .then(() => onAnswered && onAnswered(skill.skill_id))
        .catch((e) => {
          setError(e.message || String(e));
          setSubmitting(false);
        });
    }, [skill, submitting, onAnswered]);

    return h("div", {
      className:
        "flex flex-wrap items-center gap-3 px-3 py-2 text-xs " +
        "border-t border-amber-900/40 bg-amber-950/20",
    },
      h("span", { className: "text-amber-300 font-medium" }, "Echo · scope?"),
      h("span", { className: "text-zinc-400" },
        "Just created ",
        h("span", { className: "font-mono text-zinc-200" }, skill.skill_id),
        ". When you do something similar next time, should I:",
      ),
      h("button", {
        className: cn(
          "px-2 py-1 rounded border border-zinc-700",
          "hover:border-amber-500 hover:bg-amber-950/40",
          submitting && "opacity-50 pointer-events-none",
        ),
        title: "Reuse this approach across similar tasks",
        onClick: () => submit("broad"),
        disabled: submitting,
      }, "A · reuse the whole approach"),
      h("button", {
        className: cn(
          "px-2 py-1 rounded border border-zinc-700",
          "hover:border-amber-500 hover:bg-amber-950/40",
          submitting && "opacity-50 pointer-events-none",
        ),
        title: "Borrow the methodology only; redo specifics each time",
        onClick: () => submit("narrow"),
        disabled: submitting,
      }, "B · only the general idea"),
      error
        ? h("span", { className: "text-rose-400 ml-2" }, error)
        : null,
    );
  }

  function ThumbsBar(props) {
    // sessionId: the live PTY conversation id, handed in by ChatPage through
    // PluginSlot (the host forwards it as a slot prop). It scopes the rating
    // to THIS conversation. Without it — a fresh chat with no activity yet, or
    // an older dashboard build that doesn't pass the prop — the widget stays
    // hidden instead of surfacing a previous conversation's skill.
    const sessionId =
      props && props.sessionId ? String(props.sessionId) : null;
    const [queue, setQueue] = useState([]);   // FIFO of un-rated invocations (oldest-first)
    const handledRef = useRef(null);
    if (handledRef.current === null) handledRef.current = new Set();
    // invocation_id the user explicitly asked to rate next, via the sidebar
    // SKILLS panel (window 'echo:rate-skill'). Held in a ref so the poll
    // closure always reads the latest without re-subscribing.
    const priorityRef = useRef(null);
    // Float the requested invocation to the queue head so it is what's up for
    // rating. No-op when the id isn't (or no longer) pending.
    function floatPriority(arr, pid) {
      if (pid == null) return arr;
      const i = arr.findIndex((x) => x.invocation_id === pid);
      if (i <= 0) return arr;
      return [arr[i]].concat(arr.slice(0, i)).concat(arr.slice(i + 1));
    }
    const [mode, setMode] = useState("idle");  // idle | rated | reason
    const [rating, setRating] = useState(0);   // +1 / -1 chosen for the head item
    const [reasonText, setReasonText] = useState("");
    const [remain, setRemain] = useState(0);   // countdown seconds while in `rated`
    const [displayItem, setDisplayItem] = useState(null); // what the fading bar renders
    const [shown, setShown] = useState(false); // opacity target for the fade
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState(null);
    const [pendingScope, setPendingScope] = useState(null);
    // Local ack list of skills the user already answered scope for this
    // session — prevents the question from flashing back if the API
    // poll races the local write.
    const [scopeAnswered, setScopeAnswered] = useState(new Set());

    // The head of the queue is the invocation currently up for rating.
    const current = queue.length ? queue[0] : null;
    const currentId = current ? current.invocation_id : null;

    // Poll both endpoints. ScopeQuestion takes precedence in the render
    // when pendingScope is non-null.
    useEffect(() => {
      let cancelled = false;
      function tick() {
        // No conversation bound yet → empty queue, nothing to rate. Keeps a
        // prior conversation's skill from leaking into a fresh chat.
        if (!sessionId) {
          setQueue([]);
          setPendingScope(null);
          return;
        }
        apiGet(
          "/invocations/recent?limit=30&session_id=" +
            encodeURIComponent(sessionId),
        )
          .then((d) => {
            if (cancelled) return;
            setError(null);
            const handled = handledRef.current;
            // FIFO queue: oldest-first, minus anything already rated on the
            // backend or handled locally this session.
            const pend = (d.invocations || [])
              .filter(
                (iv) =>
                  iv && iv.skill_id && !iv.rated &&
                  !handled.has(iv.invocation_id),
              )
              .sort((a, b) => a.invocation_id - b.invocation_id);
            setQueue((prev) => {
              let next = pend;
              // Keep the live head object if it's still pending, so an
              // in-flight rating isn't disturbed by the refresh.
              if (
                prev.length && pend.length &&
                prev[0].invocation_id === pend[0].invocation_id
              ) {
                next = [prev[0]].concat(pend.slice(1));
              }
              // Honor a sidebar "rate this skill" request across refreshes.
              return floatPriority(next, priorityRef.current);
            });
          })
          .catch((e) => {
            if (cancelled) return;
            setError(e.message || String(e));
          });
        apiGet(
          "/scope/pending?limit=1&session_id=" +
            encodeURIComponent(sessionId),
        )
          .then((d) => {
            if (cancelled) return;
            const first = (d.pending || []).find(
              (p) => !scopeAnswered.has(p.skill_id),
            );
            setPendingScope(first || null);
          })
          .catch(() => { /* non-fatal — scope polling is opportunistic */ });
      }
      tick();
      const id = setInterval(tick, POLL_INTERVAL_MS);
      return () => { cancelled = true; clearInterval(id); };
    }, [scopeAnswered, sessionId]);

    // When the user resolves a scope question, dismiss it locally so the
    // poll doesn't re-show it before the backend update propagates.
    const onScopeAnswered = useCallback((skill_id) => {
      setScopeAnswered((prev) => {
        const next = new Set(prev);
        next.add(skill_id);
        return next;
      });
      setPendingScope(null);
    }, []);

    // Reset the per-item interaction whenever the queue head changes (advance
    // to the next invocation, or the queue drains to null). Hook — stays above
    // the early returns so the hook count is stable across renders.
    useEffect(() => {
      setMode("idle");
      setRating(0);
      setReasonText("");
      setRemain(0);
    }, [currentId]);

    // Bridge from the sidebar SKILLS panel: clicking an un-rated skill fires
    // window 'echo:rate-skill' with its invocation_id. Float it to the head
    // so the bar jumps straight to rating that exact call.
    useEffect(() => {
      function onReq(e) {
        const id = e && e.detail && e.detail.invocation_id;
        if (id == null) return;
        priorityRef.current = id;
        setQueue((q) => floatPriority(q, id));
      }
      window.addEventListener("echo:rate-skill", onReq);
      return () => window.removeEventListener("echo:rate-skill", onReq);
    }, []);

    // Fade controller. When a head exists, mount it and fade in; when the
    // queue drains, fade out then unmount. Keyed on currentId so an
    // item→item advance is an instant swap, only empty↔non-empty fades.
    useEffect(() => {
      if (current) {
        setDisplayItem(current);
        const r = window.requestAnimationFrame(() => setShown(true));
        return () => window.cancelAnimationFrame(r);
      }
      setShown(false);
      const t = setTimeout(() => setDisplayItem(null), FADE_MS);
      return () => clearTimeout(t);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [currentId]);

    // Commit the head item's rating to the backend and advance the queue.
    // The POST only happens here (window expiry or reason submit), so undo is
    // purely client-side. invocation_id pins the rating to THIS exact call.
    const commitCurrent = useCallback((rt, reason) => {
      if (!current) return;
      const inv = current;
      handledRef.current.add(inv.invocation_id);
      if (priorityRef.current === inv.invocation_id) priorityRef.current = null;
      setSubmitting(true);
      apiPost("/feedback", {
        skill_id: inv.skill_id,
        rating: rt,
        reason: reason || null,
        invocation_id: inv.invocation_id,
      })
        .catch((e) => setError((e && e.message) || String(e)))
        .finally(() => setSubmitting(false));
      setQueue((q) => q.slice(1)); // advance immediately; POST is best-effort
    }, [current]);

    // Tap a thumb → enter the `rated` window (the timer effect below starts).
    // Tapping the other thumb just switches the rating, still in `rated`.
    const choose = useCallback((rt) => {
      setRating(rt);
      setMode("rated");
    }, []);

    // RATE_WINDOW_MS countdown — runs ONLY in `rated`. Undo (→idle) and
    // ✎理由 (→reason) leave `rated`, which tears the timer down (no commit).
    // If it elapses untouched, the bare rating commits and the queue advances.
    useEffect(() => {
      if (mode !== "rated") { setRemain(0); return; }
      const deadline = Date.now() + RATE_WINDOW_MS;
      setRemain(Math.ceil(RATE_WINDOW_MS / 1000));
      const iv = setInterval(() => {
        setRemain(Math.max(0, Math.ceil((deadline - Date.now()) / 1000)));
      }, 500);
      const to = setTimeout(() => commitCurrent(rating, null), RATE_WINDOW_MS);
      return () => { clearInterval(iv); clearTimeout(to); };
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [mode, currentId, rating]);

    // ---- render (all hooks above have already run) ---------------------

    if (!sessionId) return null;

    // Session-scoped scope confirmation takes priority over rating.
    if (pendingScope) {
      return h(ScopeQuestion, {
        skill: pendingScope,
        onAnswered: onScopeAnswered,
      });
    }

    // Queue empty and fade-out finished → nothing mounted.
    if (!displayItem) return null;

    // `current` may be null during the fade-out frame; fall back to the
    // item being faded so the bar still has content to show.
    const item = current || displayItem;

    const convLabel =
      (item.session_title && String(item.session_title).trim()) ||
      (item.session_id ? item.session_id.slice(0, 8) + "…" : "—");
    const queueTail = Math.max(0, queue.length - 1);

    // Right-side controls depend on the per-item mode.
    let controls = null;
    if (mode === "idle") {
      const thumbBtn = (rt) =>
        h("button", {
          className: "echo-rate-btn",
          style: { "--echo-accent": rt === 1 ? ECHO_TEAL : ECHO_CORAL },
          title: rt === 1 ? "好评" : "差评",
          "aria-label": rt === 1 ? "好评" : "差评",
          onClick: () => choose(rt),
          disabled: submitting,
        }, thumbIcon(rt === 1));
      controls = h("div", { className: "flex items-center gap-2 mr-2 shrink-0" },
        thumbBtn(1),
        thumbBtn(-1),
      );
    } else if (mode === "rated") {
      // Order left → right: 理由 · 撤销 · 已评价, so the chip (the persistent
      // status) anchors the far right and the actions sit beside the content.
      controls = h("div", { className: "flex items-center gap-2 mr-2 shrink-0" },
        h("button", {
          className: "echo-mini-btn",
          // Open reason → timer stops; commit waits for the user's submit.
          onClick: () => setMode("reason"),
        }, "✎ 理由"),
        h("button", {
          className: "echo-mini-btn",
          // Undo → back to idle. Timer stops; nothing reaches the backend.
          onClick: () => setMode("idle"),
        }, "撤销" + (remain ? " (" + remain + ")" : "")),
        thumbChip(rating === 1, "已评价"),
      );
    } else if (mode === "reason") {
      // Reason editor is grouped on the right next to where the ✎理由 button
      // was — short single-line input + 取消 / 提交, all on the SAME row so the
      // bar height never changes.
      controls = h("div", {
        className: "flex items-center gap-2 mr-2 shrink-0",
        style: { "--echo-accent": rating === 1 ? ECHO_TEAL : ECHO_CORAL },
      },
        h("span", {
          className: "shrink-0",
          style: { color: rating === 1 ? ECHO_TEAL : ECHO_CORAL, display: "inline-flex" },
          title: rating === 1 ? "好评" : "差评",
        }, thumbIcon(rating === 1)),
        h("input", {
          className: "echo-reason-input min-w-0",
          style: { width: "18rem" },
          placeholder: "补充理由…",
          value: reasonText,
          onChange: (e) => setReasonText(e.target.value),
          autoFocus: true,
          onKeyDown: (e) => {
            if (e.key === "Enter") commitCurrent(rating, reasonText.trim() || null);
            else if (e.key === "Escape") setMode("rated");
          },
        }),
        h("button", {
          className: "echo-mini-btn",
          // Cancel → back to the undo window (rating preserved, timer restarts).
          onClick: () => setMode("rated"),
        }, "取消"),
        h("button", {
          className: "echo-accent-btn",
          disabled: submitting,
          onClick: () => commitCurrent(rating, reasonText.trim() || null),
        }, "提交"),
      );
    }

    // Plain flex spacer keeps the controls right-aligned; the row holds one
    // fixed height in every mode (the reason input now lives in `controls`).
    const middle = h("div", { className: "flex-1" });

    // The reason input is short and lives on the right now, so the meta can
    // stay visible in every mode (it truncates if space gets tight).
    const showMeta = true;

    const bar = h("div", {
      className: "flex items-center gap-2 px-3 text-xs border-t border-zinc-800 bg-zinc-950/60",
      style: { height: "3.25rem" },
    },
      h("span", { className: "text-zinc-600 shrink-0" }, "Echo"),
      showMeta && h("span", {
        className: "text-zinc-400 truncate max-w-[24%]",
        title: item.session_id || "",
      }, convLabel),
      showMeta && h("span", { className: "text-zinc-600 shrink-0" }, "—"),
      h("span", {
        className: "font-mono text-zinc-300 truncate max-w-[24%]",
        title: item.skill_id,
      }, item.skill_id),
      showMeta && queueTail > 0
        ? h("span", {
            className: "text-zinc-600 shrink-0",
            title: queueTail + " 个待评价",
          }, "+" + queueTail)
        : null,
      middle,
      error
        ? h("span", {
            className: "text-rose-400 shrink-0",
            title: String(error),
          }, "⚠")
        : null,
      controls,
    );

    // Fade wrapper — opacity transition on the whole bar's appear/disappear.
    return h("div", {
      className: cn(
        "transition-opacity ease-in-out",
        shown ? "opacity-100" : "opacity-0",
      ),
      style: { transitionDuration: FADE_MS + "ms" },
    }, bar);
  }

  // ---------------------------------------------------------------------
  // Register with the dashboard
  // ---------------------------------------------------------------------

  window.__HERMES_PLUGINS__.register("echo_signals", EchoPage);
  window.__HERMES_PLUGINS__.registerSlot("echo_signals", "chat:bottom", ThumbsBar);
})();
