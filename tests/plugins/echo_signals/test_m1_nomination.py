"""Unit tests for active M1 nomination (dedup → ask/inform/create/skip)."""

from __future__ import annotations

import time

import pytest

from plugins.echo_signals import db as echo_db
from plugins.echo_signals import m1_nomination as nom
from plugins.echo_signals import m1_trigger as m1
from plugins.echo_signals import skill_dedup as sd


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    fake_db = tmp_path / "state.db"
    import hermes_state

    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", fake_db)
    echo_db.reset_for_tests()
    yield fake_db
    echo_db.reset_for_tests()


def _log(session_id, msg, *, save_intent=False, recurrence_sim=None):
    m1.log_user_request(
        invocation_id=None, skill_id=None, session_id=session_id,
        user_message=msg, save_intent=save_intent, recurrence_sim=recurrence_sim,
    )


def _row(session_id):
    return echo_db.get_echo_conn().execute(
        "SELECT * FROM echo_session_nomination WHERE session_id = ?",
        (session_id,),
    ).fetchone()


# ---------------------------------------------------------------------------
# maybe_start_nomination
# ---------------------------------------------------------------------------


class TestMaybeStart:
    def test_non_qualifying_no_row(self, isolated_db):
        echo_db.get_echo_conn()
        _log("s1", "hi")  # 1 turn, nothing → below threshold
        nom.maybe_start_nomination("s1")
        assert _row("s1") is None

    def test_qualifying_inserts_pending(self, isolated_db, monkeypatch):
        echo_db.get_echo_conn()
        called = {}
        monkeypatch.setattr(nom, "_start_dedup_async",
                            lambda *a: called.setdefault("args", a))
        _log("s2", "draft a launch email", save_intent=True)
        nom.maybe_start_nomination("s2")
        r = _row("s2")
        assert r is not None
        assert r["state"] == "pending"
        assert r["trigger_kind"] == "save_intent"
        assert called["args"][0] == "s2"

    def test_implicit_trigger_kind(self, isolated_db, monkeypatch):
        echo_db.get_echo_conn()
        monkeypatch.setattr(nom, "_start_dedup_async", lambda *a: None)
        for i in range(m1.THRESHOLD_MODIF_ROUNDS):
            _log("s3", f"iterate {i}")
        nom.maybe_start_nomination("s3")
        assert _row("s3")["trigger_kind"] == "implicit"

    def test_idempotent(self, isolated_db, monkeypatch):
        echo_db.get_echo_conn()
        calls = []
        monkeypatch.setattr(nom, "_start_dedup_async", lambda *a: calls.append(a))
        _log("s4", "save as skill", save_intent=True)
        nom.maybe_start_nomination("s4")
        nom.maybe_start_nomination("s4")  # second time: no-op
        assert len(calls) == 1

    def test_skilled_session_excluded(self, isolated_db, monkeypatch):
        conn = echo_db.get_echo_conn()
        now = time.time()
        conn.execute("INSERT INTO echo_skill_confidence (skill_id, created_at, updated_at) VALUES ('a', ?, ?)", (now, now))
        conn.execute("INSERT INTO echo_skill_invocation (skill_id, session_id, platform, started_at) VALUES ('a','s5','cli',?)", (now,))
        conn.commit()
        monkeypatch.setattr(nom, "_start_dedup_async", lambda *a: None)
        _log("s5", "save as skill", save_intent=True)
        nom.maybe_start_nomination("s5")
        assert _row("s5") is None  # skilled → invocation-scoped path covers it


# ---------------------------------------------------------------------------
# decide_nomination — the matrix
# ---------------------------------------------------------------------------


class TestDecide:
    def _seed_pending(self, session_id, trigger_kind):
        conn = echo_db.get_echo_conn()
        conn.execute(
            "INSERT INTO echo_session_nomination "
            "(session_id, trigger_kind, state, task_text, created_at) "
            "VALUES (?, ?, 'pending', 'do a thing', ?)",
            (session_id, trigger_kind, time.time()),
        )
        conn.commit()

    def test_save_intent_hit_inform(self, isolated_db, monkeypatch):
        echo_db.get_echo_conn()
        self._seed_pending("d1", "save_intent")
        monkeypatch.setattr(sd, "check_duplicate",
                            lambda t: sd.DedupResult(match="email-draft", reason="same"))
        assert nom.decide_nomination("d1", "do a thing", "save_intent") == "inform"
        r = _row("d1")
        assert r["state"] == "inform"
        assert r["dedup_skill"] == "email-draft"

    def test_save_intent_miss_create(self, isolated_db, monkeypatch):
        echo_db.get_echo_conn()
        self._seed_pending("d2", "save_intent")
        monkeypatch.setattr(sd, "check_duplicate", lambda t: sd.DedupResult(match=None))
        assert nom.decide_nomination("d2", "do a thing", "save_intent") == "create"

    def test_implicit_hit_skip(self, isolated_db, monkeypatch):
        echo_db.get_echo_conn()
        self._seed_pending("d3", "implicit")
        monkeypatch.setattr(sd, "check_duplicate",
                            lambda t: sd.DedupResult(match="email-draft"))
        assert nom.decide_nomination("d3", "do a thing", "implicit") == "skip"

    def test_implicit_miss_ask(self, isolated_db, monkeypatch):
        echo_db.get_echo_conn()
        self._seed_pending("d4", "implicit")
        monkeypatch.setattr(sd, "check_duplicate", lambda t: sd.DedupResult(match=None))
        assert nom.decide_nomination("d4", "do a thing", "implicit") == "ask"


