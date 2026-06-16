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


# Appended to every directive that may lead to skill creation. The scope
# question MUST be asked in the SAME turn, right after creating — Echo's M2
# scope confirmation otherwise depends on the user sending another turn, which
# they usually don't after "save it". The agent summarizes the options itself
# (the just-created skill's content isn't available to Echo's aux LLM in-turn).
# The question text is required to contain "适用范围" so Echo's
# scope_clarify.capture_scope_from_history can recognize and record the answer.
_SCOPE_ASK_SUFFIX = (
    "技能创建成功后，请**紧接着在同一轮里再用一次 clarify 工具**确认这个技能的适用范围："
    "问题文本必须包含「适用范围」四个字（例如『这个技能以后的适用范围是什么？』），"
    "并由你根据技能内容总结 2–4 个由窄到宽的中文选项让用户选择"
    "（从「只用于当前这种具体任务」到「适用于更通用的一类任务」）。"
)


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
            "[Echo 系统指令——必须执行工具调用，不能只用文字] "
            "这段对话已达到「值得沉淀为可复用技能」的标准，且技能库中没有同类技能。"
            "在你对用户当前消息给出正常回复之后，你**必须立即调用 clarify 工具**"
            "（不要用普通文字提问，必须发起真正的工具调用）向用户确认是否把这个工作"
            "流程保存为新技能，clarify 的 choices 必须是 [\"是，保存为技能\", \"不用了\"]。"
            "若用户选「是」，**立即调用 skill_manage(action=create)** 创建该技能。"
            + _SCOPE_ASK_SUFFIX +
            "这是后台技能管理流程的强制步骤，请务必通过工具调用完成，不得跳过、"
            "不得仅用文字描述。"
        )
    if state == "inform":
        sk = dedup_skill or "(unknown)"
        why = f"（{dedup_reason}）" if dedup_reason else ""
        return (
            f"[Echo 系统指令——必须执行工具调用，不能只用文字] "
            f"用户希望把这个流程保存为技能，但技能库里已有一个功能类似的技能「{sk}」{why}。"
            f"请先用一句话告知用户已存在「{sk}」，然后**必须立即调用 clarify 工具**询问："
            f"直接复用现有的「{sk}」还是仍要新建一个，choices 必须是 "
            f"[\"复用现有的{sk}\", \"新建一个\"]。若用户选新建，**立即调用 "
            f"skill_manage(action=create)** 创建。" + _SCOPE_ASK_SUFFIX +
            f"务必通过工具调用完成。"
        )
    if state == "create":
        return (
            "[Echo 系统指令——必须执行工具调用，不能只用文字] "
            "用户明确希望把这个流程保存为技能，且技能库里没有同类技能。"
            "请**立即调用 skill_manage(action=create)** 把它创建为一个新技能"
            "（如需技能名可先用一句话与用户确认）。" + _SCOPE_ASK_SUFFIX +
            "务必通过工具调用完成，不得仅用文字描述。"
        )
    return None


# How many turns to keep re-injecting the ask/create directive. A fast model
# can ignore the first nudge; re-emitting for a few turns raises the odds the
# clarify/skill_manage tool call actually happens. Re-injection stops early as
# soon as a skill is created in the session.
MAX_NUDGES = 3


def _skill_created_in_session(conn, session_id: str) -> bool:
    """True once any skill has been created in this conversation (scope_dialog
    writes an echo_skill_scope row on skill_manage create). Used to stop
    re-nudging — the flow succeeded, or the agent already created something."""
    row = conn.execute(
        "SELECT 1 FROM echo_skill_scope WHERE session_id = ? LIMIT 1",
        (session_id,),
    ).fetchone()
    return row is not None


def consume_nudge(session_id: Optional[str]) -> Optional[str]:
    """Return the ask/create directive for this session, re-emitting it for up
    to MAX_NUDGES turns until the agent actually creates a skill.

    A fast model often ignores a single injected nudge, so we keep the
    directive live for a few turns. We stop (state→'done') as soon as a skill
    is created in the session, or the cap is reached.
    """
    if not session_id:
        return None
    try:
        conn = get_echo_conn()
        row = conn.execute(
            "SELECT state, task_text, dedup_skill, dedup_reason, nudge_count "
            "FROM echo_session_nomination WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None or row["state"] not in _NUDGE_STATES:
            return None

        # Success / give-up stop conditions → retire the nudge.
        if _skill_created_in_session(conn, session_id) or \
                int(row["nudge_count"] or 0) >= MAX_NUDGES:
            conn.execute(
                "UPDATE echo_session_nomination "
                "SET state = 'done', nudged_at = ? WHERE session_id = ?",
                (time.time(), session_id),
            )
            conn.commit()
            return None

        text = _build_nudge(
            row["state"], row["task_text"] or "",
            row["dedup_skill"], row["dedup_reason"],
        )
        if not text:
            return None
        conn.execute(
            "UPDATE echo_session_nomination "
            "SET nudged_at = ?, nudge_count = nudge_count + 1 "
            "WHERE session_id = ?",
            (time.time(), session_id),
        )
        conn.commit()
        return text
    except Exception as exc:
        logger.debug("consume_nudge failed: %s", exc, exc_info=True)
        return None
