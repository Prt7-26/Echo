"""Unit tests for the periodic-maintenance piggyback path."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from plugins.echo_signals import db as echo_db
from plugins.echo_signals import maintenance as maint


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    fake_db = tmp_path / "state.db"
    import hermes_state

    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", fake_db)
    echo_db.reset_for_tests()
    yield fake_db
    echo_db.reset_for_tests()


@pytest.fixture(autouse=True)
def _reset_gc():
    maint._reset_for_tests()
    yield
    maint._reset_for_tests()


class TestMaybeRunGC:
    def test_first_call_kicks_off(self, isolated_db):
        echo_db.get_echo_conn()  # bootstrap tables
        kicked = maint.maybe_run_gc()
        assert kicked is True

    def test_second_call_within_interval_skipped(self, isolated_db):
        echo_db.get_echo_conn()
        maint.maybe_run_gc()
        # Immediately after: still inside the window.
        assert maint.maybe_run_gc() is False

    def test_interval_elapsed_re_triggers(self, isolated_db):
        echo_db.get_echo_conn()
        maint.maybe_run_gc()
        # Force the timestamp back so the next call sees an elapsed window.
        with maint._gc_lock:
            maint._last_gc_ts = time.time() - maint.GC_INTERVAL_SECONDS - 10
        assert maint.maybe_run_gc() is True


class TestRunGCTasks:
    def test_deletes_old_request_log_rows(self, isolated_db):
        conn = echo_db.get_echo_conn()
        # Two rows: one old (way past retention), one fresh.
        now = time.time()
        for ts in (now - 90 * 86400, now):
            conn.execute(
                "INSERT INTO echo_user_request_log "
                "(invocation_id, skill_id, session_id, user_message, embedding, ts) "
                "VALUES (NULL, NULL, NULL, 'x', ?, ?)",
                (b"\x00" * 1024, ts),
            )
        conn.commit()

        maint._run_gc_tasks()

        remaining = conn.execute(
            "SELECT ts FROM echo_user_request_log"
        ).fetchall()
        assert len(remaining) == 1
        assert remaining[0]["ts"] == pytest.approx(now, abs=10)

    def test_deletes_old_turn_cache_rows(self, isolated_db):
        conn = echo_db.get_echo_conn()
        now = time.time()
        # Two rows: one old, one fresh.
        conn.execute(
            "INSERT INTO echo_turn_cache "
            "(session_id, skill_id, user_message, assistant_response, updated_at) "
            "VALUES ('old-session', 'alpha', 'u', 'a', ?)",
            (now - 30 * 86400,),
        )
        conn.execute(
            "INSERT INTO echo_turn_cache "
            "(session_id, skill_id, user_message, assistant_response, updated_at) "
            "VALUES ('fresh-session', 'alpha', 'u', 'a', ?)",
            (now,),
        )
        conn.commit()

        maint._run_gc_tasks()

        rows = conn.execute(
            "SELECT session_id FROM echo_turn_cache"
        ).fetchall()
        assert [r["session_id"] for r in rows] == ["fresh-session"]

    def test_gc_failure_does_not_raise(self, isolated_db, monkeypatch):
        """A broken sub-task must not propagate up."""
        echo_db.get_echo_conn()
        # Force gc_old_requests to blow up.
        from plugins.echo_signals import m1_trigger
        monkeypatch.setattr(
            m1_trigger, "gc_old_requests",
            lambda **kw: (_ for _ in ()).throw(RuntimeError("simulated")),
        )
        maint._run_gc_tasks()  # must not raise


class TestSessionStartIntegration:
    def test_on_session_start_invokes_gc(self, isolated_db, monkeypatch):
        echo_db.get_echo_conn()
        seen = threading.Event()

        def _spy():
            seen.set()
            return False

        monkeypatch.setattr(maint, "maybe_run_gc", _spy)
        from plugins.echo_signals import _on_session_start
        _on_session_start(session_id="s", platform="cli")
        assert seen.is_set()
