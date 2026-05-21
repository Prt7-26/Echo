"""Unit tests for plugins.echo_signals.usage_hook.

The monkey-patch over tools.skill_usage.bump_use is the heart of Echo's
Step 2 data path. These tests verify:

  * The wrapper calls the original bump_use first (so Hermes' own
    counters keep working).
  * After bump_use, an echo_skill_invocation row appears with the
    session_id/platform from the active session_context.
  * First-sight of a skill_name auto-creates an echo_skill_confidence
    anchor row.
  * Repeat invocations increment n_invocations.
  * Errors in Echo's path do not propagate (Hermes' bump_use behavior is
    sacrosanct — a broken Echo must never break Hermes).
  * install/uninstall are idempotent and restore module state cleanly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plugins.echo_signals import db as echo_db
from plugins.echo_signals import session_context as sc
from plugins.echo_signals import usage_hook as uh


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Same as test_db.py — point Echo at a throwaway state.db."""
    fake_db = tmp_path / "state.db"
    import hermes_state

    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", fake_db)
    echo_db.reset_for_tests()
    sc.clear_session_context()
    yield fake_db
    sc.clear_session_context()
    echo_db.reset_for_tests()


@pytest.fixture
def hook_installed(isolated_db: Path):
    """Install the monkey-patch and tear it down on test exit."""
    uh.install_bump_use_hook()
    yield
    uh.uninstall_bump_use_hook()


# ---------------------------------------------------------------------------
# Install / uninstall behavior
# ---------------------------------------------------------------------------


class TestInstall:
    def test_install_replaces_bump_use(self, isolated_db):
        import tools.skill_usage as _mod

        original = _mod.bump_use
        try:
            uh.install_bump_use_hook()
            assert _mod.bump_use is not original
            assert getattr(_mod.bump_use, "_echo_wrapped", False) is True
        finally:
            uh.uninstall_bump_use_hook()
            assert _mod.bump_use is original

    def test_install_is_idempotent(self, isolated_db):
        import tools.skill_usage as _mod

        original = _mod.bump_use
        try:
            uh.install_bump_use_hook()
            after_first = _mod.bump_use
            uh.install_bump_use_hook()  # second install is a no-op
            assert _mod.bump_use is after_first
        finally:
            uh.uninstall_bump_use_hook()
            assert _mod.bump_use is original

    def test_uninstall_when_never_installed_is_safe(self, isolated_db):
        # Should not raise even though install was never called.
        uh.uninstall_bump_use_hook()


# ---------------------------------------------------------------------------
# Data recording
# ---------------------------------------------------------------------------


class TestRecording:
    def test_bump_use_creates_invocation_row(self, hook_installed):
        import tools.skill_usage as _mod

        sc.set_session_context("sess-1", "cli")
        _mod.bump_use("test-skill")

        conn = echo_db.get_echo_conn()
        rows = conn.execute(
            "SELECT skill_id, session_id, platform FROM echo_skill_invocation"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["skill_id"] == "test-skill"
        assert rows[0]["session_id"] == "sess-1"
        assert rows[0]["platform"] == "cli"

    def test_first_use_creates_confidence_anchor(self, hook_installed):
        import tools.skill_usage as _mod

        _mod.bump_use("new-skill")
        conn = echo_db.get_echo_conn()
        row = conn.execute(
            "SELECT skill_id, confidence, n_invocations "
            "FROM echo_skill_confidence WHERE skill_id = ?",
            ("new-skill",),
        ).fetchone()
        assert row is not None
        assert row["confidence"] == 0.5  # schema default
        assert row["n_invocations"] == 1

    def test_repeat_bump_use_increments_counter(self, hook_installed):
        import tools.skill_usage as _mod

        _mod.bump_use("repeating")
        _mod.bump_use("repeating")
        _mod.bump_use("repeating")
        conn = echo_db.get_echo_conn()
        row = conn.execute(
            "SELECT n_invocations FROM echo_skill_confidence WHERE skill_id = ?",
            ("repeating",),
        ).fetchone()
        assert row["n_invocations"] == 3

        # Three rows in echo_skill_invocation too.
        n_inv = conn.execute(
            "SELECT COUNT(*) AS n FROM echo_skill_invocation WHERE skill_id = ?",
            ("repeating",),
        ).fetchone()["n"]
        assert n_inv == 3

    def test_no_session_context_uses_unknown_platform(self, hook_installed):
        """If on_session_start never fired, platform defaults to 'unknown'."""
        import tools.skill_usage as _mod

        # Don't set session context — defaults remain.
        _mod.bump_use("orphan-skill")
        conn = echo_db.get_echo_conn()
        row = conn.execute(
            "SELECT session_id, platform FROM echo_skill_invocation "
            "WHERE skill_id = ?",
            ("orphan-skill",),
        ).fetchone()
        assert row["session_id"] is None
        assert row["platform"] == "unknown"


# ---------------------------------------------------------------------------
# Hermes' bump_use must keep working even when Echo's path is broken
# ---------------------------------------------------------------------------


class TestHermesIsolation:
    def test_original_bump_use_still_called(self, hook_installed):
        """Hermes' own counter (the .usage.json sidecar) keeps updating."""
        import tools.skill_usage as _mod

        sc.set_session_context("sess-x", "cli")
        # bump_use should mutate the JSON sidecar via the original code.
        # We test this by reading back via Hermes' get_record API.
        _mod.bump_use("hermes-side-skill")
        record = _mod.get_record("hermes-side-skill")
        # get_record returns a dict; the exact shape depends on Hermes
        # implementation, but the record must be non-empty for a freshly
        # bumped skill.
        assert isinstance(record, dict)
        assert len(record) > 0  # at minimum, last_used_at or similar

    def test_echo_failure_does_not_break_bump_use(
        self, isolated_db, monkeypatch, caplog
    ):
        """If Echo's recording raises, the original bump_use still runs."""
        import logging
        import tools.skill_usage as _mod

        uh.install_bump_use_hook()
        try:
            # Force _record_invocation to blow up.
            def _boom(skill_name):
                raise RuntimeError("simulated Echo failure")

            monkeypatch.setattr(uh, "_record_invocation", _boom)
            caplog.set_level(logging.DEBUG, logger="plugins.echo_signals.usage_hook")

            # bump_use must not raise.
            _mod.bump_use("trouble-skill")

            # Original side effect (Hermes sidecar) still happened.
            record = _mod.get_record("trouble-skill")
            assert isinstance(record, dict) and len(record) > 0

            # Echo's failure was logged at DEBUG.
            assert any(
                "Echo _record_invocation" in r.message for r in caplog.records
            )
        finally:
            uh.uninstall_bump_use_hook()

    def test_empty_skill_name_skipped(self, hook_installed):
        """Defensive: bump_use('') doesn't insert garbage rows."""
        import tools.skill_usage as _mod

        _mod.bump_use("")
        conn = echo_db.get_echo_conn()
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM echo_skill_invocation"
        ).fetchone()["n"]
        assert n == 0
