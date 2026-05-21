"""Thread- and async-safe carrier for the current session's identity.

When ``bump_use(skill_name)`` fires deep inside Hermes' skill loader, we
need to know *which session* the skill was loaded into so we can write a
properly-attributed row to ``echo_skill_invocation``. ``bump_use`` takes
only a skill name — no session context — so Echo's hooks set the context
ahead of time and ``usage_hook`` reads it.

contextvars (not threading.local) so the context flows correctly through
asyncio tasks spawned by Hermes' gateway runner.
"""

from __future__ import annotations

import contextvars
import os
from typing import Optional

# Defaults: None for session_id (we may not know it yet), "unknown" for
# platform so the NOT NULL constraint on echo_skill_invocation.platform
# is always satisfied without callers having to remember to set it.
_session_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "echo_session_id", default=None
)
_platform: contextvars.ContextVar[str] = contextvars.ContextVar(
    "echo_platform", default="unknown"
)

# Last-skill-wins invocation tracking. Set by usage_hook after a fresh
# echo_skill_invocation row is INSERT-ed. Read by every Layer A signal
# collector so events are attributed to the active skill. None means
# "no Echo-tracked skill is active for this session" — signal collectors
# treat that as a no-op rather than fabricating an invocation.
_current_invocation_id: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
    "echo_current_invocation_id", default=None
)


def set_session_context(session_id: Optional[str], platform: Optional[str]) -> None:
    """Record the active session for downstream signal-collection hooks.

    Called by ``on_session_start`` and during per-message dispatch. Passing
    ``platform=None`` falls back to ``HERMES_PLATFORM`` env var (which is
    how Hermes' own code surfaces the active platform — see
    agent/skill_commands.py:50), then to ``"unknown"``.
    """
    _session_id.set(session_id)
    resolved_platform = platform or os.getenv("HERMES_PLATFORM") or "unknown"
    _platform.set(resolved_platform)


def clear_session_context() -> None:
    """Reset to defaults at session end. Also clears current invocation."""
    _session_id.set(None)
    _platform.set("unknown")
    _current_invocation_id.set(None)


def get_session_id() -> Optional[str]:
    return _session_id.get()


def get_platform() -> str:
    return _platform.get()


# ---------------------------------------------------------------------------
# Current invocation (last-skill-wins)
# ---------------------------------------------------------------------------


def set_current_invocation_id(invocation_id: int) -> None:
    """Mark a freshly-created echo_skill_invocation row as the active one.

    Called by usage_hook._record_invocation after the INSERT. Subsequent
    Layer A signal collectors will attribute their events to this id
    until either (a) clear_session_context() runs at session end, or
    (b) another skill load overwrites it.
    """
    _current_invocation_id.set(invocation_id)


def get_current_invocation_id() -> Optional[int]:
    return _current_invocation_id.get()
