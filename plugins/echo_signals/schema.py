"""SQLite schema for Echo skill-lifecycle management.

Six tables that live alongside Hermes core tables in the same state.db
(``get_hermes_home() / "state.db"``, see hermes_state.DEFAULT_DB_PATH).

Why same DB (not a separate file): we need foreign-key consistency with
``sessions(id)`` and we want WAL/retry behavior identical to Hermes core
writes — both come for free when we share the file. Echo opens its own
connection (see db.py) and operates only on the ``echo_*`` namespace, so
the two systems are physically colocated but logically independent.

Identifier convention: ``skill_id`` columns store the SKILL.md ``name:``
field, which is also what Hermes' tools.skill_usage uses as its key.
Picking the same identifier means Echo and Hermes Curator agree about
what counts as "the same skill" across file renames or moves.

Timestamps are REAL (unix epoch, fractional seconds) to match Hermes
core conventions (e.g. ``sessions.started_at REAL NOT NULL``). JOINs
against core tables are type-clean.

Idempotent: ``ensure_echo_schema()`` is safe to call multiple times.
"""

from __future__ import annotations

import sqlite3

# Schema version for Echo's own tables. Independent of Hermes core
# ``schema_version`` — bumping this does NOT touch Hermes versioning.
# Increment when adding tables or columns. Migrations are declarative
# (CREATE TABLE / ADD COLUMN IF NOT EXISTS) — no version-gated chain.
ECHO_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Table 1 — echo_skill_confidence
# Current confidence state per skill. One row per skill.
# ---------------------------------------------------------------------------
# Table 2 — echo_skill_baseline
# Per-skill, per-metric behavioral baseline. Welford's online update.
# baseline_ready flips to 1 once n >= N_warm (cold-start guard).
# ---------------------------------------------------------------------------
# Table 3 — echo_skill_invocation
# One row per skill invocation. Anchors signal_event records to a
# specific use-of-the-skill, makes per-skill sliding-window queries O(N).
# ---------------------------------------------------------------------------
# Table 4 — echo_signal_event
# Raw signal stream. Three typed value columns (real/int/text) avoid
# JSON-per-row serialization overhead.
# ---------------------------------------------------------------------------
# Table 5 — echo_skill_scope
# Module 2 product: scope_level + exclusion_conditions. Writes to
# exclusion_conditions are deferred to next session by default (prompt
# cache invariant — see AGENTS.md).
# ---------------------------------------------------------------------------
# Table 6 — echo_preference_example
# Module 5 RAG store. Embedding stored as BLOB (float32). MMR retrieval
# is done in Python — capacity capped at a few thousand rows.
# ---------------------------------------------------------------------------

ECHO_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS echo_schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS echo_skill_confidence (
    skill_id        TEXT    PRIMARY KEY,
    confidence      REAL    NOT NULL DEFAULT 0.5,
    locked          INTEGER NOT NULL DEFAULT 0,
    n_invocations   INTEGER NOT NULL DEFAULT 0,
    n_signals       INTEGER NOT NULL DEFAULT 0,
    status          TEXT    NOT NULL DEFAULT 'active',
    retired_at      REAL,
    created_at      REAL    NOT NULL,
    updated_at      REAL    NOT NULL,
    CHECK (confidence >= 0.0 AND confidence <= 1.0),
    CHECK (status IN ('active', 'pending_review', 'retired'))
);

