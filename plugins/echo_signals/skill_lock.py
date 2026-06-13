"""M4 manual-edit lock — auto-lock a skill the user hand-edited.

proposal §M4: "如果用户手动编辑过某个技能的内容，该技能会被自动标记为锁定
状态，后续的自学习循环不允许覆盖它。"

Echo can't watch the filesystem in real time, so it diffs each tracked
skill's SKILL.md content hash at session start. The hard part is
*attribution*: Hermes itself rewrites skills (its self-improvement loop
patches SKILL.md), so locking on ANY change would lock Hermes' own edits
and freeze Echo. We solve that by observing skill_manage tool calls:

  * Whenever a skill_manage op touches a skill, record agent_managed_at.
  * At session start, if a skill's content hash changed AND the change is
    NOT explained by a recent agent_managed_at (i.e. no skill_manage since
    we last hashed it), it was a manual editor edit → set_locked(True).
  * A change that IS explained by an agent op just refreshes the stored
    hash, no lock.

Fail-soft throughout: any error (missing file, unreadable, etc.) is logged
and skipped. A skill we can't locate (folder name != skill name, bundled
skill elsewhere) is simply not tracked — best-effort, never crashes.
"""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from typing import Optional

from .db import get_echo_conn

logger = logging.getLogger(__name__)

# skill_manage actions that legitimately rewrite a skill's content.
AGENT_MANAGE_ACTIONS = frozenset({
    "create", "update", "edit", "patch", "write", "save", "append", "replace",
})


def _skill_md_path(skill_id: str) -> Optional[Path]:
    """Best-effort path to a skill's SKILL.md. The common layout is
    <hermes_home>/skills/<skill_id>/SKILL.md (folder name == skill name)."""
    try:
        from hermes_constants import get_hermes_home

        p = get_hermes_home() / "skills" / skill_id / "SKILL.md"
        return p
    except Exception as exc:
        logger.debug("skill_lock._skill_md_path(%s) failed: %s", skill_id, exc)
        return None


def _hash_file(path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except Exception as exc:
        logger.debug("skill_lock._hash_file(%s) failed: %s", path, exc)
        return None


def record_agent_managed(skill_id: str, action: str = "") -> None:
    """Note that the agent (via skill_manage) touched a skill's content, so
    a later content change isn't mistaken for a manual edit. Idempotent
    upsert on echo_skill_content_hash.agent_managed_at."""
    if not skill_id:
        return
    if action and action.strip().lower() not in AGENT_MANAGE_ACTIONS:
        return
    try:
        conn = get_echo_conn()
        now = time.time()
        row = conn.execute(
            "SELECT skill_id FROM echo_skill_content_hash WHERE skill_id = ?",
            (skill_id,),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO echo_skill_content_hash "
                "(skill_id, content_hash, hash_updated_at, agent_managed_at) "
                "VALUES (?, NULL, NULL, ?)",
                (skill_id, now),
            )
        else:
            conn.execute(
                "UPDATE echo_skill_content_hash SET agent_managed_at = ? "
                "WHERE skill_id = ?",
                (now, skill_id),
            )
        conn.commit()
    except Exception as exc:
        logger.debug("record_agent_managed(%s) failed: %s", skill_id, exc, exc_info=True)


def on_post_tool_call(*, tool_name: str = "", args=None, **_kwargs) -> None:
    """Hook: record agent-managed timestamp on any skill_manage write."""
    if tool_name != "skill_manage":
        return
    try:
        from .scope_dialog import _extract_action_and_name

        action, skill_name = _extract_action_and_name(args)
        if skill_name and action and action.lower() in AGENT_MANAGE_ACTIONS:
            record_agent_managed(skill_name, action)
    except Exception as exc:
        logger.debug("skill_lock.on_post_tool_call failed: %s", exc, exc_info=True)


def check_skill_edits() -> int:
    """Scan tracked skills for manual SKILL.md edits and lock the offenders.

    Returns the number of skills newly locked this pass. Idempotent and
    fail-soft: a skill already locked is skipped, an unlocatable/unreadable
    SKILL.md is skipped.
    """
    locked_count = 0
    try:
        conn = get_echo_conn()
        skills = conn.execute(
            "SELECT skill_id, locked FROM echo_skill_confidence"
        ).fetchall()
    except Exception as exc:
        logger.debug("check_skill_edits: skill list query failed: %s", exc)
        return 0

    for s in skills:
        if int(s["locked"]):
            continue
        skill_id = s["skill_id"]
        path = _skill_md_path(skill_id)
        if path is None or not path.is_file():
            continue
        h = _hash_file(path)
        if h is None:
            continue

        try:
            now = time.time()
            row = conn.execute(
                "SELECT content_hash, hash_updated_at, agent_managed_at "
                "FROM echo_skill_content_hash WHERE skill_id = ?",
                (skill_id,),
            ).fetchone()

            if row is None:
                # First time we see this skill's content — record a baseline,
                # never lock on first sight.
                conn.execute(
                    "INSERT INTO echo_skill_content_hash "
                    "(skill_id, content_hash, hash_updated_at, agent_managed_at) "
                    "VALUES (?, ?, ?, NULL)",
                    (skill_id, h, now),
                )
                conn.commit()
                continue

            stored_hash = row["content_hash"]
            if not stored_hash:
                # We had an agent_managed marker but no hash yet — baseline it.
                conn.execute(
                    "UPDATE echo_skill_content_hash "
                    "SET content_hash = ?, hash_updated_at = ? WHERE skill_id = ?",
                    (h, now, skill_id),
                )
                conn.commit()
                continue

            if h == stored_hash:
                continue  # unchanged

            # Content changed — attribute it.
            agent_managed_at = row["agent_managed_at"]
            hash_updated_at = row["hash_updated_at"] or 0.0
            agent_explained = (
                agent_managed_at is not None and agent_managed_at >= hash_updated_at
            )
            # Refresh the stored hash either way.
            conn.execute(
                "UPDATE echo_skill_content_hash "
                "SET content_hash = ?, hash_updated_at = ? WHERE skill_id = ?",
                (h, now, skill_id),
            )
            conn.commit()

            if not agent_explained:
                from .confidence import set_locked

                if set_locked(skill_id, True):
                    locked_count += 1
                    logger.info(
                        "Echo: locked skill '%s' — detected a manual SKILL.md edit "
                        "(no agent skill_manage explained the change)",
                        skill_id,
                    )
        except Exception as exc:
            logger.debug("check_skill_edits(%s) failed: %s", skill_id, exc, exc_info=True)

    return locked_count
