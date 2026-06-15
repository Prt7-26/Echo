"""Active M1 nomination — ask the user before creating a new skill.

The dashboard candidate widgets are PASSIVE: they list conversations Echo
thinks are skill-worthy and wait for the user to look. This module is the
ACTIVE half: when a skill-less conversation crosses the nomination threshold,
Echo runs a skill-library dedup check and then, depending on how the need was
detected, nudges the agent to *ask the user* (via Hermes' clarify tool) or to
inform them — never silently creating a skill.

Decision matrix (set by the maintainer):

  trigger      dedup result   state    → nudge injected next turn
  ───────────  ─────────────  ───────  ─────────────────────────────────────
  save_intent  similar found  inform   tell the user skill X already exists,
  (explicit)                           ask reuse-vs-new via clarify
  save_intent  nothing        create   create the skill directly (skill_manage)
  implicit     similar found  skip     (silent — the need is already met)
  implicit     nothing        ask      ask the user via clarify; on yes, create

"implicit" = recurrence / tool-count / modification investment. The dedup
check + decision run in a fire-and-forget daemon thread (Echo's standard aux
pattern), so the user's turn is never blocked. The decision lands in
echo_session_nomination; the NEXT turn's inject channel reads it and appends
the nudge to the user message (cache-safe — never touches the system prompt).

At most one nomination per conversation (the row is keyed by session_id), so
the user is never re-asked about the same chat.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from .db import get_echo_conn

logger = logging.getLogger(__name__)


# States that carry a pending nudge for the inject channel.
_NUDGE_STATES = ("ask", "inform", "create")


# ---------------------------------------------------------------------------
# Entry point — called from signals.on_pre_llm_call after a turn is logged
# ---------------------------------------------------------------------------


def maybe_start_nomination(session_id: Optional[str]) -> None:
    """If this skill-less conversation now qualifies and hasn't been handled,
    record a pending nomination and kick off the async dedup + decision.

    Idempotent: a session that already has a nomination row is left alone.
    """
    if not session_id:
        return
    try:
        from . import m1_trigger

        conn = get_echo_conn()
        existing = conn.execute(
            "SELECT 1 FROM echo_session_nomination WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if existing is not None:
            return

        cand = m1_trigger.evaluate_session(session_id)
        if cand is None:
            return

        trigger_kind = "save_intent" if cand.has_save_intent else "implicit"
        task_text = cand.first_message or ""
        now = time.time()
        conn.execute(
            "INSERT OR IGNORE INTO echo_session_nomination "
            "(session_id, trigger_kind, state, task_text, created_at) "
            "VALUES (?, ?, 'pending', ?, ?)",
            (session_id, trigger_kind, task_text, now),
        )
        conn.commit()
        _start_dedup_async(session_id, task_text, trigger_kind)
    except Exception as exc:
        logger.debug("maybe_start_nomination failed: %s", exc, exc_info=True)


# ---------------------------------------------------------------------------
# Async dedup + decision
# ---------------------------------------------------------------------------


def _start_dedup_async(session_id: str, task_text: str, trigger_kind: str) -> None:
    """Spawn the dedup+decide work on a daemon thread. Test seam: conftest
    stubs this so unit tests don't spawn threads / hit a real LLM; the tests
    that exercise the decision proper call ``decide_nomination`` directly."""
    t = threading.Thread(
        target=decide_nomination,
        args=(session_id, task_text, trigger_kind),
        daemon=True,
    )
    t.start()


def decide_nomination(session_id: str, task_text: str, trigger_kind: str) -> str:
    """Run the dedup check and apply the decision matrix. Returns the chosen
    state (also for tests). Fail-soft: on any error, leaves the row 'pending'.
    """
    try:
        from . import skill_dedup

        result = skill_dedup.check_duplicate(task_text)
        matched = result.match
        reason = result.reason

        if trigger_kind == "save_intent":
            state = "inform" if matched else "create"
        else:  # implicit
            state = "skip" if matched else "ask"

        conn = get_echo_conn()
        conn.execute(
            "UPDATE echo_session_nomination "
            "SET state = ?, dedup_skill = ?, dedup_reason = ?, decided_at = ? "
            "WHERE session_id = ?",
            (state, matched, reason, time.time(), session_id),
        )
        conn.commit()
        return state
    except Exception as exc:
        logger.debug("decide_nomination failed: %s", exc, exc_info=True)
        return "pending"


# ---------------------------------------------------------------------------
# Inject channel — consume the pending nudge (called from on_pre_llm_call_inject)
# ---------------------------------------------------------------------------


def _build_nudge(state: str, task_text: str, dedup_skill: Optional[str],
                 dedup_reason: Optional[str]) -> Optional[str]:
    """Compose the directive appended to the next user message. Chinese,
    matching the maintainer's main-chat language; tool/skill names stay in
    English so tool-calling is unambiguous."""
    if state == "ask":
        return (
            "[Echo 提示] 你和用户的这段对话看起来是一个可复用的工作流程，"
            "而技能库里还没有类似的技能。请在回答完用户当前的问题之后，"
            "使用 clarify 工具询问用户是否要把这个流程保存成一个新技能"
            "（选项例如：是，保存为技能 / 不用了）。如果用户选择「是」，"
            "请用 skill_manage 工具（action=create）创建这个技能。"
            "如果用户当前的话与保存技能无关，正常回答即可。"
        )
    if state == "inform":
        sk = dedup_skill or "(unknown)"
        why = f"（{dedup_reason}）" if dedup_reason else ""
        return (
            f"[Echo 提示] 用户希望把这个流程保存为技能，但技能库里已经有一个"
            f"功能类似的技能「{sk}」{why}。请先告知用户已存在「{sk}」，"
            f"再用 clarify 工具询问：直接复用现有的「{sk}」，还是仍要新建一个"
            f"（选项例如：复用现有 / 新建一个）。如果用户选择新建，"
            f"请用 skill_manage 工具（action=create）创建。"
        )
    if state == "create":
        return (
            "[Echo 提示] 用户明确希望把这个流程保存为技能，且技能库里没有"
            "功能类似的技能。请用 skill_manage 工具（action=create）把它"
            "创建为一个新技能；创建前可以简要和用户确认一下技能名称。"
        )
    return None


def consume_nudge(session_id: Optional[str]) -> Optional[str]:
    """Return the pending nudge directive for this session and mark it
    consumed (state→'done'), or None if there's nothing to inject.

    Marking consumed in the same call guarantees the nudge is injected at
    most once even though the inject channel may fire on every turn.
    """
    if not session_id:
        return None
    try:
        conn = get_echo_conn()
        row = conn.execute(
            "SELECT state, task_text, dedup_skill, dedup_reason "
            "FROM echo_session_nomination "
            "WHERE session_id = ? AND nudged_at IS NULL",
            (session_id,),
        ).fetchone()
        if row is None or row["state"] not in _NUDGE_STATES:
            return None
        text = _build_nudge(
            row["state"], row["task_text"] or "",
            row["dedup_skill"], row["dedup_reason"],
        )
        if not text:
            return None
        conn.execute(
            "UPDATE echo_session_nomination "
            "SET state = 'done', nudged_at = ? WHERE session_id = ?",
            (time.time(), session_id),
        )
        conn.commit()
        return text
    except Exception as exc:
        logger.debug("consume_nudge failed: %s", exc, exc_info=True)
        return None
