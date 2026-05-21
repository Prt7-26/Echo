"""Echo — user-signal-driven skill lifecycle management.

This is the Hermes plugin entry point. It registers hooks for collecting
Layer A behavioral signals during the agent loop. See DevPlan/proposal.tex
for the full design.

Plugin loading happens via the standard ``register(ctx)`` contract used by
all Hermes plugins (e.g. observability/langfuse). The first thing we do is
ensure Echo's SQLite tables exist — see ``schema.ensure_echo_schema``.
"""

from __future__ import annotations

from .schema import ECHO_SCHEMA_VERSION, ECHO_TABLES, ensure_echo_schema

__all__ = ["ECHO_SCHEMA_VERSION", "ECHO_TABLES", "ensure_echo_schema"]


def register(ctx) -> None:
    """Hermes plugin entry point.

    Called once by hermes_cli.plugins.discover_plugins() at startup.
    Step 1 (this commit): schema only — verifies tables can be created.
    Step 2+ will add hook registration here.
    """
    # Hook / tool / CLI registration goes here in Step 2.
    # For now: no-op. Schema initialization is invoked lazily by the
    # data-access helpers (TBD in Step 2) so this plugin does not need
    # the SessionDB to be alive at register-time.
    pass
