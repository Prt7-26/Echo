"""Unit tests for plugins.echo_signals.schema.

Verifies the Echo schema creates cleanly, is idempotent, enforces the
constraints documented in DevPlan/schema.md, and coexists peacefully with
Hermes core tables (no name collisions).

All tests run against in-memory SQLite — no filesystem state, no fixture
bleed. Fast (<100ms total).
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from plugins.echo_signals.schema import (
    ECHO_SCHEMA_SQL,
    ECHO_SCHEMA_VERSION,
    ECHO_TABLES,
    ensure_echo_schema,
)


@pytest.fixture
def conn():
    """Fresh in-memory SQLite connection with foreign keys enabled."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    yield c
    c.close()


# ---------------------------------------------------------------------------
# Basic creation
# ---------------------------------------------------------------------------


class TestSchemaCreation:
    def test_creates_all_tables(self, conn):
        ensure_echo_schema(conn)
        rows = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name LIKE 'echo_%'"
        ).fetchall()
        names = {r["name"] for r in rows}
        assert names == set(ECHO_TABLES)

    def test_creates_expected_indexes(self, conn):
        ensure_echo_schema(conn)
        rows = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND name LIKE 'idx_echo_%'"
        ).fetchall()
        names = {r["name"] for r in rows}
        # All eight indexes from schema.py (+1 vs v1: user_request_ts at v3).
        assert names == {
            "idx_echo_confidence_status",
            "idx_echo_invocation_skill_time",
            "idx_echo_invocation_session",
            "idx_echo_signal_skill_layer_time",
            "idx_echo_signal_invocation",
            "idx_echo_preference_skill",
            "idx_echo_preference_score",
            "idx_echo_user_request_ts",
        }

    def test_records_schema_version(self, conn):
        ensure_echo_schema(conn)
        row = conn.execute(
            "SELECT version FROM echo_schema_version"
        ).fetchone()
        assert row["version"] == ECHO_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Idempotency: repeated calls don't fail, don't duplicate rows
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_double_ensure_is_safe(self, conn):
        ensure_echo_schema(conn)
        ensure_echo_schema(conn)  # second call must not raise

        # version row stays unique
        rows = conn.execute(
            "SELECT version FROM echo_schema_version"
        ).fetchall()
        assert len(rows) == 1

    def test_existing_data_survives_re_ensure(self, conn):
        ensure_echo_schema(conn)
        now = time.time()
        conn.execute(
            "INSERT INTO echo_skill_confidence "
            "(skill_id, created_at, updated_at) VALUES (?, ?, ?)",
            ("test_skill", now, now),
        )
        conn.commit()

        ensure_echo_schema(conn)  # re-run

        rows = conn.execute(
            "SELECT skill_id FROM echo_skill_confidence"
        ).fetchall()
        assert [r["skill_id"] for r in rows] == ["test_skill"]


# ---------------------------------------------------------------------------
# Column / type sanity for the high-traffic tables
# ---------------------------------------------------------------------------


class TestTableShape:
    def _columns(self, conn, table):
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {r["name"]: r["type"] for r in rows}

    def test_confidence_columns(self, conn):
        ensure_echo_schema(conn)
        cols = self._columns(conn, "echo_skill_confidence")
        assert cols["skill_id"] == "TEXT"
        assert cols["confidence"] == "REAL"
        assert cols["status"] == "TEXT"
        assert cols["retired_at"] == "REAL"

    def test_signal_event_typed_value_columns(self, conn):
        """Three nullable typed value columns avoid per-row JSON."""
        ensure_echo_schema(conn)
        cols = self._columns(conn, "echo_signal_event")
        assert cols["value_real"] == "REAL"
        assert cols["value_int"] == "INTEGER"
        assert cols["value_text"] == "TEXT"

    def test_preference_embedding_blob(self, conn):
        ensure_echo_schema(conn)
        cols = self._columns(conn, "echo_preference_example")
        # Embedding is float32 bytes — BLOB not TEXT.
        assert cols["task_embedding"] == "BLOB"


# ---------------------------------------------------------------------------
# CHECK constraints — these are load-bearing for data integrity
# ---------------------------------------------------------------------------


