"""Echo dashboard — REST endpoints mounted at /api/plugins/echo_signals/.

Five endpoints serve the four visualizations the dashboard plugin owns,
plus the Layer B feedback ingest path:

    GET  /skills                        — confidence ranking
    GET  /skills/{skill_id}/timeline    — raw signal stream for one skill
    GET  /status-distribution           — active/pending_review/retired counts
    GET  /invocations/recent            — recent skill loads with signal counts
    POST /feedback                      — Layer B thumbs up/down ingest

Sorting policy: ``/skills`` orders by confidence ASC (worst first) — the
dashboard view's first job is to surface skills the user should pay
attention to, not to celebrate the healthy ones.

This module is imported by hermes_cli/web_server.py's
``_mount_plugin_api_routes`` scanner. It runs in the same Python process
as the Hermes web server, so importing ``plugins.echo_signals.*`` works
naturally — no need for sys.path tricks.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from plugins.echo_signals import confidence as conf_mod
from plugins.echo_signals import db as echo_db
from plugins.echo_signals.confidence_actions import apply_signal_event

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rows_to_dicts(rows) -> list[dict]:
    """Convert sqlite3.Row objects to plain dicts for JSON serialization."""
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 0. Status / diagnostics
# ---------------------------------------------------------------------------


@router.get("/status")
def status():
    """One-shot diagnostic snapshot for the dashboard status strip.

    Reports the schema version, which embedding encoder is active, and
    the row count of every echo_* table. Cheap (COUNT is index-served
    by SQLite for these tables); intended to be safe to poll.
    """
    from plugins.echo_signals.schema import ECHO_SCHEMA_VERSION, ECHO_TABLES

    try:
        from plugins.echo_signals.embeddings import is_neural_active
        encoder = "neural" if is_neural_active() else "hashing"
    except Exception:
        encoder = "hashing"

    conn = echo_db.get_echo_conn()
    counts: dict[str, int] = {}
    for table in ECHO_TABLES:
        # Skip the version table — it always has one row, not interesting.
        if table == "echo_schema_version":
            continue
        try:
            counts[table] = int(conn.execute(
                f"SELECT COUNT(*) AS n FROM {table}"
            ).fetchone()["n"])
        except Exception:
            counts[table] = -1

    return {
        "schema_version": ECHO_SCHEMA_VERSION,
        "encoder": encoder,
        "table_row_counts": counts,
    }


# ---------------------------------------------------------------------------
# 1. Skill confidence ranking
# ---------------------------------------------------------------------------


@router.get("/skills")
def list_skills(
    status: Optional[str] = Query(
        None,
        pattern="^(active|pending_review|retired)$",
        description="Filter to one status; omit for all.",
    ),
    limit: int = Query(100, ge=1, le=500),
):
    """Skill confidence rows ordered by confidence ASC (worst first).

    The dashboard's primary use case is "which skills need my attention",
    so low confidence floats to the top. To see healthy skills sort
    client-side or query ?status=active and reverse.
    """
    conn = echo_db.get_echo_conn()
    if status:
        rows = conn.execute(
            "SELECT skill_id, confidence, status, locked, n_invocations, "
            "       n_signals, created_at, updated_at, retired_at "
            "FROM echo_skill_confidence "
            "WHERE status = ? "
            "ORDER BY confidence ASC, skill_id ASC "
            "LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT skill_id, confidence, status, locked, n_invocations, "
            "       n_signals, created_at, updated_at, retired_at "
            "FROM echo_skill_confidence "
            "ORDER BY confidence ASC, skill_id ASC "
            "LIMIT ?",
            (limit,),
        ).fetchall()
    return {"skills": _rows_to_dicts(rows)}


# ---------------------------------------------------------------------------
# 2. Single-skill signal timeline
# ---------------------------------------------------------------------------


@router.get("/skills/{skill_id}/timeline")
def get_skill_timeline(
    skill_id: str,
    limit: int = Query(200, ge=1, le=1000),
):
    """Recent signal events for one skill, most recent first."""
    conn = echo_db.get_echo_conn()
    skill_row = conn.execute(
        "SELECT skill_id, confidence, status, locked, n_invocations, "
        "       n_signals, created_at, updated_at, retired_at "
        "FROM echo_skill_confidence WHERE skill_id = ?",
        (skill_id,),
    ).fetchone()
    if skill_row is None:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")

    events = conn.execute(
        "SELECT event_id, invocation_id, layer, signal_type, "
        "       value_real, value_int, value_text, metadata, ts "
        "FROM echo_signal_event "
        "WHERE skill_id = ? "
        "ORDER BY ts DESC, event_id DESC "
        "LIMIT ?",
        (skill_id, limit),
    ).fetchall()
    return {
        "skill": dict(skill_row),
        "events": _rows_to_dicts(events),
    }


# ---------------------------------------------------------------------------
# 3. Status distribution (for the pie/donut chart)
# ---------------------------------------------------------------------------


@router.get("/status-distribution")
def get_status_distribution():
    """Count of skills in each status bucket.

    Returned as a list so the JSON payload has stable ordering and
    consumers can iterate without sorting dict keys.
    """
    conn = echo_db.get_echo_conn()
    rows = conn.execute(
        "SELECT status, COUNT(*) AS count "
        "FROM echo_skill_confidence "
        "GROUP BY status"
    ).fetchall()

    # Normalize to all three statuses so the chart always has the same
    # categories even when one bucket is empty.
    by_status = {r["status"]: r["count"] for r in rows}
    distribution = [
        {"status": s, "count": by_status.get(s, 0)}
        for s in ("active", "pending_review", "retired")
    ]
    return {"distribution": distribution}


# ---------------------------------------------------------------------------
# 4. Recent invocations list
# ---------------------------------------------------------------------------


@router.get("/invocations/recent")
def list_recent_invocations(limit: int = Query(50, ge=1, le=200)):
    """Most-recent skill invocations with per-row signal counts.

    Useful for debugging "why is this skill getting these signals" —
    drill into a recent invocation to see its time window.
    """
    conn = echo_db.get_echo_conn()
    rows = conn.execute(
        "SELECT i.invocation_id, i.skill_id, i.session_id, i.platform, "
        "       i.started_at, i.finished_at, i.task_summary, "
        "       (SELECT COUNT(*) "
        "        FROM echo_signal_event e "
        "        WHERE e.invocation_id = i.invocation_id) AS signal_count "
        "FROM echo_skill_invocation i "
        "ORDER BY i.started_at DESC, i.invocation_id DESC "
        "LIMIT ?",
        (limit,),
    ).fetchall()
    return {"invocations": _rows_to_dicts(rows)}


# ---------------------------------------------------------------------------
# 5. Layer B feedback ingest
# ---------------------------------------------------------------------------


class FeedbackPayload(BaseModel):
    skill_id: str = Field(..., min_length=1)
    rating: int = Field(..., description="+1 for thumbs up, -1 for thumbs down")
    reason: Optional[str] = Field(
        None,
        description="Optional user-provided reason (long-press detail mode).",
    )


class ScopePayload(BaseModel):
    skill_id: str = Field(..., min_length=1)
    scope_level: str = Field(..., pattern="^(broad|narrow)$")


class ClipboardSignalPayload(BaseModel):
    """Echo desktop shell reports clipboard / window-focus events.

    event_type:
      'clipboard_copy'   — text was copied to OS clipboard
      'clipboard_paste'  — text was pasted out (less commonly reported)
      'window_focus'     — Tauri window gained focus
      'window_blur'      — Tauri window lost focus
    """

    event_type: str = Field(
        ..., pattern="^(clipboard_copy|clipboard_paste|window_focus|window_blur)$",
    )
    text: Optional[str] = Field(None, max_length=8192)
    text_length: Optional[int] = Field(None, ge=0)


@router.post("/feedback")
def submit_feedback(payload: FeedbackPayload):
    """Receive Layer B explicit thumbs-up/down from the dashboard.

    +1 → explicit_positive confidence update (α = 0.10 raise)
    -1 → explicit_negative confidence update (γ = 0.30 multiplicative cut)

    Returns whether the update was applied; if not (locked skill or
    unknown skill_id), surfaces the reason so the UI can show why nothing
    happened.
    """
    if payload.rating not in (-1, 1):
        raise HTTPException(
            status_code=400, detail="rating must be +1 or -1",
        )
    event = "explicit_positive" if payload.rating == 1 else "explicit_negative"
    result = apply_signal_event(payload.skill_id, event)

    # M5: thumbs-up + applied → also persist the most recent cached
    # turn for this skill into the preference library. Rating 5 when
    # the user added a long-press reason, 4 otherwise — the explicit
    # reason is treated as stronger endorsement.
    preference_example_id: Optional[int] = None
    if payload.rating == 1 and result.applied:
        try:
            from plugins.echo_signals.preference_rag import (
                store_from_turn_cache_by_skill,
            )
            preference_rating = 5 if payload.reason else 4
            eid = store_from_turn_cache_by_skill(
                payload.skill_id, rating=preference_rating,
            )
            preference_example_id = eid if eid > 0 else None
        except Exception:
            # Preference store failures must not break the feedback flow.
            preference_example_id = None

    response = {
        "applied": result.applied,
        "skill_id": result.skill_id,
        "old_confidence": result.old_confidence,
        "new_confidence": result.new_confidence,
        "old_status": result.old_status,
        "new_status": result.new_status,
        "event": result.event,
    }
    if not result.applied:
        response["reason"] = result.reason
    if preference_example_id is not None:
        response["preference_example_id"] = preference_example_id
    return response


# ---------------------------------------------------------------------------
# 6. M2 — pending scope queue + scope confirmation
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 7. M5 — preference library browse / delete
# ---------------------------------------------------------------------------


@router.get("/preferences")
def list_preferences(
    limit: int = Query(50, ge=1, le=200),
    skill_id: Optional[str] = Query(None, description="Filter to one skill"),
    min_rating: int = Query(1, ge=1, le=5),
):
    """List stored preference examples (M5 RAG corpus).

    Sorted by composite_score DESC (highest-quality / freshest first)
    so the user sees their best examples up top. Each row carries the
    rating, skill_id, task_request, agent_output, and usage stats —
    enough for the dashboard to render an inspectable list with
    "delete" affordances.
    """
    conn = echo_db.get_echo_conn()
    if skill_id:
        rows = conn.execute(
            "SELECT example_id, task_request, agent_output, rating, "
            "       skill_id, task_type_tag, created_at, last_used_at, "
            "       use_count, composite_score "
            "FROM echo_preference_example "
            "WHERE rating >= ? AND skill_id = ? "
            "ORDER BY composite_score DESC NULLS LAST, created_at DESC "
            "LIMIT ?",
            (min_rating, skill_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT example_id, task_request, agent_output, rating, "
            "       skill_id, task_type_tag, created_at, last_used_at, "
            "       use_count, composite_score "
            "FROM echo_preference_example "
            "WHERE rating >= ? "
            "ORDER BY composite_score DESC NULLS LAST, created_at DESC "
            "LIMIT ?",
            (min_rating, limit),
        ).fetchall()
    return {"preferences": _rows_to_dicts(rows)}


@router.delete("/preferences/{example_id}")
def delete_preference(example_id: int):
    """Remove a preference example.

    Idempotent — deleting an already-gone row returns deleted=false
    rather than a 404, so the UI can refresh-after-delete without
    racing against a parallel deletion.
    """
    conn = echo_db.get_echo_conn()
    cur = conn.execute(
        "DELETE FROM echo_preference_example WHERE example_id = ?",
        (example_id,),
    )
    conn.commit()
    return {"deleted": cur.rowcount > 0, "example_id": example_id}


# ---------------------------------------------------------------------------
# 8. Tauri desktop-shell signals (clipboard + window focus)
# ---------------------------------------------------------------------------


@router.post("/clipboard-signal")
def submit_clipboard_signal(payload: ClipboardSignalPayload):
    """Receive a clipboard / window-focus event from the Echo desktop shell.

    Stored as a Layer A signal attributed to the most recent invocation
    (cheap "last-skill-wins" pairing — the shell doesn't know which
    invocation is active, so the backend picks the most recent one).
    If no invocation exists yet, the signal is silently dropped (no
    point retaining context-less events).

    Text body is bounded at 8 KB; we store only the length in value_int
    by default, plus the first 200 chars in value_text for analytics
    on what was copied. Full text is NOT persisted by design — Echo
    intentionally avoids becoming a clipboard log.
    """
    conn = echo_db.get_echo_conn()
    inv_row = conn.execute(
        "SELECT invocation_id, skill_id FROM echo_skill_invocation "
        "ORDER BY started_at DESC LIMIT 1",
    ).fetchone()
    if inv_row is None:
        return {"recorded": False, "reason": "no_active_invocation"}

    text_value = (payload.text or "")[:200] if payload.text else None
    text_len = payload.text_length
    if text_len is None and payload.text is not None:
        text_len = len(payload.text)

    import time as _time

    conn.execute(
        "INSERT INTO echo_signal_event "
        "(invocation_id, skill_id, layer, signal_type, value_int, value_text, ts) "
        "VALUES (?, ?, 'A', ?, ?, ?, ?)",
        (
            inv_row["invocation_id"],
            inv_row["skill_id"],
            payload.event_type,
            text_len,
            text_value,
            _time.time(),
        ),
    )
    conn.execute(
        "UPDATE echo_skill_confidence "
        "SET n_signals = n_signals + 1, updated_at = ? "
        "WHERE skill_id = ?",
        (_time.time(), inv_row["skill_id"]),
    )
    conn.commit()
    return {
        "recorded": True,
        "invocation_id": inv_row["invocation_id"],
        "skill_id": inv_row["skill_id"],
    }


@router.get("/scope/pending")
def list_pending_scopes(limit: int = Query(50, ge=1, le=200)):
    """Skills whose scope_level is still 'unknown' — needing user input.

    Most recent first so the dashboard shows the freshly-created skill
    at the top of the queue. The frontend polls this for the
    ThumbsBar's "pending scope confirmation" mode (Step 10).
    """
    conn = echo_db.get_echo_conn()
    rows = conn.execute(
        "SELECT skill_id, scope_level, created_at, updated_at "
        "FROM echo_skill_scope "
        "WHERE scope_level = 'unknown' "
        "ORDER BY created_at DESC "
        "LIMIT ?",
        (limit,),
    ).fetchall()
    return {"pending": _rows_to_dicts(rows)}


@router.get("/candidates")
def list_skill_candidates(
    limit: int = Query(20, ge=1, le=100),
    min_score: int = Query(30, ge=0, le=200),
):
    """M1 — invocations Echo nominates as skill-worthy.

    Score breakdown comes back per-row so the dashboard can show the
    user *why* a given invocation was flagged. The actual decision to
    create a skill (calling Hermes' skill_manage) stays with the user;
    Echo is the nominator, not the decider.
    """
    from plugins.echo_signals.m1_trigger import list_candidates

    candidates = list_candidates(limit=limit, min_score=min_score)
    return {
        "candidates": [
            {
                "invocation_id": c.invocation_id,
                "skill_id": c.skill_id,
                "score": c.score,
                "reasons": c.reasons,
                "user_turns": c.user_turns,
                "tool_calls": c.tool_calls,
                "has_save_intent": c.has_save_intent,
            }
            for c in candidates
        ],
    }


@router.post("/scope")
def set_scope(payload: ScopePayload):
    """User picks broad or narrow for a previously-pending skill.

    Idempotent / overwrite: writing again replaces the previous choice.
    Echo doesn't try to detect malicious flip-flopping — that's
    a user-trust concern, not a data-integrity one.
    """
    conn = echo_db.get_echo_conn()
    now = __import__("time").time()
    cur = conn.execute(
        "UPDATE echo_skill_scope "
        "SET scope_level = ?, user_confirmed_at = ?, updated_at = ? "
        "WHERE skill_id = ?",
        (payload.scope_level, now, now, payload.skill_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        # No scope row existed yet — create one in the chosen state.
        # This is the path for "user pre-tagged a skill before Echo
        # saw it created", which can happen if scope_dialog's hook
        # missed an old skill.
        conn.execute(
            "INSERT INTO echo_skill_scope "
            "(skill_id, scope_level, user_confirmed_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (payload.skill_id, payload.scope_level, now, now, now),
        )
        conn.commit()

    return {
        "skill_id": payload.skill_id,
        "scope_level": payload.scope_level,
    }
