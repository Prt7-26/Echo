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