class TestCheckConstraints:
    def test_confidence_out_of_range_rejected(self, conn):
        ensure_echo_schema(conn)
        now = time.time()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO echo_skill_confidence "
                "(skill_id, confidence, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                ("bad_skill", 1.5, now, now),  # > 1.0
            )
            conn.commit()

    def test_confidence_status_enum_enforced(self, conn):
        ensure_echo_schema(conn)
        now = time.time()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO echo_skill_confidence "
                "(skill_id, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                ("bad_skill", "exploded", now, now),
            )
            conn.commit()

    def test_signal_layer_enum_enforced(self, conn):
        ensure_echo_schema(conn)
        now = time.time()
        # Need a valid confidence row + invocation first for FK to pass.
        conn.execute(
            "INSERT INTO echo_skill_confidence "
            "(skill_id, created_at, updated_at) VALUES (?, ?, ?)",
            ("s", now, now),
        )
        conn.execute(
            "INSERT INTO echo_skill_invocation "
            "(skill_id, platform, started_at) VALUES (?, ?, ?)",
            ("s", "cli", now),
        )
        inv_id = conn.execute(
            "SELECT invocation_id FROM echo_skill_invocation"
        ).fetchone()["invocation_id"]

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO echo_signal_event "
                "(invocation_id, skill_id, layer, signal_type, ts) "
                "VALUES (?, ?, ?, ?, ?)",
                (inv_id, "s", "Z", "thumbs_up", now),  # layer must be A/B/C
            )
            conn.commit()

    def test_preference_rating_range_enforced(self, conn):
        ensure_echo_schema(conn)
        now = time.time()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO echo_preference_example "
                "(task_request, task_embedding, agent_output, rating, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("q", b"\x00" * 16, "a", 6, now),  # rating > 5
            )
            conn.commit()


# ---------------------------------------------------------------------------
# Foreign keys: when confidence row is deleted, dependent rows go too
# ---------------------------------------------------------------------------


class TestForeignKeys:
    def test_cascade_delete_clears_dependents(self, conn):
        ensure_echo_schema(conn)
        now = time.time()
        conn.execute(
            "INSERT INTO echo_skill_confidence "
            "(skill_id, created_at, updated_at) VALUES (?, ?, ?)",
            ("s", now, now),
        )
        conn.execute(
            "INSERT INTO echo_skill_baseline "
            "(skill_id, metric_name, mean, variance, n, last_updated) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("s", "modification_rounds", 2.0, 1.5, 10, now),
        )
        conn.execute(
            "INSERT INTO echo_skill_scope "
            "(skill_id, created_at, updated_at) VALUES (?, ?, ?)",
            ("s", now, now),
        )
        conn.commit()

        conn.execute("DELETE FROM echo_skill_confidence WHERE skill_id = ?", ("s",))
        conn.commit()

        baseline_count = conn.execute(
            "SELECT COUNT(*) AS n FROM echo_skill_baseline"
        ).fetchone()["n"]
        scope_count = conn.execute(
            "SELECT COUNT(*) AS n FROM echo_skill_scope"
        ).fetchone()["n"]
        assert baseline_count == 0
        assert scope_count == 0


# ---------------------------------------------------------------------------
# Coexistence with Hermes core SCHEMA_SQL (no name collisions)
# ---------------------------------------------------------------------------


class TestCoexistence:
    def test_no_name_collision_with_hermes_core(self, conn):
        """Echo tables must not shadow Hermes core tables."""
        from hermes_state import SCHEMA_SQL as HERMES_SCHEMA_SQL

        # Run Hermes core first, then Echo. If any Echo CREATE clobbered
        # a Hermes table, the IF NOT EXISTS would silently skip but the
        # Hermes row layouts would be wrong. Easier check: name sets
        # don't intersect.
        hermes_names = set()
        for line in HERMES_SCHEMA_SQL.split(";"):
            line = line.strip()
            if line.upper().startswith("CREATE TABLE IF NOT EXISTS"):
                # "CREATE TABLE IF NOT EXISTS <name> (..."
                rest = line[len("CREATE TABLE IF NOT EXISTS"):].strip()
                tbl = rest.split("(", 1)[0].strip()
                hermes_names.add(tbl)

        assert hermes_names, "failed to parse Hermes core schema"
        assert hermes_names.isdisjoint(set(ECHO_TABLES))

    def test_runs_alongside_hermes_core(self, conn):
        """Both schemas in same DB, no errors."""
        from hermes_state import SCHEMA_SQL as HERMES_SCHEMA_SQL

        conn.executescript(HERMES_SCHEMA_SQL)
        ensure_echo_schema(conn)

        # Sanity: both sets of tables now exist.
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = {r["name"] for r in rows}
        assert "sessions" in names  # Hermes
        assert "echo_skill_confidence" in names  # Echo
