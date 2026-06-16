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
#
# v1 → v2: added echo_turn_cache for M5 preference RAG. The table is
#          ephemeral per session, only used to pair "what did the user
#          ask" with "what did the agent answer" at thumbs-up time.
# v2 → v3: added echo_user_request_log for M1 semantic recurrence
#          detection. Persists every user turn's text + hashing
#          embedding so M1 can detect "is the user repeating a request
#          they made days ago?" without requiring a neural embedding
#          provider.
# v3 → v4: added echo_skill_content_hash for M4 manual-edit locking.
#          Tracks each tracked skill's SKILL.md content hash + the last
#          time Echo observed an agent-driven skill_manage op, so a
#          content change with NO corresponding skill_manage is attributed
#          to a manual user edit and the skill is auto-locked (proposal §M4
#          "用户手动编辑过 → 锁定").
# v4 → v5: added echo_skill_scope.session_id (M2 scope question binds to
#          the conversation that created the skill).
# v5 → v6: added echo_user_request_log.save_intent + recurrence_sim so M1
#          can nominate brand-new skills from SKILL-LESS conversations.
#          The request log is the only signal store that allows NULL
#          invocation_id/skill_id (echo_signal_event requires both NOT
#          NULL), so it carries the per-turn save-intent flag and top
#          recurrence similarity for sessions that never invoked any
#          existing skill. list_session_candidates() aggregates these.
# v6 → v7: added echo_session_tool_count — a per-conversation tool-call
#          counter for SKILL-LESS sessions. Tool calls during a skilled
#          invocation are already counted per-invocation in
#          echo_signal_event; this table covers the no-active-skill case
#          so M1's tool-complexity condition (≥5 calls) also applies to
#          new-skill nomination of skill-less conversations.
# v7 → v8: added echo_session_nomination — per-conversation state for the
#          ACTIVE M1 nominator. When a skill-less conversation crosses the
#          nomination threshold, Echo runs a skill-library dedup check and
#          records the decision (ask / inform / create / skip) here, so it
#          asks at most once per conversation and the inject channel knows
#          what nudge (if any) to append on the next turn.
# v8 → v9: M2 scope confirmation moved from the dashboard widget to an
#          in-conversation clarify question. echo_skill_scope gains
#          scope_options (JSON of the 2-4 Echo-generated applicability
#          choices), scope_choice (the option the user picked, captured from
#          the clarify result in conversation history), and scope_state
#          ('pending'|'options_ready'|'asked'|'confirmed') to drive the
#          generate→inject→capture flow.
ECHO_SCHEMA_VERSION = 9


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
    -- v5: the conversation that created this skill. The dashboard's
    -- scope-confirmation prompt is bound to this session so a skill created
    -- in conversation A never pops its question in conversation B.
    session_id            TEXT,
    -- v9: in-conversation clarify scope flow. scope_options is a JSON array of
    -- the 2-4 Echo-generated applicability choices; scope_choice is the option
    -- the user picked (captured from the clarify result); scope_state drives
    -- generate→ask→capture (pending/options_ready/asked/confirmed).
    scope_options         TEXT,
    scope_choice          TEXT,
    scope_state           TEXT    NOT NULL DEFAULT 'pending',
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

-- v2: M5 turn cache. One row per session, overwritten on every
-- post_llm_call. Used by the dashboard /feedback endpoint to pair the
-- user's thumbs-up with the actual {user_message, assistant_response}
-- that prompted it, since the API itself only sees skill_id + rating.
CREATE TABLE IF NOT EXISTS echo_turn_cache (
    session_id          TEXT    PRIMARY KEY,
    skill_id            TEXT,
    user_message        TEXT    NOT NULL,
    assistant_response  TEXT    NOT NULL,
    updated_at          REAL    NOT NULL
);