CREATE TABLE IF NOT EXISTS echo_skill_baseline (
    skill_id        TEXT    NOT NULL,
    metric_name     TEXT    NOT NULL,
    mean            REAL    NOT NULL,
    variance        REAL    NOT NULL,
    n               INTEGER NOT NULL,
    baseline_ready  INTEGER NOT NULL DEFAULT 0,
    last_updated    REAL    NOT NULL,
    PRIMARY KEY (skill_id, metric_name),
    FOREIGN KEY (skill_id) REFERENCES echo_skill_confidence(skill_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS echo_skill_invocation (
    invocation_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id        TEXT    NOT NULL,
    session_id      TEXT,
    platform        TEXT    NOT NULL,
    started_at      REAL    NOT NULL,
    finished_at     REAL,
    task_summary    TEXT,
    FOREIGN KEY (skill_id) REFERENCES echo_skill_confidence(skill_id)
);

CREATE TABLE IF NOT EXISTS echo_signal_event (
    event_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    invocation_id   INTEGER NOT NULL,
    skill_id        TEXT    NOT NULL,
    layer           TEXT    NOT NULL,
    signal_type     TEXT    NOT NULL,
    value_real      REAL,
    value_int       INTEGER,
    value_text      TEXT,
    metadata        TEXT,
    ts              REAL    NOT NULL,
    FOREIGN KEY (invocation_id) REFERENCES echo_skill_invocation(invocation_id)
        ON DELETE CASCADE,
    CHECK (layer IN ('A', 'B', 'C'))
);

CREATE TABLE IF NOT EXISTS echo_skill_scope (
    skill_id              TEXT    PRIMARY KEY,
    scope_level           TEXT    NOT NULL DEFAULT 'unknown',
    task_type_tags        TEXT,
    exclusion_conditions  TEXT,
    methodology_layer     TEXT,
    specifics_layer       TEXT,
    user_confirmed_at     REAL,
    created_at            REAL    NOT NULL,
    updated_at            REAL    NOT NULL,
    FOREIGN KEY (skill_id) REFERENCES echo_skill_confidence(skill_id)
        ON DELETE CASCADE,
    CHECK (scope_level IN ('broad', 'narrow', 'unknown'))
);

CREATE TABLE IF NOT EXISTS echo_preference_example (
    example_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    task_request      TEXT    NOT NULL,
    task_embedding    BLOB    NOT NULL,
    agent_output      TEXT    NOT NULL,
    rating            INTEGER NOT NULL,
    skill_id          TEXT,
    task_type_tag     TEXT,
    created_at        REAL    NOT NULL,
    last_used_at      REAL,
    use_count         INTEGER NOT NULL DEFAULT 0,
    composite_score   REAL,
    CHECK (rating BETWEEN 1 AND 5)
);

CREATE INDEX IF NOT EXISTS idx_echo_confidence_status
    ON echo_skill_confidence(status, confidence ASC);

CREATE INDEX IF NOT EXISTS idx_echo_invocation_skill_time
    ON echo_skill_invocation(skill_id, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_echo_invocation_session
    ON echo_skill_invocation(session_id)
    WHERE session_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_echo_signal_skill_layer_time
    ON echo_signal_event(skill_id, layer, ts DESC);

CREATE INDEX IF NOT EXISTS idx_echo_signal_invocation
    ON echo_signal_event(invocation_id);

CREATE INDEX IF NOT EXISTS idx_echo_preference_skill
    ON echo_preference_example(skill_id)
    WHERE skill_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_echo_preference_score
    ON echo_preference_example(composite_score ASC)
    WHERE composite_score IS NOT NULL;
"""


# All Echo table names — used for introspection / cleanup / tests.
ECHO_TABLES = (
    "echo_schema_version",
    "echo_skill_confidence",
    "echo_skill_baseline",
    "echo_skill_invocation",
    "echo_signal_event",
    "echo_skill_scope",
    "echo_preference_example",
)


def ensure_echo_schema(conn: sqlite3.Connection) -> None:
    """Create all Echo tables and indexes if they don't exist.

    Idempotent: safe to call on every plugin load. Does not touch any
    Hermes core tables — only creates rows in ``echo_*`` namespace.

    Args:
        conn: A live SQLite connection to the Hermes sessions.db. Must
            have ``foreign_keys`` PRAGMA enabled by caller if FK
            enforcement is desired (Hermes core enables it).
    """
    cursor = conn.cursor()
    cursor.executescript(ECHO_SCHEMA_SQL)

    # Record / refresh schema version. INSERT on first run, UPDATE later.
    cursor.execute("SELECT version FROM echo_schema_version LIMIT 1")
    row = cursor.fetchone()
    if row is None:
        cursor.execute(
            "INSERT INTO echo_schema_version (version) VALUES (?)",
            (ECHO_SCHEMA_VERSION,),
        )
    elif row[0] < ECHO_SCHEMA_VERSION:
        cursor.execute(
            "UPDATE echo_schema_version SET version = ?",
            (ECHO_SCHEMA_VERSION,),
        )

    conn.commit()
