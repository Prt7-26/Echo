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
from .session_context import get_current_invocation_id, get_session_id

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


def _recent_invocation_for_session(session_id: Optional[str]) -> Optional[int]:
    """Most recent skill invocation in a conversation, or None.

    Used to attribute a turn's signals (Layer A user_turn, Layer B NL
    sentiment, M1) to the conversation's current skill when the per-turn
    contextvar isn't populated — e.g. a follow-up "太棒了" turn that loads no
    skill of its own but is clearly feedback on the last skill's output.
    """
    if not session_id:
        return None
    try:
        row = get_echo_conn().execute(
            "SELECT invocation_id FROM echo_skill_invocation "
            "WHERE session_id = ? ORDER BY started_at DESC, invocation_id DESC "
            "LIMIT 1",
            (session_id,),
        ).fetchone()
        return int(row["invocation_id"]) if row is not None else None
    except Exception as exc:
        logger.debug("Echo _recent_invocation_for_session failed: %s", exc, exc_info=True)
        return None


def on_pre_llm_call(
    *,
    turn_type: str = "",
    user_message: Any = None,
    session_id: Any = None,
    platform: Any = None,
    **_kwargs: Any,
) -> None:
    """Two-layer record on each fresh user turn.

      Layer A — append a `user_turn` event (raw signal; aggregated to
                modification_round_count via COUNT(*)).
      Layer B — fire-and-forget NL sentiment classify on user_message;
                callback updates confidence with nl_positive /
                nl_negative when the label is non-neutral.

    Hermes' live conversation_loop fires pre_llm_call EXACTLY ONCE per turn,
    before the tool-calling loop, and does NOT pass a ``turn_type`` kwarg — so
    every fire we see is a fresh user utterance. We therefore treat a missing/
    empty turn_type as a user turn and only skip turns a caller explicitly tags
    as assistant/tool/system. (The earlier ``turn_type != "user"`` gate assumed
    Hermes tagged each fire; since it doesn't, that gate silently suppressed
    ALL Layer A / Layer B / M1 signals in the live runtime — they only ever
    fired in unit tests that passed turn_type="user" by hand.)
    """
    # Keep the session contextvar fresh so the bump_use wrapper can attribute
    # the invocation it later writes in this same turn. on_session_start fires
    # ONLY for brand-new conversations (conversation_loop.py builds the system
    # prompt from scratch), so a RESUMED conversation would otherwise leave the
    # context unset → bump_use writes echo_skill_invocation rows with a NULL
    # session_id + platform='unknown' → the per-conversation rating queue (and
    # any session-scoped query) never sees them. pre_llm_call fires on EVERY
    # turn (new and resumed) with the live session id, and runs before the
    # turn's tool loop where bump_use happens, so refresh it here unconditionally.
    if session_id:
        from .session_context import set_session_context
        set_session_context(str(session_id), (platform or None))

    # M2 (v9): capture the user's scope pick from a prior turn's clarify result.
    # clarify bypasses post_tool_call, but its result IS appended to the message
    # list, so we scan conversation_history here. Runs before the turn_type gate
    # because the clarify result lands on the turn AFTER the question was asked.
    try:
        from . import scope_clarify
        from .session_context import get_session_id
        scope_clarify.capture_scope_from_history(
            get_session_id() or (str(session_id) if session_id else None),
            _kwargs.get("conversation_history"),
        )
    except Exception as exc:
        logger.debug("Echo on_pre_llm_call(scope capture) failed: %s", exc, exc_info=True)

    if turn_type in ("assistant", "tool", "system"):
        return
    # Resolve which skill invocation this turn's signals attach to.
    #
    # The _current_invocation_id contextvar is set by bump_use — but bump_use
    # runs DURING the tool loop, AFTER pre_llm_call fires, and the contextvar
    # does not survive across turns (each gateway turn is a fresh context). So
    # at pre_llm_call time it is almost always None. Fall back to the most
    # recent invocation in THIS conversation — the skill the user's words are
    # about. May still be None: a SKILL-LESS conversation that never loaded any
    # skill. That is no longer a dead end — M1 below nominates skill-less
    # conversations as candidates for a NEW skill (proposal §M1's 孵化 intent).
    invocation_id = get_current_invocation_id()
    if invocation_id is None:
        invocation_id = _recent_invocation_for_session(get_session_id())

    # ── M1 — save-intent + recurrence (runs for skilled AND skill-less) ──
    # save_intent: regex scan for "save this as a skill" style phrases.
    # semantic_recurrence: hashing/neural embedding cosine match against the
    # user_request_log over the last RECURRENCE_LOOKBACK_DAYS days.
    #
    # For a skilled turn these attach to the active skill and ALSO emit Layer B
    # signal_event rows that the invocation-scoped list_candidates() reads. For
    # a skill-less turn there is no invocation/skill, so the per-turn flags live
    # only on echo_user_request_log; list_session_candidates() aggregates them
    # to nominate the whole conversation as a NEW skill.
    try:
        from . import m1_trigger
        from . import nl_classifier as _nlc

        user_text_for_intent = ""
        if isinstance(user_message, (str, dict, list)):
            user_text_for_intent = _nlc.extract_user_text(user_message) or ""

        if user_text_for_intent:
            if invocation_id is not None:
                from .db import get_echo_conn as _conn
                row = _conn().execute(
                    "SELECT skill_id, session_id FROM echo_skill_invocation "
                    "WHERE invocation_id = ?",
                    (invocation_id,),
                ).fetchone()
                skill_id_now = row["skill_id"] if row is not None else None
                session_id_now = row["session_id"] if row is not None else None
            else:
                skill_id_now = None
                session_id_now = get_session_id()

            # First: check recurrence against the PRIOR log (before inserting
            # this turn — otherwise it self-matches).
            hit, sim = m1_trigger.detect_semantic_recurrence(
                user_text_for_intent,
                current_invocation_id=invocation_id,
            )
            if hit and skill_id_now:
                m1_trigger.record_semantic_recurrence_signal(
                    invocation_id, skill_id_now, sim,
                )

            is_save = m1_trigger.detect_save_intent(user_text_for_intent)

            # Then: log this turn (carries the per-turn flags that
            # list_session_candidates aggregates for skill-less sessions).
            m1_trigger.log_user_request(
                invocation_id=invocation_id,
                skill_id=skill_id_now,
                session_id=session_id_now,
                user_message=user_text_for_intent,
                save_intent=is_save,
                recurrence_sim=(sim if sim > 0.0 else None),
            )

            # Skilled turns also emit the Layer B signal_event used by the
            # invocation-scoped candidate list.
            if is_save and skill_id_now:
                m1_trigger.record_save_intent_signal(invocation_id, skill_id_now)
    except Exception as exc:
        logger.debug("Echo on_pre_llm_call(m1) failed: %s", exc, exc_info=True)

    # Layer A user_turn and Layer B NL classify both update an EXISTING skill's
    # confidence, so they need an attributable invocation. A skill-less turn has
    # nothing to attribute to and stops here — M1 above already captured it.
    if invocation_id is None:
        # Active M1 nomination: a skill-less conversation that just crossed the
        # threshold gets a fire-and-forget dedup + ask/inform/create decision.
        try:
            from . import m1_nomination
            m1_nomination.maybe_start_nomination(get_session_id())
        except Exception as exc:
            logger.debug("Echo on_pre_llm_call(nomination) failed: %s",
                         exc, exc_info=True)
        return

    # ── Layer A — sync record ─────────────────────────────────────────
    try:
        record_signal(
            invocation_id=invocation_id,
            layer="A",
            signal_type="user_turn",
        )
    except Exception as exc:
        logger.debug("Echo on_pre_llm_call(user_turn) failed: %s", exc, exc_info=True)

    # ── Layer B — async NL classify ───────────────────────────────────
    # Pin skill_id NOW so a later bump_use flipping the contextvar can't
    # misattribute the eventual callback.
    try:
        from . import nl_classifier
        from .db import get_echo_conn

        text = nl_classifier.extract_user_text(user_message)
        if not text:
            return

        conn = get_echo_conn()
        row = conn.execute(
            "SELECT skill_id FROM echo_skill_invocation WHERE invocation_id = ?",
            (invocation_id,),
        ).fetchone()
        if row is None:
            return
        skill_id = row["skill_id"]

        def _on_label(label):
            from .confidence_actions import apply_signal_event
            from .db import get_echo_conn as _conn

            if label == "positive":
                event = "nl_positive"
            elif label == "negative":
                event = "nl_negative"
            else:
                return  # neutral — silence, the sacred invariant

            try:
                apply_signal_event(skill_id, event)
                conn2 = _conn()
                ts = time.time()
                conn2.execute(
                    "INSERT INTO echo_signal_event "
                    "(invocation_id, skill_id, layer, signal_type, value_text, ts) "
                    "VALUES (?, ?, 'B', ?, ?, ?)",
                    (invocation_id, skill_id, event, label, ts),
                )
                conn2.execute(
                    "UPDATE echo_skill_confidence "
                    "SET n_signals = n_signals + 1, updated_at = ? "
                    "WHERE skill_id = ?",
                    (ts, skill_id),
                )
                conn2.commit()
            except Exception as exc:
                logger.debug(
                    "Echo nl_classifier confidence update failed for %s/%s: %s",
                    skill_id, event, exc, exc_info=True,
                )

        nl_classifier.classify_async(text, _on_label)
    except Exception as exc:
        logger.debug("Echo on_pre_llm_call(nl_classify) failed: %s", exc, exc_info=True)


