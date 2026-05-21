"""Echo — user-signal-driven skill lifecycle management.

This is the Hermes plugin entry point. See DevPlan/proposal.tex for the
full system design (modules M1-M5).

Step 2 wires up:
  * Monkey-patch of tools.skill_usage.bump_use so every skill load also
    creates an echo_skill_invocation row.
  * on_session_start / on_session_end hooks so session_context is set
    before bump_use fires.
  * Lazy schema initialization — db.get_echo_conn() runs the migration
    on the first call, which is reached only when the first hook fires.
    register() itself does not touch SQLite.
"""

from __future__ import annotations

import logging

from .schema import ECHO_SCHEMA_VERSION, ECHO_TABLES, ensure_echo_schema
from .session_context import (
    clear_session_context,
    set_session_context,
)
from .usage_hook import install_bump_use_hook

__all__ = [
    "ECHO_SCHEMA_VERSION",
    "ECHO_TABLES",
    "ensure_echo_schema",
    "register",
]

logger = logging.getLogger(__name__)


def _on_session_start(session_id=None, platform=None, **_kwargs):
    """Echo's on_session_start hook.

    Records the active session in a contextvar so bump_use's wrapper can
    attribute the resulting invocation row correctly. We accept **kwargs
    defensively — Hermes' hook contracts may evolve and we don't want to
    crash on a new parameter.
    """
    set_session_context(session_id, platform)


def _on_session_end(**_kwargs):
    """Echo's on_session_end hook. Drops session context to defaults."""
    clear_session_context()


def register(ctx) -> None:
    """Hermes plugin entry point.

    Called once per process by hermes_cli.plugins.discover_plugins().
    Side effects:
      1. Replace tools.skill_usage.bump_use with Echo's wrapping version.
      2. Register session-lifecycle hooks so bump_use sees the right context.
    """
    install_bump_use_hook()
    ctx.register_hook("on_session_start", _on_session_start)
    ctx.register_hook("on_session_end", _on_session_end)
    logger.info("Echo signals plugin registered (schema v%d)", ECHO_SCHEMA_VERSION)
