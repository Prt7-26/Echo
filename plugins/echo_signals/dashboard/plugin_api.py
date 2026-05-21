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
    return response
