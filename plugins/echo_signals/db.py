"""Lazy SQLite connection for Echo plugin.

Why a dedicated connection (vs. reusing Hermes' SessionDB._conn): Echo
operates entirely on its own ``echo_*`` tables. Reaching into SessionDB's
private ``_conn`` would couple us to Hermes internals; opening a separate
connection to the same file lets SQLite's WAL mode coordinate concurrent
writers for us, and keeps our codebase decoupled.

The connection is opened lazily — the first call to ``get_echo_conn()``
constructs it, runs ``ensure_echo_schema()`` once, and caches it at module
scope. Subsequent calls are O(1) returns of the cached handle.

Concurrency: a Lock guards the lazy-init step. After that, the underlying
sqlite3.Connection is created with ``check_same_thread=False`` so multiple
threads in the agent process can issue queries against it. WAL mode
serializes writes at the SQLite layer.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from .schema import ensure_echo_schema

logger = logging.getLogger(__name__)

# Module-level state. Guarded by _init_lock for the lazy-init transition.
_init_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None
_schema_initialized = False
# Resolved DB path — captured at first init so tests using
# reset_for_tests() + a different HERMES_HOME can re-resolve.
_resolved_db_path: Optional[Path] = None


def _resolve_db_path() -> Path:
    """Return the path Echo should write to.

    Defers the ``hermes_state`` import to call-time so plugin import does
    not pull in the entire agent state module at Hermes startup. This
    matters because ``register(ctx)`` runs early in the boot path.
    """
    from hermes_state import DEFAULT_DB_PATH

    return DEFAULT_DB_PATH


def get_echo_conn() -> sqlite3.Connection:
    """Return a live sqlite3.Connection to state.db (lazy-initialized).

    Idempotent: first call opens the connection and runs the Echo schema
    migration. Subsequent calls return the cached handle.
    """
    global _conn, _schema_initialized, _resolved_db_path
    with _init_lock:
        if _conn is None:
            _resolved_db_path = _resolve_db_path()
            _resolved_db_path.parent.mkdir(parents=True, exist_ok=True)

            _conn = sqlite3.connect(
                str(_resolved_db_path),
                check_same_thread=False,
                # Short timeout — Hermes' policy is application-level
                # retry with jitter rather than sitting in SQLite's
                # internal busy handler. Echo follows the same pattern
                # (retry implemented at the call sites that need it).
                timeout=1.0,
                # Autocommit-off: we manage transactions explicitly.
                isolation_level=None,
            )
            _conn.row_factory = sqlite3.Row

            # WAL is already on (Hermes sets it). PRAGMA foreign_keys must
            # be set per-connection — it's a connection-level toggle.
            _conn.execute("PRAGMA foreign_keys = ON")

            logger.debug("Echo opened connection to %s", _resolved_db_path)

        if not _schema_initialized:
            ensure_echo_schema(_conn)
            _schema_initialized = True
            logger.debug("Echo schema initialized")

        return _conn


def open_standalone_conn() -> sqlite3.Connection:
    """Open a NEW connection that the CALLER owns and must ``close()``.

    Background daemon threads (periodic GC, the skill-edit lock scan) must
    NOT touch the module-level cached connection: concurrent use of a single
    sqlite3.Connection object from two threads is undefined behaviour, and
    ``reset_for_tests()`` closes the cached handle — which, if such a thread
    is mid-statement on it, corrupts memory and crashes the interpreter
    (SIGSEGV at shutdown). Each background thread takes its own short-lived
    connection instead, with the same pragmas/schema as the shared one.
    """
    path = _resolve_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(path),
        check_same_thread=False,
        timeout=1.0,
        isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    ensure_echo_schema(conn)
    return conn


def reset_for_tests() -> None:
    """Drop the cached connection so the next get_echo_conn() rebuilds.

    Used by test fixtures that need a fresh DB (different HERMES_HOME).
    Closing the connection is best-effort; if it raises, we still clear
    the module-level state so subsequent calls don't hand back a stale
    handle.
    """
    global _conn, _schema_initialized, _resolved_db_path
    with _init_lock:
        if _conn is not None:
            try:
                _conn.close()
            except sqlite3.Error:
                pass
        _conn = None
        _schema_initialized = False
        _resolved_db_path = None
