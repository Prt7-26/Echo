"""Echo — user-signal-driven skill lifecycle management.

This is the Hermes plugin entry point. See DevPlan/proposal.tex for the
full system design (modules M1-M5).

Step 3 wires four hooks total:
  * on_session_start        -> set session_context (Step 2)
  * on_session_end          -> record session_ended signal THEN clear context
  * pre_llm_call            -> record user_turn signal (Step 3)
  * post_tool_call          -> record tool_call signal (Step 3)

Plus a monkey-patch of tools.skill_usage.bump_use so every skill load
creates an echo_skill_invocation row and writes its id into the
"current invocation" contextvar (Step 2).

Lazy schema initialization — db.get_echo_conn() runs the migration on
the first call, which is reached only when the first hook fires.
register() itself does not touch SQLite.
"""

from __future__ import annotations

import logging

from .schema import ECHO_SCHEMA_VERSION, ECHO_TABLES, ensure_echo_schema
from .session_context import (
    clear_session_context,
    set_session_context,
)
from .signals import (
    on_post_tool_call,
    on_pre_llm_call,
    on_session_end_signal,
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
    """Record the active session so bump_use's wrapper can attribute correctly."""
    set_session_context(session_id, platform)


def _on_session_end(**kwargs):
    """Order matters: record the signal THEN clear context.

    on_session_end_signal reads get_current_invocation_id() to attribute
    the session_ended event, so clear_session_context() must run after
    it -- otherwise the signal lands without a target.
    """
    on_session_end_signal(**kwargs)
    clear_session_context()


def register(ctx) -> None:
    """Hermes plugin entry point.

    Called once per process by hermes_cli.plugins.discover_plugins().
    Side effects:
      1. Replace tools.skill_usage.bump_use with Echo's wrapping version.
      2. Register session-lifecycle hooks (set/clear context + session_ended signal).
      3. Register Layer A signal-collection hooks (user_turn, tool_call).
    """
    install_bump_use_hook()
    ctx.register_hook("on_session_start", _on_session_start)
    ctx.register_hook("on_session_end", _on_session_end)
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    ctx.register_hook("post_tool_call", on_post_tool_call)
    logger.info("Echo signals plugin registered (schema v%d)", ECHO_SCHEMA_VERSION)
