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
    """Reset to defaults at session end."""
    _session_id.set(None)
    _platform.set("unknown")


def get_session_id() -> Optional[str]:
    return _session_id.get()


def get_platform() -> str:
    return _platform.get()
