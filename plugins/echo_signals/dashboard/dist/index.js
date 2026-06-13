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
  const { useState, useEffect, useCallback } = SDK.hooks;
  const C = SDK.components;
  const cn = SDK.utils && SDK.utils.cn ? SDK.utils.cn : (...xs) => xs.filter(Boolean).join(" ");
  const fetchJSON = SDK.fetchJSON;
  const h = React.createElement;

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
      // Header
      h("div", { className: "flex items-end justify-between" },
        h("div", null,
          h("h1", { className: "text-2xl font-bold text-zinc-100" }, "Echo"),
          h("p", { className: "text-sm text-zinc-400 mt-1" },
            "User-signal-driven skill lifecycle. Confidence updates flow from explicit feedback, language sentiment, and behavior-drift detection."),
        ),
        h("button", {
          className: "text-xs text-zinc-400 hover:text-zinc-200 px-3 py-1.5 border border-zinc-800 rounded hover:border-zinc-600",
          onClick: refresh,
        }, "Refresh"),
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
  // hooks. Instead we mount a thin status bar at chat:bottom showing
  // "current skill" + thumbs. "Current" is defined as the most recent
  // echo_skill_invocation row — Echo's last-skill-wins rule from
  // session_context applies here too.
  //
  // Interaction: tap a thumb to submit ±1 immediately. Long-press (≥500ms)
  // expands a textarea so the user can attach a free-form reason; submit
  // sends {rating, reason}. The two-tier design keeps the common path
  // friction-free while letting users supply richer signal when they care.

  const LONG_PRESS_MS = 500;
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

  function ThumbsBar() {
    const [recent, setRecent] = useState(null);
    const [pendingScope, setPendingScope] = useState(null);
    const [error, setError] = useState(null);
    const [submitting, setSubmitting] = useState(false);
    const [lastSubmit, setLastSubmit] = useState(null);
    // detailMode is the long-press expansion — {rating, reason} draft.
    const [detailMode, setDetailMode] = useState(null);
    // Local ack list of skills the user already answered scope for this
    // session — prevents the question from flashing back if the API
    // poll races the local write.
    const [scopeAnswered, setScopeAnswered] = useState(new Set());

    // Poll both endpoints. ScopeQuestion takes precedence in the render
    // when pendingScope is non-null.
    useEffect(() => {
      let cancelled = false;
      function tick() {
        apiGet("/invocations/recent?limit=1")
          .then((d) => {
            if (cancelled) return;
            setError(null);
            setRecent((d.invocations && d.invocations[0]) || null);
          })
          .catch((e) => {
            if (cancelled) return;
            setError(e.message || String(e));
          });
        apiGet("/scope/pending?limit=1")
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
    }, [scopeAnswered]);

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

    // NOTE: every hook (useState/useEffect/useCallback) MUST run on every
    // render. `submit` therefore lives ABOVE the conditional early returns
    // below — moving it under `if (pendingScope) return` would change the
    // hook count between renders (9 vs 10) the moment a scope question
    // appears, which makes React throw "rendered fewer hooks than expected"
    // and unmounts the whole chat-bottom widget. That bug is exactly the
    // "thumbs never show up after a skill is created" symptom.
    const submit = useCallback((rating, reason) => {
      if (!recent || !recent.skill_id || submitting) return;
      setSubmitting(true);
      apiPost("/feedback", { skill_id: recent.skill_id, rating, reason: reason || null })
        .then((res) => {
          setLastSubmit({
            ts: Date.now(),
            ok: res.applied,
            rating,
            skill_id: recent.skill_id,
            reason: res.reason || null,
          });
          setDetailMode(null);
        })
        .catch((e) => {
          setLastSubmit({
            ts: Date.now(),
            ok: false,
            rating,
            skill_id: recent.skill_id,
            reason: (e && e.message) || String(e),
          });
        })
        .finally(() => setSubmitting(false));
    }, [recent, submitting]);

    // PRIORITY: pending scope wins over thumbs. (Conditional render only —
    // all hooks above have already run, so the hook count is stable.)
    if (pendingScope) {
      return h(ScopeQuestion, {
        skill: pendingScope,
        onAnswered: onScopeAnswered,
      });
    }

    // Long-press tracker — pointer down starts a timer; tap fires submit
    // unless the timer already opened detail mode.
    function makePressHandlers(rating) {
      let timer = null;
      let openedDetail = false;
      return {
        onPointerDown: () => {
          openedDetail = false;
          timer = window.setTimeout(() => {
            openedDetail = true;
            setDetailMode({ rating, reason: "" });
          }, LONG_PRESS_MS);
        },
        onPointerUp: () => {
          if (timer) { window.clearTimeout(timer); timer = null; }
          if (!openedDetail) submit(rating);
        },
        onPointerLeave: () => {
          if (timer) { window.clearTimeout(timer); timer = null; }
        },
        onContextMenu: (ev) => ev.preventDefault(),
      };
    }

    if (error) {
      return h("div", {
        className: "px-3 py-1.5 text-xs text-rose-400 border-t border-zinc-800",
      }, "Echo: " + error);
    }

    // No active invocation — render nothing to avoid clutter when Echo
    // has no data to act on yet.
    if (!recent || !recent.skill_id) return null;

    const bar = h("div", {
      className: "flex items-center gap-3 px-3 py-1.5 text-xs border-t border-zinc-800 bg-zinc-950/60",
    },
      h("span", { className: "text-zinc-500" }, "Echo active skill:"),
      h("span", { className: "font-mono text-zinc-300" }, recent.skill_id),
      h("div", { className: "flex-1" }),
      h("button", Object.assign({
        className: cn(
          "px-2 py-1 rounded border border-zinc-800 hover:border-emerald-700 hover:bg-emerald-950/40",
          submitting && "opacity-50 pointer-events-none",
        ),
        title: "Tap to submit. Long-press to add a reason.",
        disabled: submitting,
      }, makePressHandlers(1)), "👍"),
      h("button", Object.assign({
        className: cn(
          "px-2 py-1 rounded border border-zinc-800 hover:border-rose-700 hover:bg-rose-950/40",
          submitting && "opacity-50 pointer-events-none",
        ),
        title: "Tap to submit. Long-press to add a reason.",
        disabled: submitting,
      }, makePressHandlers(-1)), "👎"),
      lastSubmit ? h("span", {
        className: cn(
          "ml-2 text-xs",
          lastSubmit.ok ? "text-emerald-400" : "text-amber-400",
        ),
      }, lastSubmit.ok
        ? "✓ recorded"
        : "skipped (" + (lastSubmit.reason || "unknown") + ")"
      ) : null,
    );

    // Detail mode: long-press opened a reason field. Render below the bar.
    if (!detailMode) return bar;

    return h("div", null, bar,
      h("div", { className: "px-3 py-2 border-t border-zinc-800 bg-zinc-950/60 space-y-2" },
        h("div", { className: "text-xs text-zinc-400" },
          "Adding a reason for ",
          h("span", {
            className: detailMode.rating === 1 ? "text-emerald-400" : "text-rose-400",
          }, detailMode.rating === 1 ? "👍 positive" : "👎 negative"),
          " on ", h("span", { className: "font-mono" }, recent.skill_id),
        ),
        h("textarea", {
          className: "w-full bg-zinc-900 border border-zinc-800 rounded p-2 text-xs text-zinc-200 font-mono",
          rows: 2,
          placeholder: "What worked / what didn't?",
          value: detailMode.reason,
          onChange: (e) => setDetailMode({ ...detailMode, reason: e.target.value }),
          autoFocus: true,
        }),
        h("div", { className: "flex gap-2 justify-end" },
          h("button", {
            className: "px-3 py-1 text-xs text-zinc-400 hover:text-zinc-200",
            onClick: () => setDetailMode(null),
          }, "Cancel"),
          h("button", {
            className: cn(
              "px-3 py-1 text-xs rounded border",
              detailMode.rating === 1
                ? "border-emerald-700 text-emerald-300 hover:bg-emerald-950/40"
                : "border-rose-700 text-rose-300 hover:bg-rose-950/40",
              submitting && "opacity-50 pointer-events-none",
            ),
            disabled: submitting,
            onClick: () => submit(detailMode.rating, detailMode.reason.trim() || null),
          }, "Submit"),
        ),
      ),
    );
  }

  // ---------------------------------------------------------------------
  // Register with the dashboard
  // ---------------------------------------------------------------------

  window.__HERMES_PLUGINS__.register("echo_signals", EchoPage);
  window.__HERMES_PLUGINS__.registerSlot("echo_signals", "chat:bottom", ThumbsBar);
})();