-- v3: M1 user-request log for semantic recurrence detection. Append-
-- only stream of user messages with their hashing embeddings; M1's
-- detect_semantic_recurrence cosine-compares the current message
-- against the lookback window. Older rows are garbage-collected by
-- m1_trigger.gc_old_requests().
CREATE TABLE IF NOT EXISTS echo_user_request_log (
    request_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    invocation_id   INTEGER,
    skill_id        TEXT,
    session_id      TEXT,
    user_message    TEXT    NOT NULL,
    embedding       BLOB    NOT NULL,
    ts              REAL    NOT NULL,
    -- v6: per-turn M1 signals for SKILL-LESS nomination. save_intent is
    -- 1 when this turn matched the save-intent regex; recurrence_sim is
    -- the top cosine similarity this turn had against the prior log (NULL
    -- if not computed). list_session_candidates() aggregates these for
    -- sessions that never invoked an existing skill.
    save_intent     INTEGER NOT NULL DEFAULT 0,
    recurrence_sim  REAL
);

CREATE INDEX IF NOT EXISTS idx_echo_user_request_ts
    ON echo_user_request_log(ts DESC);

-- v4: M4 manual-edit lock. Tracks each skill's SKILL.md content hash and
-- the last time Echo saw an agent-driven skill_manage op for it. A content
-- change with no recent agent op is a manual user edit → auto-lock.
CREATE TABLE IF NOT EXISTS echo_skill_content_hash (
    skill_id          TEXT    PRIMARY KEY,
    content_hash      TEXT,
    hash_updated_at   REAL,
    agent_managed_at  REAL
);

-- v7: per-conversation tool-call counter for SKILL-LESS sessions. Only
-- incremented by on_post_tool_call when there is no active invocation
-- (a skilled invocation's tool calls are already counted per-invocation
-- in echo_signal_event). list_session_candidates() reads this to apply
-- M1's tool-complexity condition to new-skill nomination.
CREATE TABLE IF NOT EXISTS echo_session_tool_count (
    session_id      TEXT    PRIMARY KEY,
    tool_calls      INTEGER NOT NULL DEFAULT 0,
    updated_at      REAL    NOT NULL
);

-- v8: active M1 nomination state, one row per skill-less conversation that
-- crossed the nomination threshold. trigger_kind is 'save_intent' (user said
-- so explicitly) or 'implicit' (recurrence/tool/modif). state is the decision
-- after the dedup check: 'pending' (dedup running), 'ask' (clarify the user),
-- 'inform' (tell the user a similar skill exists), 'create' (nudge creation),
-- 'skip' (similar skill exists for an implicit trigger — stay silent), or
-- 'done' (the nudge was already injected). dedup_skill names the existing
-- skill the dedup check matched, if any.
CREATE TABLE IF NOT EXISTS echo_session_nomination (
    session_id      TEXT    PRIMARY KEY,
    trigger_kind    TEXT    NOT NULL,
    state           TEXT    NOT NULL DEFAULT 'pending',
    dedup_skill     TEXT,
    dedup_reason    TEXT,
    task_text       TEXT,
    created_at      REAL    NOT NULL,
    decided_at      REAL,
    nudged_at       REAL
);
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
    "echo_turn_cache",
    "echo_user_request_log",
    "echo_skill_content_hash",
    "echo_session_tool_count",
    "echo_session_nomination",
)


def _add_column_if_missing(
    cursor: sqlite3.Cursor, table: str, column: str, decl: str
) -> None:
    """ALTER TABLE ... ADD COLUMN, but only when the column isn't there yet.

    Idempotent stand-in for SQLite's missing ``ADD COLUMN IF NOT EXISTS``.
    """
    cols = {row[1] for row in cursor.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


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

    # Additive column migrations for DBs created before the column existed.
    # CREATE TABLE IF NOT EXISTS above is a no-op on an existing table, so a
    # column added in a later schema version must be ALTERed in explicitly.
    # SQLite has no "ADD COLUMN IF NOT EXISTS", so probe table_info first.
    _add_column_if_missing(cursor, "echo_skill_scope", "session_id", "TEXT")
    _add_column_if_missing(
        cursor, "echo_user_request_log", "save_intent", "INTEGER NOT NULL DEFAULT 0"
    )
    _add_column_if_missing(
        cursor, "echo_user_request_log", "recurrence_sim", "REAL"
    )
    # v9: M2 scope confirmation via in-conversation clarify.
    _add_column_if_missing(cursor, "echo_skill_scope", "scope_options", "TEXT")
    _add_column_if_missing(cursor, "echo_skill_scope", "scope_choice", "TEXT")
    _add_column_if_missing(
        cursor, "echo_skill_scope", "scope_state", "TEXT NOT NULL DEFAULT 'pending'"
    )

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
