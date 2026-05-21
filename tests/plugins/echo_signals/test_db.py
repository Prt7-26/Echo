"""Unit tests for plugins.echo_signals.db.

Verifies lazy initialization, idempotency, and that the connection lands
on a fresh, isolated DB path (we must NOT touch the user's real
~/.hermes/state.db during tests).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from plugins.echo_signals import db as echo_db
from plugins.echo_signals.schema import ECHO_TABLES


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Redirect Echo's DB path to a temp file and reset module state."""
    fake_db = tmp_path / "state.db"
    # Patch the source of truth — hermes_state.DEFAULT_DB_PATH — that
    # db._resolve_db_path() imports lazily. Patching it via monkeypatch
    # keeps the override scoped to the test.
    import hermes_state

    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", fake_db)
    echo_db.reset_for_tests()
    yield fake_db
    echo_db.reset_for_tests()


class TestLazyInit:
    def test_first_call_creates_db_and_schema(self, isolated_db: Path):
        assert not isolated_db.exists()
        conn = echo_db.get_echo_conn()
        assert isolated_db.exists()
        names = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name LIKE 'echo_%'"
            )
        }
        assert names == set(ECHO_TABLES)

    def test_second_call_returns_same_connection(self, isolated_db: Path):
        c1 = echo_db.get_echo_conn()
        c2 = echo_db.get_echo_conn()
        assert c1 is c2

    def test_foreign_keys_enabled_on_connection(self, isolated_db: Path):
        conn = echo_db.get_echo_conn()
        row = conn.execute("PRAGMA foreign_keys").fetchone()
        assert row[0] == 1


class TestReset:
    def test_reset_drops_cached_connection(self, isolated_db: Path):
        c1 = echo_db.get_echo_conn()
        echo_db.reset_for_tests()
        c2 = echo_db.get_echo_conn()
        assert c1 is not c2

    def test_reset_survives_already_closed_connection(self, isolated_db: Path):
        conn = echo_db.get_echo_conn()
        conn.close()  # simulate an externally-closed handle
        echo_db.reset_for_tests()  # must not raise
        new_conn = echo_db.get_echo_conn()
        assert new_conn is not conn


class TestNoTouchHermesCoreTables:
    """Echo's lazy init must NOT clobber or create Hermes core tables."""

    def test_state_db_has_only_echo_tables(self, isolated_db: Path):
        echo_db.get_echo_conn()
        conn = sqlite3.connect(str(isolated_db))
        all_tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        conn.close()
        # Only echo_* tables (and SQLite's own bookkeeping tables) —
        # Echo does not create sessions/messages/etc. That's Hermes'
        # job at SessionDB init time. sqlite_sequence is auto-created
        # by SQLite when any table uses INTEGER PRIMARY KEY AUTOINCREMENT.
        non_echo = {
            t
            for t in all_tables
            if not t.startswith("echo_") and not t.startswith("sqlite_")
        }
        assert non_echo == set(), f"Unexpected non-echo tables: {non_echo}"
