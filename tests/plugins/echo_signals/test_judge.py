"""Unit tests for Layer C — judge module and the trigger wrapper.

Three layers of coverage:

  * _parse_verdict: every shape we expect from the LLM (clean JSON,
    JSON wrapped in code fences, prose-padded JSON, invalid garbage).
  * process_verdict: ok / degraded / exclusion all land in the right
    state column.
  * Integration: apply_signal_event fires judge when, and only when,
    confidence transitions active → pending_review.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from plugins.echo_signals import confidence as conf
from plugins.echo_signals import confidence_actions as ca
from plugins.echo_signals import db as echo_db
from plugins.echo_signals import judge as jdg


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


@pytest.fixture(autouse=True)
def _reset_judge():
    jdg.reset_judge_impl()
    yield
    jdg.reset_judge_impl()


def _seed(skill_id: str, confidence: float = 0.5,
          status: str = "active", locked: int = 0):
    conn = echo_db.get_echo_conn()
    now = time.time()
    conn.execute(
        "INSERT INTO echo_skill_confidence "
        "(skill_id, confidence, status, locked, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (skill_id, confidence, status, locked, now, now),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Verdict parsing
# ---------------------------------------------------------------------------


class TestParseVerdict:
    def test_clean_ok(self):
        v = jdg._parse_verdict('{"verdict": "ok"}')
        assert v.verdict == "ok"

    def test_clean_degraded_with_reason(self):
        v = jdg._parse_verdict('{"verdict": "degraded", "reason": "Tool output is wrong"}')
        assert v.verdict == "degraded"
        assert v.reason == "Tool output is wrong"

    def test_clean_exclusion_with_context(self):
        v = jdg._parse_verdict('{"verdict": "exclusion", "context": "Google Ads campaigns"}')
        assert v.verdict == "exclusion"
        assert v.context == "Google Ads campaigns"

    def test_code_fence_wrapped(self):
        text = '```json\n{"verdict": "degraded", "reason": "x"}\n```'
        v = jdg._parse_verdict(text)
        assert v.verdict == "degraded"
        assert v.reason == "x"

    def test_prose_padded(self):
        text = 'Based on the signals, my verdict is: {"verdict": "ok"} Hope this helps!'
        v = jdg._parse_verdict(text)
        assert v.verdict == "ok"

    def test_garbage_falls_back_to_ok(self):
        assert jdg._parse_verdict("I don't know").verdict == "ok"
        assert jdg._parse_verdict("").verdict == "ok"
        assert jdg._parse_verdict("{not json").verdict == "ok"

    def test_unknown_verdict_value_falls_back_to_ok(self):
        v = jdg._parse_verdict('{"verdict": "maybe", "reason": "I am unsure"}')
        assert v.verdict == "ok"

    def test_reason_only_attached_to_degraded(self):
        v = jdg._parse_verdict('{"verdict": "ok", "reason": "ignored"}')
        assert v.reason is None  # reason only meaningful for degraded


# ---------------------------------------------------------------------------
# process_verdict — three paths
# ---------------------------------------------------------------------------


class TestProcessVerdict:
    def test_ok_is_noop(self, isolated_db):
        _seed("alpha", confidence=0.25, status=conf.STATUS_PENDING_REVIEW)
        jdg.process_verdict("alpha", jdg.JudgeVerdict(verdict="ok"))

        conn = echo_db.get_echo_conn()
        row = conn.execute(
            "SELECT confidence, status FROM echo_skill_confidence WHERE skill_id='alpha'"
        ).fetchone()
        assert row["confidence"] == pytest.approx(0.25)
        assert row["status"] == conf.STATUS_PENDING_REVIEW

    def test_degraded_pushes_confidence_further_down(self, isolated_db):
        _seed("alpha", confidence=0.25, status=conf.STATUS_PENDING_REVIEW)
        jdg.process_verdict(
            "alpha",
            jdg.JudgeVerdict(verdict="degraded", reason="really bad"),
        )

        conn = echo_db.get_echo_conn()
        c = conn.execute(
            "SELECT confidence FROM echo_skill_confidence WHERE skill_id='alpha'"
        ).fetchone()["confidence"]
        # drift_detected with severity=2.0: c * (1 - 0.15 * 2.0) = c * 0.7
        assert c == pytest.approx(0.25 * 0.7)

    def test_exclusion_creates_scope_row(self, isolated_db):
        _seed("alpha")
        jdg.process_verdict(
            "alpha",
            jdg.JudgeVerdict(verdict="exclusion", context="Google Ads"),
        )

        conn = echo_db.get_echo_conn()
        row = conn.execute(
            "SELECT scope_level, exclusion_conditions FROM echo_skill_scope "
            "WHERE skill_id='alpha'"
        ).fetchone()
        assert row is not None
        excl = json.loads(row["exclusion_conditions"])
        assert excl == ["Google Ads"]

    def test_exclusion_appends_to_existing(self, isolated_db):
        _seed("alpha")
        conn = echo_db.get_echo_conn()
        now = time.time()
        conn.execute(
            "INSERT INTO echo_skill_scope "
            "(skill_id, scope_level, exclusion_conditions, created_at, updated_at) "
            "VALUES (?, 'narrow', ?, ?, ?)",
            ("alpha", json.dumps(["LinkedIn"]), now, now),
        )
        conn.commit()

        jdg.process_verdict(
            "alpha",
            jdg.JudgeVerdict(verdict="exclusion", context="Google Ads"),
        )

        excl = json.loads(conn.execute(
            "SELECT exclusion_conditions FROM echo_skill_scope WHERE skill_id='alpha'"
        ).fetchone()["exclusion_conditions"])
        assert excl == ["LinkedIn", "Google Ads"]

    def test_exclusion_dedupes(self, isolated_db):
        _seed("alpha")
        for _ in range(3):
            jdg.process_verdict(
                "alpha",
                jdg.JudgeVerdict(verdict="exclusion", context="same ctx"),
            )
        conn = echo_db.get_echo_conn()
        excl = json.loads(conn.execute(
            "SELECT exclusion_conditions FROM echo_skill_scope WHERE skill_id='alpha'"
        ).fetchone()["exclusion_conditions"])
        assert excl == ["same ctx"]

    def test_exclusion_with_empty_context_skipped(self, isolated_db):
        _seed("alpha")
        jdg.process_verdict(
            "alpha",
            jdg.JudgeVerdict(verdict="exclusion", context=""),
        )
        conn = echo_db.get_echo_conn()
        row = conn.execute(
            "SELECT scope_level FROM echo_skill_scope WHERE skill_id='alpha'"
        ).fetchone()
        assert row is None


# ---------------------------------------------------------------------------
# run_judge — synchronous, exception-safe
# ---------------------------------------------------------------------------


class TestRunJudge:
    def test_uses_injected_impl(self, isolated_db):
        _seed("alpha")
        jdg.set_judge_impl(lambda sk, c: jdg.JudgeVerdict(verdict="degraded", reason="x"))
        v = jdg.run_judge("alpha", 0.25)
        assert v.verdict == "degraded"

    def test_impl_exception_returns_ok(self, isolated_db):
        _seed("alpha")

        def _boom(skill_id, confidence):
            raise RuntimeError("simulated")

        jdg.set_judge_impl(_boom)
        v = jdg.run_judge("alpha", 0.25)
        assert v.verdict == "ok"  # SACRED: never raises


# ---------------------------------------------------------------------------
# start_judge_async — fire-and-forget
# ---------------------------------------------------------------------------


class TestStartJudgeAsync:
    def test_runs_and_processes_verdict(self, isolated_db, real_judge):
        _seed("alpha", confidence=0.25, status=conf.STATUS_PENDING_REVIEW)

        jdg.set_judge_impl(
            lambda sk, c: jdg.JudgeVerdict(verdict="exclusion", context="X-platform"),
        )

        done = threading.Event()

        def on_done(v):
            done.set()

        thread = jdg.start_judge_async("alpha", 0.25, on_done)
        done.wait(timeout=2.0)
        thread.join(timeout=2.0)

        conn = echo_db.get_echo_conn()
        excl = json.loads(conn.execute(
            "SELECT exclusion_conditions FROM echo_skill_scope WHERE skill_id='alpha'"
        ).fetchone()["exclusion_conditions"])
        assert excl == ["X-platform"]


# ---------------------------------------------------------------------------
# apply_signal_event — judge trigger gating
# ---------------------------------------------------------------------------


def _wait_judge_done(thread):
    """Helper that polls until the daemon thread finishes."""
    if thread is not None:
        thread.join(timeout=2.0)


class TestApplySignalEventJudgeTrigger:
    """The Layer C trigger contract: judge fires when, and only when,
    confidence transitions from active to pending_review."""

    def test_active_to_active_does_not_fire(self, isolated_db, monkeypatch):
        _seed("alpha", confidence=0.7)
        fired = threading.Event()
        monkeypatch.setattr(
            jdg, "start_judge_async",
            lambda *a, **kw: (fired.set(), threading.Thread(target=lambda: None))[1],
        )

        result = ca.apply_signal_event("alpha", "explicit_positive")
        assert result.applied
        assert result.new_status == conf.STATUS_ACTIVE
        time.sleep(0.05)
        assert not fired.is_set()

    def test_active_to_pending_review_fires_judge(self, isolated_db, monkeypatch):
        _seed("alpha", confidence=0.4)
        fired = threading.Event()
        captured = {}

        def fake_start(skill_id, confidence, on_done=None):
            captured["skill_id"] = skill_id
            captured["confidence"] = confidence
            fired.set()
            return threading.Thread(target=lambda: None)

        monkeypatch.setattr(jdg, "start_judge_async", fake_start)

        # 0.4 * (1 - 0.30) = 0.28 → below c_min = 0.30 → pending_review.
        result = ca.apply_signal_event("alpha", "explicit_negative")
        assert result.new_status == conf.STATUS_PENDING_REVIEW
        fired.wait(timeout=1.0)
        assert fired.is_set()
        assert captured["skill_id"] == "alpha"

    def test_pending_review_to_retired_does_not_re_fire(
        self, isolated_db, monkeypatch,
    ):
        _seed("alpha", confidence=0.12, status=conf.STATUS_PENDING_REVIEW)
        fired = threading.Event()
        monkeypatch.setattr(
            jdg, "start_judge_async",
            lambda *a, **kw: (fired.set(), threading.Thread(target=lambda: None))[1],
        )

        # 0.12 * 0.70 = 0.084 → below c_retire = 0.10 → retired.
        result = ca.apply_signal_event("alpha", "explicit_negative")
        assert result.new_status == conf.STATUS_RETIRED
        time.sleep(0.05)
        assert not fired.is_set()  # only active→pending_review fires

    def test_locked_skill_no_update_no_judge(self, isolated_db, monkeypatch):
        _seed("alpha", confidence=0.4, locked=1)
        fired = threading.Event()
        monkeypatch.setattr(
            jdg, "start_judge_async",
            lambda *a, **kw: (fired.set(), threading.Thread(target=lambda: None))[1],
        )

        result = ca.apply_signal_event("alpha", "explicit_negative")
        assert not result.applied
        time.sleep(0.05)
        assert not fired.is_set()


# ---------------------------------------------------------------------------
# Signal summary
# ---------------------------------------------------------------------------


class TestSignalSummary:
    def test_no_invocations_returns_placeholder(self, isolated_db):
        echo_db.get_echo_conn()  # bootstrap tables
        s = jdg._summarize_recent_signals("ghost")
        assert "no invocations recorded" in s

    def test_counts_signals_per_invocation(self, isolated_db):
        conn = echo_db.get_echo_conn()
        now = time.time()
        _seed("alpha")
        conn.execute(
            "INSERT INTO echo_skill_invocation "
            "(skill_id, session_id, platform, started_at) VALUES (?, ?, ?, ?)",
            ("alpha", "s1", "cli", now),
        )
        inv_id = conn.execute(
            "SELECT invocation_id FROM echo_skill_invocation"
        ).fetchone()["invocation_id"]
        for _ in range(3):
            conn.execute(
                "INSERT INTO echo_signal_event "
                "(invocation_id, skill_id, layer, signal_type, ts) "
                "VALUES (?, 'alpha', 'A', 'user_turn', ?)",
                (inv_id, now),
            )
        conn.commit()
        s = jdg._summarize_recent_signals("alpha")
        assert "user_turn=3" in s
