"""M2 — applicability-scope confirmation flow.

Proposal §M2 wants the user to be asked, right after a skill is
created, whether the skill should generalize broadly across similar
tasks or narrowly to its current context. The answer becomes the
skill's stored scope_level — broad / narrow / unknown — and gates the
retrieval matching that future invocations do.

Hermes-side constraints we found while exploring (curator survey):

  * There is no `pre_skill_create` / `post_skill_create` hook. The
    SKILL.md write in tools.skill_manager_tool happens with zero
    `invoke_hook` calls around it.
  * `pre_tool_call` is observer-only — it sees calls but can't block
    or modify them.
  * Therefore: Echo cannot intercept *before* the write. The cleanest
    available wedge is `post_tool_call` filtered to
    ``tool_name == 'skill_manage'`` with ``args.action == 'create'``.

Behavior implemented here (Phase A):

  * The post_tool_call hook handler inserts an ``echo_skill_scope`` row
    with ``scope_level='unknown'`` so Echo knows "this skill exists and
    needs a scope decision". The frontend surfaces these and lets the
    user pick.
  * We do NOT block the agent loop or call clarify_tool from the hook.
    Hermes' clarify_tool wants to live inside an agent's tool-call
    conversation; injecting one from a fire-and-forget plugin path
    would risk fighting the agent for control. The dashboard
    ThumbsBar slot (Phase B, Step 10) is a calmer surface for the
    question.

Result-shape robustness: skill_manage(action='create') can fail (name
collision, schema error). When that happens the tool returns an error
payload, not a success row. We treat *any* successful-looking
post_tool_call for action='create' as ground for inserting a pending
scope row; the worst case is one unused row that the user ignores.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from .db import get_echo_conn

logger = logging.getLogger(__name__)


def _extract_action_and_name(args: Any) -> tuple[str, str]:
    """Return (action, name) from a skill_manage call's args, or ('', '')."""
    if not isinstance(args, dict):
        return ("", "")
    action = args.get("action") or ""
    name = args.get("name") or ""
    if not isinstance(action, str) or not isinstance(name, str):
        return ("", "")
    return (action.strip(), name.strip())


def _looks_like_create_success(result: Any) -> bool:
    """Heuristic: did the create succeed enough to warrant a scope row?

    skill_manage returns a JSON-stringified payload. We treat any
    non-error response as success — false positives only cost an
    unused echo_skill_scope row, which is benign.
    """
    if result is None:
        return False
    if isinstance(result, dict):
        # A typical error has an `error` key. Absence ≈ success.
        return "error" not in result and not result.get("failed")
    if isinstance(result, str):
        # JSON envelope or plain text — assume success unless the
        # word "error" appears at a top-ish position.
        low = result.lower().strip()
        if low.startswith('{"error"') or low.startswith("error:"):
            return False
        return True
    return True


def on_post_tool_call(
    *,
    tool_name: str = "",
    args: Any = None,
    result: Any = None,
    **_kwargs: Any,
) -> None:
    """Detect skill_manage(action='create') and record pending scope."""
    if tool_name != "skill_manage":
        return

    action, skill_name = _extract_action_and_name(args)
    if action != "create" or not skill_name:
        return

    if not _looks_like_create_success(result):
        logger.debug(
            "scope_dialog: skill_manage(create) for %s looked like a failure, skipping",
            skill_name,
        )
        return

    try:
        _record_pending_scope(skill_name)
    except Exception as exc:
        # As elsewhere — Echo failures must not break Hermes tool flow.
        logger.debug(
            "scope_dialog._record_pending_scope(%s) failed: %s",
            skill_name, exc, exc_info=True,
        )

    # M2 scope confirmation is asked IN-TURN by the agent (driven by the
    # m1_nomination directive's _SCOPE_ASK_SUFFIX), then captured from
    # conversation_history by scope_clarify.capture_scope_from_history. We no
    # longer pre-generate options here / inject a next-turn nudge: that path
    # depended on the user sending another turn, which they usually don't right
    # after creating a skill, so the question never got asked.


# How far back (seconds) to look for an m1_save_intent signal when
# deciding a newly-created skill's initial confidence. If the user said
# "save this as a skill" shortly before the skill was created, the skill
# starts with a higher prior (proposal §M4).
SAVE_INTENT_LOOKBACK_SECONDS = 300


def _recent_save_intent(conn, lookback_s: float = SAVE_INTENT_LOOKBACK_SECONDS) -> bool:
    """True if an m1_save_intent signal fired within the lookback window.

    This is the "user explicitly asked to save this" context the proposal
    uses to justify a higher initial confidence. Session-agnostic recency
    check — a save-intent in the last few minutes immediately followed by a
    skill creation is the signal we want.
    """
    cutoff = time.time() - lookback_s
    row = conn.execute(
        "SELECT 1 FROM echo_signal_event "
        "WHERE signal_type = 'm1_save_intent' AND ts >= ? LIMIT 1",
        (cutoff,),
    ).fetchone()
    return row is not None


def _record_pending_scope(skill_name: str) -> None:
    """Insert an echo_skill_scope row with scope_level='unknown' if absent.

    Idempotent — re-creating the same skill (delete/recreate workflow)
    will not clobber an existing scope choice the user already made.
    If the user wants to re-confirm, they can clear the row via the
    dashboard.
    """
    from .confidence import INITIAL_CONFIDENCE, INITIAL_CONFIDENCE_SAVE_INTENT

    conn = get_echo_conn()
    now = time.time()

    # Also seed the confidence row if this is a brand-new skill — that
    # way the dashboard's confidence-ranking widget shows the skill
    # immediately, even before its first bump_use fires. Initial
    # confidence is context-dependent (proposal §M4): a skill created
    # right after the user said "save this" starts with a higher prior.
    initial_c = (
        INITIAL_CONFIDENCE_SAVE_INTENT
        if _recent_save_intent(conn)
        else INITIAL_CONFIDENCE
    )
    conn.execute(
        "INSERT OR IGNORE INTO echo_skill_confidence "
        "(skill_id, confidence, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (skill_name, initial_c, now, now),
    )

    # Bind the scope question to the conversation that created the skill, so
    # the dashboard only surfaces it there (not in unrelated conversations).
    from .session_context import get_session_id
    creating_session = get_session_id()
    conn.execute(
        "INSERT OR IGNORE INTO echo_skill_scope "
        "(skill_id, scope_level, created_at, updated_at, session_id) "
        "VALUES (?, 'unknown', ?, ?, ?)",
        (skill_name, now, now, creating_session),
    )
    conn.commit()