def tool_call_failed(result: Any) -> bool:
    """Heuristic 'did this tool call fail' over Hermes' heterogeneous tool
    results (proposal §M3 Layer A: '工具执行的 exit code').

    Conservative: we only return True on POSITIVE evidence of failure, so a
    normal result is never mislabeled (a false 'failed' would corrupt the
    skill's behavior baseline). In particular None and empty string are NOT
    failures — plenty of tools succeed with no output.

    Positive failure evidence:
      * dict with a truthy 'error'/'err' key, 'ok'==False, 'success'==False,
        'failed'==True, or a non-zero 'exit_code'/'returncode'/'status_code'
      * str starting with 'error'/'failed'/'traceback'/'exception'/'✗'
    Everything else (incl. None, '', normal strings, clean dicts) → success.
    """
    if isinstance(result, dict):
        for k in ("exit_code", "returncode", "status_code"):
            v = result.get(k)
            if isinstance(v, (int, float)) and int(v) != 0:
                return True
        if result.get("error") or result.get("err"):
            return True
        if result.get("ok") is False or result.get("success") is False:
            return True
        if result.get("failed") is True:
            return True
        return False
    if isinstance(result, str):
        head = result.strip().lower()
        if not head:
            return False
        return head.startswith(("error", "failed", "traceback", "exception", "✗"))
    return False


