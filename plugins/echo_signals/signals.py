"""Layer A signal collectors and the hook handlers that fire them.

Step 3 records three event types into echo_signal_event, all attributed
to the active invocation (set by usage_hook's bump_use wrapper):

  * ``user_turn``  -- one row per (turn_type='user') pre_llm_call fire.
                     Aggregated to give modification_round_count.
  * ``tool_call``  -- one row per post_tool_call fire; value_text holds
                     the tool name. Step 3 only records event presence;
                     success/error parsing is deferred to Step 4.
  * ``session_ended`` -- one row per on_session_end while a skill is
                     still active. Used by M4 to detect "user bailed
                     out right after the skill ran" patterns.

All collectors short-circuit if get_current_invocation_id() returns None
-- that means no skill was loaded in this session, so there's nothing
for Echo to attribute the event to.

Every recording path is wrapped in try/except at the hook layer. Hermes
must never observe Echo throwing.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from .db import get_echo_conn
from .session_context import get_current_invocation_id

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Low-level write helper
# ---------------------------------------------------------------------------


def record_signal(
    *,
    invocation_id: int,
    layer: str,
    signal_type: str,
    value_real: Optional[float] = None,
    value_int: Optional[int] = None,
    value_text: Optional[str] = None,
    metadata: Optional[str] = None,
    ts: Optional[float] = None,
) -> None:
    """Insert one row into echo_signal_event.

    All three typed value columns are nullable; pass whichever ones are
    relevant to the signal_type. Caller is responsible for matching the
    signal_type's expected shape (we don't validate here -- a schema-
    level CHECK on (signal_type, value_*) would be too rigid as new
    signal types get added).
    """
    if ts is None:
        ts = time.time()
    conn = get_echo_conn()
    # We also denormalize skill_id onto the event row so per-skill
    # analytics queries don't need to JOIN through invocation. Look it
    # up from the invocation row.
    skill_id_row = conn.execute(
        "SELECT skill_id FROM echo_skill_invocation WHERE invocation_id = ?",
        (invocation_id,),
    ).fetchone()
    if skill_id_row is None:
        logger.debug("record_signal: invocation_id=%s not found, skipping", invocation_id)
        return
    skill_id = skill_id_row["skill_id"]

    conn.execute(
        "INSERT INTO echo_signal_event "
        "(invocation_id, skill_id, layer, signal_type, "
        " value_real, value_int, value_text, metadata, ts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (invocation_id, skill_id, layer, signal_type,
         value_real, value_int, value_text, metadata, ts),
    )
    conn.execute(
        "UPDATE echo_skill_confidence "
        "SET n_signals = n_signals + 1, updated_at = ? "
        "WHERE skill_id = ?",
        (ts, skill_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Hook handlers
# ---------------------------------------------------------------------------


def on_pre_llm_call(*, turn_type: str = "", **_kwargs: Any) -> None:
    """Record a user_turn event when the user triggers a fresh LLM call.

    Hermes fires pre_llm_call multiple times per turn (context injection,
    actual API request). Only turn_type='user' represents a fresh user
    utterance -- the others are internal plumbing we don't want to count.
    """
    if turn_type != "user":
        return
    invocation_id = get_current_invocation_id()
    if invocation_id is None:
        return
    try:
        record_signal(
            invocation_id=invocation_id,
            layer="A",
            signal_type="user_turn",
        )
    except Exception as exc:
        logger.debug("Echo on_pre_llm_call(user_turn) failed: %s", exc, exc_info=True)


def on_post_tool_call(*, tool_name: str = "", **_kwargs: Any) -> None:
    """Record one tool_call event per completed tool execution.

    Step 3 records event presence only -- value_text holds the tool's
    name. Success/error parsing of the result is deferred to Step 4
    (different tools have different result shapes; not worth a one-size
    parser now).
    """
    invocation_id = get_current_invocation_id()
    if invocation_id is None:
        return
    try:
        record_signal(
            invocation_id=invocation_id,
            layer="A",
            signal_type="tool_call",
            value_text=tool_name or None,
        )
    except Exception as exc:
        logger.debug("Echo on_post_tool_call failed: %s", exc, exc_info=True)


def on_session_end_signal(**_kwargs: Any) -> None:
    """Record session_ended if a skill was active for this session.

    Important: this is the Layer A signal half of session-end handling.
    The companion call clear_session_context() (lives in __init__.py)
    must run *after* this so we still have the invocation_id to attribute
    the event to.
    """
    invocation_id = get_current_invocation_id()
    if invocation_id is None:
        return
    try:
        record_signal(
            invocation_id=invocation_id,
            layer="A",
            signal_type="session_ended",
        )
    except Exception as exc:
        logger.debug("Echo on_session_end_signal failed: %s", exc, exc_info=True)
