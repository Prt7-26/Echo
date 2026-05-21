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


def _record_pending_scope(skill_name: str) -> None:
    """Insert an echo_skill_scope row with scope_level='unknown' if absent.

    Idempotent — re-creating the same skill (delete/recreate workflow)
    will not clobber an existing scope choice the user already made.
    If the user wants to re-confirm, they can clear the row via the
    dashboard.
    """
    conn = get_echo_conn()
    now = time.time()

    # Also seed the confidence row if this is a brand-new skill — that
    # way the dashboard's confidence-ranking widget shows the skill
    # immediately, even before its first bump_use fires.
    conn.execute(
        "INSERT OR IGNORE INTO echo_skill_confidence "
        "(skill_id, created_at, updated_at) VALUES (?, ?, ?)",
        (skill_name, now, now),
    )

    conn.execute(
        "INSERT OR IGNORE INTO echo_skill_scope "
        "(skill_id, scope_level, created_at, updated_at) "
        "VALUES (?, 'unknown', ?, ?)",
        (skill_name, now, now),
    )
    conn.commit()
