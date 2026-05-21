"""Monkey-patch Hermes' tools.skill_usage.bump_use to also feed Echo.

Hermes already calls ``bump_use(skill_name)`` whenever a skill is loaded
into an agent turn (see agent/skill_commands.py:457, :504 and
agent/skill_bundles.py:300 — all three sites use late imports, so a
module-attribute replacement here is picked up by every subsequent call).

We wrap ``bump_use`` so that after Hermes' own counter increment, Echo
records an ``echo_skill_invocation`` row attributed to the current
session context (see session_context.py). All Echo work is wrapped in a
broad try/except — a bug in Echo must never break Hermes' core skill
loading.

install_bump_use_hook() is idempotent: re-running it on the same process
is a no-op. uninstall_bump_use_hook() restores the original function
(used by tests).
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from .db import get_echo_conn
from .session_context import (
    get_current_invocation_id,
    get_platform,
    get_session_id,
    set_current_invocation_id,
)

logger = logging.getLogger(__name__)

# Reference to the original Hermes bump_use, kept so we can restore it.
# Stored as a module-level Optional to make idempotency explicit.
_original_bump_use: Optional[Callable[[str], None]] = None


def _record_invocation(skill_name: str) -> None:
    """Insert one echo_skill_invocation row + ensure confidence anchor exists.

    Confidence row uses INSERT OR IGNORE so first-sight of a skill creates
    a default-confidence anchor without clobbering existing state. The
    invocation row always inserts (each call is a distinct event).

    After the INSERT we write the new invocation_id to the contextvar
    used by Layer A signal collectors — last-skill-wins attribution.

    If there is already a current invocation when bump_use fires (i.e.
    the user is switching from one skill to another within the same
    session), we finalize the prior invocation FIRST so its Layer A
    metrics are computed against the right time window and any drift
    events for it land before the new contextvar overwrite.
    """
    if not skill_name:
        return  # Defensive — Hermes shouldn't call bump_use(""), but if it does, skip.

    # Skill switch: finalize the prior invocation, if any.
    prior_invocation_id = get_current_invocation_id()
    if prior_invocation_id is not None:
        try:
            from .baseline import finalize_invocation
            finalize_invocation(prior_invocation_id)
        except Exception as exc:
            logger.debug(
                "finalize_invocation(%s) failed on skill switch: %s",
                prior_invocation_id, exc, exc_info=True,
            )

    conn = get_echo_conn()
    now = time.time()
    session_id = get_session_id()
    platform = get_platform()

    # Idempotent confidence anchor. Done first so the FK on invocation succeeds.
    conn.execute(
        "INSERT OR IGNORE INTO echo_skill_confidence "
        "(skill_id, created_at, updated_at) VALUES (?, ?, ?)",
        (skill_name, now, now),
    )
    conn.execute(
        "UPDATE echo_skill_confidence "
        "SET n_invocations = n_invocations + 1, updated_at = ? "
        "WHERE skill_id = ?",
        (now, skill_name),
    )
    cursor = conn.execute(
        "INSERT INTO echo_skill_invocation "
        "(skill_id, session_id, platform, started_at) "
        "VALUES (?, ?, ?, ?)",
        (skill_name, session_id, platform, now),
    )
    conn.commit()

    # lastrowid is the AUTOINCREMENT-assigned invocation_id. Cache it for
    # downstream signal collectors. Last-skill-wins by construction:
    # later bump_use calls overwrite this contextvar.
    if cursor.lastrowid is not None:
        set_current_invocation_id(cursor.lastrowid)


def _wrapped_bump_use(skill_name: str) -> None:
    """Replacement for tools.skill_usage.bump_use.

    Always calls the original first so Hermes' own counters update even
    if Echo blows up. Echo errors are logged at DEBUG and swallowed — a
    broken sidecar database never breaks a tool call.
    """
    assert _original_bump_use is not None  # install_bump_use_hook ran first
    _original_bump_use(skill_name)
    try:
        _record_invocation(skill_name)
    except Exception as exc:
        logger.debug("Echo _record_invocation(%s) failed: %s", skill_name, exc, exc_info=True)


def install_bump_use_hook() -> None:
    """Install Echo's wrapper over tools.skill_usage.bump_use.

    Idempotent: if Echo's wrapper is already installed, this is a no-op.
    The three callers in Hermes all do ``from tools.skill_usage import
    bump_use`` *inside* their function bodies, so replacing the module
    attribute is enough — no need to rebind module references.
    """
    global _original_bump_use
    import tools.skill_usage as _mod

    # Detect prior install by attribute identity rather than name —
    # function names match after replacement.
    if getattr(_mod.bump_use, "_echo_wrapped", False):
        return

    _original_bump_use = _mod.bump_use
    _wrapped_bump_use._echo_wrapped = True  # type: ignore[attr-defined]
    _mod.bump_use = _wrapped_bump_use


def uninstall_bump_use_hook() -> None:
    """Restore the original tools.skill_usage.bump_use. Idempotent.

    Used by tests to leave global state clean. In production this should
    never be called — the plugin is meant to be a permanent fixture once
    loaded.
    """
    global _original_bump_use
    if _original_bump_use is None:
        return
    import tools.skill_usage as _mod

    _mod.bump_use = _original_bump_use
    _original_bump_use = None
