"""Periodic housekeeping for Echo's append-only tables.

Echo accumulates two kinds of debt over time:
  * echo_user_request_log — every user turn is appended (M1 condition
    4's history). Past the recurrence lookback window these rows
    serve no purpose.
  * echo_turn_cache — one row per session (UPSERT); orphaned rows for
    long-dead sessions can sit forever.

Rather than wiring into Hermes' cron/scheduler (which is prompt-
oriented, designed for agent-defined recurring tasks), we piggyback
on session lifecycle: on every `on_session_start` we check whether
24 hours have elapsed since the last GC. If so, we fire-and-forget a
daemon thread that runs the cleanup. Worst case across all users: one
GC per day per long-running process, which is the right cadence.

The check itself is microseconds (one lock + one timestamp compare).
The GC work is bounded by SQL DELETE limits and never blocks the
session-start hook itself.

State note: ``_last_gc_ts`` is in-process. On restart we re-run GC at
the next session start — harmless since the GC operations are
idempotent (DELETE WHERE ts < cutoff).
"""

from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)

GC_INTERVAL_SECONDS = 86400.0           # one day
TURN_CACHE_RETENTION_SECONDS = 7 * 86400.0   # 7 days for orphaned cache rows

_last_gc_ts: float = 0.0
_gc_lock = threading.Lock()


def maybe_run_gc() -> bool:
    """If a day has elapsed since the last GC, kick off a background pass.

    Returns True when a GC was scheduled (just kicked off, not finished).
    False means we're still inside the GC_INTERVAL window. Thread-safe.
    """
    global _last_gc_ts
    now = time.time()
    with _gc_lock:
        if now - _last_gc_ts < GC_INTERVAL_SECONDS:
            return False
        _last_gc_ts = now

    threading.Thread(
        target=_run_gc_tasks, name="echo_gc", daemon=True,
    ).start()
    return True


def _run_gc_tasks() -> None:
    """Run all maintenance tasks. Errors are logged and swallowed."""
    # 1. M1 user_request_log — drops rows past the recurrence retention.
    try:
        from . import m1_trigger

        deleted = m1_trigger.gc_old_requests()
        if deleted:
            logger.info("Echo GC: removed %d old user_request_log rows", deleted)
    except Exception as exc:
        logger.debug("Echo GC m1 step failed: %s", exc, exc_info=True)

    # 2. echo_turn_cache — drops rows older than the cache retention.
    try:
        from .db import get_echo_conn

        cutoff = time.time() - TURN_CACHE_RETENTION_SECONDS
        conn = get_echo_conn()
        cur = conn.execute(
            "DELETE FROM echo_turn_cache WHERE updated_at < ?",
            (cutoff,),
        )
        conn.commit()
        if cur.rowcount:
            logger.info("Echo GC: removed %d old turn_cache rows", cur.rowcount)
    except Exception as exc:
        logger.debug("Echo GC turn_cache step failed: %s", exc, exc_info=True)


def _reset_for_tests() -> None:
    """Reset the cooldown so tests can re-trigger GC deterministically."""
    global _last_gc_ts
    with _gc_lock:
        _last_gc_ts = 0.0
