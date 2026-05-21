"""Unit tests for M1 adaptive trigger.

Three categories:
  * detect_save_intent: regex matrix across English/Chinese phrases.
  * record_save_intent_signal: writes the Layer B row, bumps n_signals.
  * list_candidates: scoring across save_intent + tool_count + modif_rounds,
    threshold + ordering + finalization gating.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins.echo_signals import db as echo_db
from plugins.echo_signals import m1_trigger as m1
from plugins.echo_signals.dashboard.plugin_api import router


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    fake_db = tmp_path / "state.db"
    import hermes_state

    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", fake_db)
    echo_db.reset_for_tests()
    yield fake_db
    echo_db.reset_for_tests()


@pytest.fixture
def client(isolated_db):
    app = FastAPI()
    app.include_router(router, prefix="/api/plugins/echo_signals")
    return TestClient(app)


def _seed_skill(skill_id: str):
    conn = echo_db.get_echo_conn()
    now = time.time()
    conn.execute(
        "INSERT INTO echo_skill_confidence "
        "(skill_id, created_at, updated_at) VALUES (?, ?, ?)",
        (skill_id, now, now),
    )
    conn.commit()


def _seed_invocation(skill_id: str, finalized: bool = True,
                     started_at: float | None = None) -> int:
    conn = echo_db.get_echo_conn()
    if started_at is None:
        started_at = time.time()
    finished_at = started_at + 1 if finalized else None
    cur = conn.execute(
        "INSERT INTO echo_skill_invocation "
        "(skill_id, session_id, platform, started_at, finished_at) "
        "VALUES (?, ?, 'cli', ?, ?)",
        (skill_id, f"s-{started_at}", started_at, finished_at),
    )
    conn.commit()
    return cur.lastrowid


def _seed_signal(invocation_id: int, skill_id: str, signal_type: str,
                 layer: str = "A", count: int = 1):
    conn = echo_db.get_echo_conn()
    now = time.time()
    for _ in range(count):
        conn.execute(
            "INSERT INTO echo_signal_event "
            "(invocation_id, skill_id, layer, signal_type, ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (invocation_id, skill_id, layer, signal_type, now),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# detect_save_intent
# ---------------------------------------------------------------------------


class TestDetectSaveIntent:
    def test_empty_returns_false(self):
        assert m1.detect_save_intent("") is False
        assert m1.detect_save_intent(None) is False  # type: ignore[arg-type]
        assert m1.detect_save_intent("   ") is False

    @pytest.mark.parametrize("text", [
        "Please save this as a skill",
        "Save that workflow to a SKILL for later use",
        "remember this for next time",
        "Remember how you did the formatting just now",
        "turn this into a skill",
        "make that a skill so I don't have to ask again",
        "store this for future use",
        "do this every time when I ask for a report",
        "use that from now on",
    ])
    def test_english_positives(self, text):
        assert m1.detect_save_intent(text), f"Expected match: {text!r}"

    @pytest.mark.parametrize("text", [
        "把这个写法存为技能",
        "把这个流程记住",
        "把这种方法保留下来",
        "下次就这样做",
        "以后都这么写",
        "记住这种格式",
        "保存为 skill",
    ])
    def test_chinese_positives(self, text):
        assert m1.detect_save_intent(text), f"Expected match: {text!r}"

    @pytest.mark.parametrize("text", [
        "save the file at /tmp/foo.txt",     # save+filename → not skill intent
        "what is this about?",
        "could you summarize this paper",
        "tell me more about this approach",
        "I need to remember to send the email",  # different remember sense
        "use this library instead",          # different "use this" sense
        "我要保存这个文件",                  # save a file, not a skill
    ])
    def test_negatives_no_false_positive(self, text):
        assert not m1.detect_save_intent(text), f"Expected no match: {text!r}"

    def test_case_insensitive_english(self):
        assert m1.detect_save_intent("SAVE THIS AS A SKILL")
        assert m1.detect_save_intent("Save This For Future Use")


# ---------------------------------------------------------------------------
# record_save_intent_signal
# ---------------------------------------------------------------------------


class TestRecordSaveIntentSignal:
    def test_appends_event_row(self, isolated_db):
        _seed_skill("alpha")
        inv = _seed_invocation("alpha")
        m1.record_save_intent_signal(inv, "alpha")

        conn = echo_db.get_echo_conn()
        row = conn.execute(
            "SELECT layer, signal_type, value_int FROM echo_signal_event "
            "WHERE skill_id = 'alpha'"
        ).fetchone()
        assert row is not None
        assert row["layer"] == "B"
        assert row["signal_type"] == "m1_save_intent"
        assert row["value_int"] == 1

    def test_bumps_n_signals(self, isolated_db):
        _seed_skill("alpha")
        inv = _seed_invocation("alpha")
        before = echo_db.get_echo_conn().execute(
            "SELECT n_signals FROM echo_skill_confidence WHERE skill_id='alpha'"
        ).fetchone()["n_signals"]

        m1.record_save_intent_signal(inv, "alpha")
        m1.record_save_intent_signal(inv, "alpha")

        after = echo_db.get_echo_conn().execute(
            "SELECT n_signals FROM echo_skill_confidence WHERE skill_id='alpha'"
        ).fetchone()["n_signals"]
        assert after - before == 2

    def test_empty_skill_id_no_op(self, isolated_db):
        echo_db.get_echo_conn()
        m1.record_save_intent_signal(1, "")
        conn = echo_db.get_echo_conn()
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM echo_signal_event"
        ).fetchone()["n"]
        assert n == 0


# ---------------------------------------------------------------------------
# list_candidates
# ---------------------------------------------------------------------------


class TestListCandidates:
    def test_empty_db(self, isolated_db):
        echo_db.get_echo_conn()
        assert m1.list_candidates() == []

    def test_save_intent_alone_qualifies(self, isolated_db):
        _seed_skill("alpha")
        inv = _seed_invocation("alpha")
        _seed_signal(inv, "alpha", "m1_save_intent", layer="B")
        out = m1.list_candidates()
        assert len(out) == 1
        c = out[0]
        assert c.invocation_id == inv
        assert c.has_save_intent
        assert c.score == m1.WEIGHT_SAVE_INTENT  # 100
        assert "save intent" in c.reasons[0].lower()

    def test_tool_count_threshold_alone_qualifies(self, isolated_db):
        _seed_skill("alpha")
        inv = _seed_invocation("alpha")
        # Need ≥ 5 tool_call events.
        for _ in range(5):
            _seed_signal(inv, "alpha", "tool_call")
        out = m1.list_candidates()
        assert len(out) == 1
        c = out[0]
        assert c.tool_calls == 5
        assert c.score == m1.WEIGHT_TOOL_COUNT

    def test_modif_rounds_threshold_alone_qualifies(self, isolated_db):
        _seed_skill("alpha")
        inv = _seed_invocation("alpha")
        for _ in range(3):
            _seed_signal(inv, "alpha", "user_turn")
        out = m1.list_candidates()
        assert len(out) == 1
        assert out[0].user_turns == 3
        assert out[0].score == m1.WEIGHT_MODIF_ROUNDS

    def test_below_threshold_excluded(self, isolated_db):
        _seed_skill("alpha")
        inv = _seed_invocation("alpha")
        # Only 4 tool calls — below threshold of 5.
        for _ in range(4):
            _seed_signal(inv, "alpha", "tool_call")
        # And 2 user turns — below threshold of 3.
        for _ in range(2):
            _seed_signal(inv, "alpha", "user_turn")
        assert m1.list_candidates() == []

    def test_multiple_conditions_sum_score(self, isolated_db):
        _seed_skill("alpha")
        inv = _seed_invocation("alpha")
        _seed_signal(inv, "alpha", "m1_save_intent", layer="B")
        for _ in range(5):
            _seed_signal(inv, "alpha", "tool_call")
        for _ in range(3):
            _seed_signal(inv, "alpha", "user_turn")
        out = m1.list_candidates()
        assert len(out) == 1
        # 100 + 30 + 30 = 160
        assert out[0].score == (
            m1.WEIGHT_SAVE_INTENT
            + m1.WEIGHT_TOOL_COUNT
            + m1.WEIGHT_MODIF_ROUNDS
        )
        assert len(out[0].reasons) == 3

    def test_finalized_filter(self, isolated_db):
        _seed_skill("alpha")
        # Open invocation (no finished_at) with save intent.
        inv = _seed_invocation("alpha", finalized=False)
        _seed_signal(inv, "alpha", "m1_save_intent", layer="B")
        # only_finalized=True excludes it.
        assert m1.list_candidates(only_finalized=True) == []
        # only_finalized=False includes it.
        out = m1.list_candidates(only_finalized=False)
        assert len(out) == 1

    def test_ordering_most_recent_first(self, isolated_db):
        _seed_skill("alpha")
        old = _seed_invocation("alpha", started_at=100.0)
        new = _seed_invocation("alpha", started_at=200.0)
        _seed_signal(old, "alpha", "m1_save_intent", layer="B")
        _seed_signal(new, "alpha", "m1_save_intent", layer="B")
        out = m1.list_candidates()
        ids = [c.invocation_id for c in out]
        assert ids == [new, old]

    def test_limit_applied(self, isolated_db):
        _seed_skill("alpha")
        for i in range(5):
            inv = _seed_invocation("alpha", started_at=100.0 + i)
            _seed_signal(inv, "alpha", "m1_save_intent", layer="B")
        out = m1.list_candidates(limit=2)
        assert len(out) == 2

    def test_min_score_filter(self, isolated_db):
        _seed_skill("alpha")
        inv = _seed_invocation("alpha")
        # Just modif_rounds → score = 30
        for _ in range(3):
            _seed_signal(inv, "alpha", "user_turn")
        # min_score=50 should exclude this 30-score candidate.
        out = m1.list_candidates(min_score=50)
        assert out == []
        # min_score=10 includes it.
        out = m1.list_candidates(min_score=10)
        assert len(out) == 1


# ---------------------------------------------------------------------------
# Dashboard /candidates
# ---------------------------------------------------------------------------


class TestCandidatesAPI:
    def test_empty(self, client):
        r = client.get("/api/plugins/echo_signals/candidates")
        assert r.status_code == 200
        assert r.json() == {"candidates": []}

    def test_full_payload(self, client):
        _seed_skill("alpha")
        inv = _seed_invocation("alpha")
        _seed_signal(inv, "alpha", "m1_save_intent", layer="B")
        for _ in range(5):
            _seed_signal(inv, "alpha", "tool_call")

        r = client.get("/api/plugins/echo_signals/candidates")
        data = r.json()["candidates"]
        assert len(data) == 1
        c = data[0]
        assert c["invocation_id"] == inv
        assert c["skill_id"] == "alpha"
        assert c["score"] == 130
        assert c["has_save_intent"] is True
        assert c["tool_calls"] == 5
        assert "save intent" in " ".join(c["reasons"]).lower()

    def test_query_params(self, client):
        _seed_skill("alpha")
        inv1 = _seed_invocation("alpha", started_at=100.0)
        inv2 = _seed_invocation("alpha", started_at=200.0)
        for inv in (inv1, inv2):
            for _ in range(3):
                _seed_signal(inv, "alpha", "user_turn")
        # Both qualify at min_score=30. Limit=1 should return just one.
        r = client.get(
            "/api/plugins/echo_signals/candidates?limit=1&min_score=30"
        )
        data = r.json()["candidates"]
        assert len(data) == 1
        assert data[0]["invocation_id"] == inv2  # most recent


# ---------------------------------------------------------------------------
# End-to-end through signals.on_pre_llm_call
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# M1 condition 4 — semantic recurrence
# ---------------------------------------------------------------------------


def _seed_request_log(user_message: str, *, ts: float | None = None,
                      invocation_id: int | None = None,
                      skill_id: str | None = None,
                      session_id: str | None = None):
    """Insert a row into echo_user_request_log mirroring log_user_request."""
    from plugins.echo_signals.preference_rag import encode, vec_to_blob

    if ts is None:
        ts = time.time()
    vec = encode(user_message)
    blob = vec_to_blob(vec)
    conn = echo_db.get_echo_conn()
    conn.execute(
        "INSERT INTO echo_user_request_log "
        "(invocation_id, skill_id, session_id, user_message, embedding, ts) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (invocation_id, skill_id, session_id, user_message, blob, ts),
    )
    conn.commit()


class TestLogUserRequest:
    def test_writes_row(self, isolated_db):
        m1.log_user_request(
            invocation_id=1, skill_id="alpha", session_id="s1",
            user_message="write me a marketing email",
        )
        conn = echo_db.get_echo_conn()
        row = conn.execute(
            "SELECT invocation_id, skill_id, user_message, embedding "
            "FROM echo_user_request_log"
        ).fetchone()
        assert row["invocation_id"] == 1
        assert row["skill_id"] == "alpha"
        assert row["user_message"] == "write me a marketing email"
        # 256-dim float32 = 1024-byte BLOB
        assert len(row["embedding"]) == 1024

    def test_empty_message_skipped(self, isolated_db):
        echo_db.get_echo_conn()
        m1.log_user_request(
            invocation_id=1, skill_id="alpha", session_id="s1",
            user_message="",
        )
        n = echo_db.get_echo_conn().execute(
            "SELECT COUNT(*) AS n FROM echo_user_request_log"
        ).fetchone()["n"]
        assert n == 0


class TestDetectSemanticRecurrence:
    def test_empty_db_returns_no_hit(self, isolated_db):
        echo_db.get_echo_conn()
        hit, sim = m1.detect_semantic_recurrence("write a marketing email")
        assert hit is False
        assert sim == 0.0

    def test_lexical_match_above_threshold(self, isolated_db):
        # Seed an older log row (well outside the 60s self-window).
        old_ts = time.time() - 7200  # 2 hours ago
        _seed_request_log(
            "write a marketing email for our product launch",
            ts=old_ts,
        )
        hit, sim = m1.detect_semantic_recurrence(
            "write me a marketing email for the launch",
        )
        assert hit is True
        assert sim >= m1.RECURRENCE_THRESHOLD

    def test_unrelated_message_no_hit(self, isolated_db):
        _seed_request_log(
            "write a marketing email",
            ts=time.time() - 7200,
        )
        hit, _sim = m1.detect_semantic_recurrence(
            "debug this python stacktrace",
        )
        assert hit is False

    def test_self_window_excludes_recent_matches(self, isolated_db):
        # Seed within the self-window — should NOT count.
        _seed_request_log(
            "write a marketing email",
            ts=time.time() - 10,  # 10s ago, inside the 60s window
        )
        hit, _sim = m1.detect_semantic_recurrence(
            "write me a marketing email",
        )
        assert hit is False

    def test_lookback_excludes_old_matches(self, isolated_db):
        # Seed 60 days ago (outside default lookback of 30).
        _seed_request_log(
            "write a marketing email",
            ts=time.time() - 60 * 86400,
        )
        hit, _sim = m1.detect_semantic_recurrence(
            "write me a marketing email",
        )
        assert hit is False

    def test_current_invocation_excluded(self, isolated_db):
        # Seed within window but with invocation_id=42; if we query with
        # current_invocation_id=42 it should be excluded.
        _seed_request_log(
            "write a marketing email",
            ts=time.time() - 7200,
            invocation_id=42,
        )
        hit, _sim = m1.detect_semantic_recurrence(
            "write me a marketing email",
            current_invocation_id=42,
        )
        assert hit is False

    def test_empty_query_no_hit(self, isolated_db):
        _seed_request_log("anything", ts=time.time() - 7200)
        assert m1.detect_semantic_recurrence("") == (False, 0.0)
        assert m1.detect_semantic_recurrence("   ") == (False, 0.0)


class TestRecordSemanticRecurrenceSignal:
    def test_appends_event_row(self, isolated_db):
        _seed_skill("alpha")
        inv = _seed_invocation("alpha")
        m1.record_semantic_recurrence_signal(inv, "alpha", similarity=0.82)

        conn = echo_db.get_echo_conn()
        row = conn.execute(
            "SELECT layer, signal_type, value_real "
            "FROM echo_signal_event WHERE skill_id='alpha'"
        ).fetchone()
        assert row["layer"] == "B"
        assert row["signal_type"] == "m1_semantic_recurrence"
        assert row["value_real"] == pytest.approx(0.82)


class TestGCOldRequests:
    def test_deletes_only_old_rows(self, isolated_db):
        echo_db.get_echo_conn()
        _seed_request_log("recent", ts=time.time() - 86400)              # 1 day
        _seed_request_log("old", ts=time.time() - 90 * 86400)            # 90 days
        _seed_request_log("very_old", ts=time.time() - 365 * 86400)      # 1 year
        # Default retention is 60 days (lookback_days × 2 = 30 × 2 = 60)
        deleted = m1.gc_old_requests()
        assert deleted == 2
        remaining = echo_db.get_echo_conn().execute(
            "SELECT user_message FROM echo_user_request_log"
        ).fetchall()
        assert [r["user_message"] for r in remaining] == ["recent"]


class TestRecurrenceCandidate:
    def test_recurrence_alone_qualifies(self, isolated_db):
        _seed_skill("alpha")
        inv = _seed_invocation("alpha")
        _seed_signal(inv, "alpha", "m1_semantic_recurrence", layer="B")
        out = m1.list_candidates()
        assert len(out) == 1
        c = out[0]
        assert c.has_recurrence is True
        assert c.score == m1.WEIGHT_RECURRENCE  # 50

    def test_recurrence_sums_with_other_conditions(self, isolated_db):
        _seed_skill("alpha")
        inv = _seed_invocation("alpha")
        _seed_signal(inv, "alpha", "m1_semantic_recurrence", layer="B")
        _seed_signal(inv, "alpha", "m1_save_intent", layer="B")
        out = m1.list_candidates()
        # 100 + 50 = 150
        assert out[0].score == m1.WEIGHT_SAVE_INTENT + m1.WEIGHT_RECURRENCE


class TestSignalIntegration:
    def test_save_intent_phrase_routes_through_pre_llm_call(self, isolated_db):
        """sig.on_pre_llm_call should detect the phrase, write the row,
        and the candidate appears in /candidates afterward."""
        from plugins.echo_signals import session_context as sc
        from plugins.echo_signals import signals as sig
        from plugins.echo_signals import usage_hook as uh

        uh.install_bump_use_hook()
        try:
            sc.set_session_context("s1", "cli")
            import tools.skill_usage as _su
            _su.bump_use("alpha")
            inv = sc.get_current_invocation_id()

            sig.on_pre_llm_call(
                turn_type="user",
                user_message="Please save this as a skill",
            )

            # Finalize so list_candidates sees it.
            conn = echo_db.get_echo_conn()
            conn.execute(
                "UPDATE echo_skill_invocation SET finished_at = ? WHERE invocation_id = ?",
                (time.time(), inv),
            )
            conn.commit()

            out = m1.list_candidates()
            assert len(out) == 1
            assert out[0].has_save_intent
        finally:
            uh.uninstall_bump_use_hook()
            sc.clear_session_context()

    def test_recurrence_routes_through_pre_llm_call(self, isolated_db):
        """The full flow: an old log row exists, a similar message arrives
        via on_pre_llm_call, recurrence signal lands on the invocation."""
        from plugins.echo_signals import session_context as sc
        from plugins.echo_signals import signals as sig
        from plugins.echo_signals import usage_hook as uh

        # Seed a log entry from "the past" (outside the 60s self-window).
        _seed_request_log(
            "write me a marketing email for our launch",
            ts=time.time() - 7200,
        )

        uh.install_bump_use_hook()
        try:
            sc.set_session_context("s2", "cli")
            import tools.skill_usage as _su
            _su.bump_use("alpha")
            inv = sc.get_current_invocation_id()

            sig.on_pre_llm_call(
                turn_type="user",
                user_message="write a marketing email for our launch",
            )

            conn = echo_db.get_echo_conn()
            row = conn.execute(
                "SELECT signal_type FROM echo_signal_event "
                "WHERE skill_id='alpha' AND signal_type='m1_semantic_recurrence'"
            ).fetchone()
            assert row is not None
            # And the current turn was also logged for future comparisons.
            n_logged = conn.execute(
                "SELECT COUNT(*) AS n FROM echo_user_request_log"
            ).fetchone()["n"]
            assert n_logged == 2  # seeded + current
        finally:
            uh.uninstall_bump_use_hook()
            sc.clear_session_context()