# Sentinel so we can tell "Hermes passed result=None (a real failure)"
# apart from "no result kwarg was passed at all (unknown — don't guess)".
_NO_RESULT = object()


def on_post_tool_call(*, tool_name: str = "", result: Any = _NO_RESULT,
                      **_kwargs: Any) -> None:
    """Record one tool_call event per completed tool execution, plus a
    tool_error event when the result looks like a failure.

    The tool_error count per invocation is a Layer A drift metric: a skill
    whose tool calls start failing more often than its baseline is drifting
    (proposal §M3 Layer A — exit codes feed the per-skill behavior baseline).

    If no ``result`` is supplied (the caller doesn't carry one), we record
    only the tool_call event and make NO success/failure claim — guessing
    would fabricate errors on every call.
    """
    invocation_id = get_current_invocation_id()
    if invocation_id is None:
        # No active skill — count this against the conversation instead, so
        # M1's tool-complexity condition can nominate a SKILL-LESS session as
        # a new-skill candidate (list_session_candidates reads this counter).
        try:
            from . import m1_trigger
            m1_trigger.record_session_tool_call(get_session_id())
        except Exception as exc:
            logger.debug("Echo on_post_tool_call(session count) failed: %s",
                         exc, exc_info=True)
        return
    try:
        record_signal(
            invocation_id=invocation_id,
            layer="A",
            signal_type="tool_call",
            value_text=tool_name or None,
        )
        if result is not _NO_RESULT and tool_call_failed(result):
            record_signal(
                invocation_id=invocation_id,
                layer="A",
                signal_type="tool_error",
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
