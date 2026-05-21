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
from .preference_rag import (
    on_post_llm_call_cache,
    on_pre_llm_call_inject,
)
from .scope_dialog import on_post_tool_call as on_post_tool_call_scope
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
    """Record the active session so bump_use's wrapper can attribute correctly.

    Also piggyback Echo's periodic GC check here — at most one daemon
    thread per 24h. The check is microseconds; the actual cleanup runs
    out-of-band so it can't slow session startup.
    """
    set_session_context(session_id, platform)
    try:
        from .maintenance import maybe_run_gc
        maybe_run_gc()
    except Exception as exc:
        logger.debug("Echo maybe_run_gc failed: %s", exc, exc_info=True)


def _on_session_end(**kwargs):
    """Order matters at session end:

      1. Record the session_ended signal (uses current invocation_id).
      2. Finalize the current invocation — computes Layer A metrics,
         updates baselines, may emit drift events into the confidence
         engine. This must happen BEFORE the contextvar is cleared so
         the in-progress invocation is what gets finalized.
      3. Clear the contextvar so the next session starts clean.
    """
    on_session_end_signal(**kwargs)

    from .session_context import get_current_invocation_id
    current = get_current_invocation_id()
    if current is not None:
        try:
            from .baseline import finalize_invocation
            finalize_invocation(current)
        except Exception as exc:
            logger.debug(
                "finalize_invocation(%s) failed on session end: %s",
                current, exc, exc_info=True,
            )

    clear_session_context()


def register(ctx) -> None:
    """Hermes plugin entry point.

    Called once per process by hermes_cli.plugins.discover_plugins().
    Side effects:
      1. Replace tools.skill_usage.bump_use with Echo's wrapping version.
      2. Register session-lifecycle hooks (set/clear context + session_ended signal).
      3. Register Layer A signal-collection hooks (user_turn, tool_call).
      4. Install the configured embedding encoder onto preference_rag.
    """
    install_bump_use_hook()

    # Install the active embedding provider. If ECHO_EMBEDDING_PROVIDER=
    # openai + an API key is present, we route encode() through OpenAI
    # embeddings; otherwise the stdlib hashing default stays.
    try:
        from .embeddings import install_active_encoder

        chosen = install_active_encoder()
        logger.info("Echo embedding encoder: %s", chosen)
    except Exception as exc:
        logger.debug("Echo embedding install failed: %s", exc, exc_info=True)
    ctx.register_hook("on_session_start", _on_session_start)
    ctx.register_hook("on_session_end", _on_session_end)
    # Two pre_llm_call handlers:
    #   signals.on_pre_llm_call:   Layer A user_turn signal + Layer B
    #                              NL classify (sentiment → confidence)
    #   preference_rag.on_pre_llm_call_inject:
    #                              M5 RAG — retrieve top-k preference
    #                              examples and inject as few-shots.
    #                              Returns {"context": ...} which Hermes
    #                              appends to the user message
    #                              (cache-safe — system prompt unchanged).
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    ctx.register_hook("pre_llm_call", on_pre_llm_call_inject)
    # post_llm_call: cache the (user_message, assistant_response) pair so
    # the dashboard /feedback endpoint can pair a thumbs-up with the
    # actual turn that prompted it.
    ctx.register_hook("post_llm_call", on_post_llm_call_cache)
    # Two separate post_tool_call handlers — one for Layer A signal
    # recording (signals.on_post_tool_call) and one for M2 scope-row
    # bookkeeping (scope_dialog.on_post_tool_call_scope). Hermes calls
    # them both per fire.
    ctx.register_hook("post_tool_call", on_post_tool_call)
    ctx.register_hook("post_tool_call", on_post_tool_call_scope)
    logger.info("Echo signals plugin registered (schema v%d)", ECHO_SCHEMA_VERSION)