# ---------------------------------------------------------------------------
# consume_nudge
# ---------------------------------------------------------------------------


class TestConsumeNudge:
    def _seed(self, session_id, state, **extra):
        conn = echo_db.get_echo_conn()
        conn.execute(
            "INSERT INTO echo_session_nomination "
            "(session_id, trigger_kind, state, task_text, dedup_skill, created_at) "
            "VALUES (?, 'implicit', ?, 'draft email', ?, ?)",
            (session_id, state, extra.get("dedup_skill"), time.time()),
        )
        conn.commit()

    @pytest.mark.parametrize("state", ["ask", "inform", "create"])
    def test_returns_text_and_marks_done(self, isolated_db, state):
        echo_db.get_echo_conn()
        self._seed("n1", state, dedup_skill="email-draft")
        text = nom.consume_nudge("n1")
        assert text and "Echo" in text
        # consumed → second call returns None, row is 'done'
        assert nom.consume_nudge("n1") is None
        assert _row("n1")["state"] == "done"

    def test_skip_state_no_nudge(self, isolated_db):
        echo_db.get_echo_conn()
        self._seed("n2", "skip")
        assert nom.consume_nudge("n2") is None
        assert _row("n2")["state"] == "skip"  # untouched

    def test_pending_state_no_nudge(self, isolated_db):
        echo_db.get_echo_conn()
        self._seed("n3", "pending")
        assert nom.consume_nudge("n3") is None

    def test_inform_text_names_skill(self, isolated_db):
        echo_db.get_echo_conn()
        self._seed("n4", "inform", dedup_skill="email-draft")
        text = nom.consume_nudge("n4")
        assert "email-draft" in text

    def test_unknown_session_none(self, isolated_db):
        echo_db.get_echo_conn()
        assert nom.consume_nudge("nope") is None
        assert nom.consume_nudge(None) is None


# ---------------------------------------------------------------------------
# Integration through signals.on_pre_llm_call
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_skill_less_qualifying_turn_starts_nomination(self, isolated_db, monkeypatch):
        from plugins.echo_signals import signals as sig
        from plugins.echo_signals import session_context as sc

        started = {}
        monkeypatch.setattr(nom, "_start_dedup_async",
                            lambda *a: started.setdefault("args", a))
        try:
            sc.set_session_context("live-nom", "cli")
            sig.on_pre_llm_call(
                user_message="把这个流程保存为技能",
                session_id="live-nom", platform="cli",
            )
            r = _row("live-nom")
            assert r is not None
            assert r["trigger_kind"] == "save_intent"
            assert started["args"][0] == "live-nom"
        finally:
            sc.clear_session_context()

    def test_nudge_reaches_inject_context(self, isolated_db):
        from plugins.echo_signals import preference_rag as prag
        from plugins.echo_signals import session_context as sc

        conn = echo_db.get_echo_conn()
        conn.execute(
            "INSERT INTO echo_session_nomination "
            "(session_id, trigger_kind, state, task_text, created_at) "
            "VALUES ('inj-1', 'implicit', 'ask', 'draft email', ?)",
            (time.time(),),
        )
        conn.commit()
        try:
            sc.set_session_context("inj-1", "cli")
            out = prag.on_pre_llm_call_inject(
                user_message="随便问点什么", session_id="inj-1",
            )
            assert out is not None
            assert "Echo" in out["context"]
            # consumed → row is now 'done', second inject has no nudge
            assert _row("inj-1")["state"] == "done"
        finally:
            sc.clear_session_context()


class TestScopeAskBundled:
    """The create-leading directives must instruct the agent to ask scope
    in-turn (so it's not stranded waiting for a next turn that never comes)."""

    def _seed(self, session_id, state, **extra):
        conn = echo_db.get_echo_conn()
        conn.execute(
            "INSERT INTO echo_session_nomination "
            "(session_id, trigger_kind, state, task_text, dedup_skill, dedup_reason, created_at) "
            "VALUES (?, 'implicit', ?, 'do a thing', ?, ?, ?)",
            (session_id, state, extra.get("dedup_skill"), extra.get("dedup_reason"), time.time()),
        )
        conn.commit()

    @pytest.mark.parametrize("state", ["ask", "create", "inform"])
    def test_directive_includes_scope_ask(self, isolated_db, state):
        self._seed("sc", state, dedup_skill="x", dedup_reason="y")
        text = nom.consume_nudge("sc")
        assert text is not None
        assert "适用范围" in text  # the scope question marker
        assert "clarify" in text
