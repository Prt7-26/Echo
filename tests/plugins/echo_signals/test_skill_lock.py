"""Tests for M4 manual-edit lock (plugins.echo_signals.skill_lock)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from plugins.echo_signals import db as echo_db
from plugins.echo_signals import skill_lock as sl


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Isolated HERMES_HOME so _skill_md_path resolves under tmp + a fresh DB."""
    fake_db = tmp_path / "state.db"
    import hermes_state
    import hermes_constants

    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", fake_db)
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path)
    echo_db.reset_for_tests()
    yield tmp_path
    echo_db.reset_for_tests()


def _seed_skill(skill_id: str, locked: int = 0):
    conn = echo_db.get_echo_conn()
    now = time.time()
    conn.execute(
        "INSERT INTO echo_skill_confidence (skill_id, locked, created_at, updated_at) "
        "VALUES (?, ?, ?, ?)",
        (skill_id, locked, now, now),
    )
    conn.commit()


def _write_skill_md(home: Path, skill_id: str, content: str):
    d = home / "skills" / skill_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(content, encoding="utf-8")


def _is_locked(skill_id: str) -> bool:
    conn = echo_db.get_echo_conn()
    return bool(conn.execute(
        "SELECT locked FROM echo_skill_confidence WHERE skill_id = ?",
        (skill_id,),
    ).fetchone()["locked"])


class TestCheckSkillEdits:
    def test_first_sight_baselines_no_lock(self, isolated_home):
        _seed_skill("alpha")
        _write_skill_md(isolated_home, "alpha", "name: alpha\nsteps: do X")
        n = sl.check_skill_edits()
        assert n == 0
        assert not _is_locked("alpha")
        # Hash recorded.
        conn = echo_db.get_echo_conn()
        row = conn.execute(
            "SELECT content_hash FROM echo_skill_content_hash WHERE skill_id='alpha'"
        ).fetchone()
        assert row is not None and row["content_hash"]

    def test_unchanged_does_not_lock(self, isolated_home):
        _seed_skill("alpha")
        _write_skill_md(isolated_home, "alpha", "name: alpha\nsteps: do X")
        sl.check_skill_edits()          # baseline
        n = sl.check_skill_edits()      # again, unchanged
        assert n == 0
        assert not _is_locked("alpha")

    def test_manual_edit_locks(self, isolated_home):
        _seed_skill("alpha")
        _write_skill_md(isolated_home, "alpha", "name: alpha\nsteps: do X")
        sl.check_skill_edits()          # baseline
        # User hand-edits the file (no skill_manage op recorded).
        _write_skill_md(isolated_home, "alpha", "name: alpha\nsteps: do X then Y")
        n = sl.check_skill_edits()
        assert n == 1
        assert _is_locked("alpha")

    def test_agent_edit_does_not_lock(self, isolated_home):
        _seed_skill("alpha")
        _write_skill_md(isolated_home, "alpha", "name: alpha\nsteps: do X")
        sl.check_skill_edits()          # baseline
        # The agent rewrites the skill via skill_manage — record that, then
        # the content changes. Should NOT lock (it's the agent's edit).
        sl.record_agent_managed("alpha", "update")
        _write_skill_md(isolated_home, "alpha", "name: alpha\nsteps: totally new")
        n = sl.check_skill_edits()
        assert n == 0
        assert not _is_locked("alpha")

    def test_already_locked_skipped(self, isolated_home):
        _seed_skill("alpha", locked=1)
        _write_skill_md(isolated_home, "alpha", "x")
        n = sl.check_skill_edits()
        assert n == 0  # already locked, not re-counted

    def test_missing_skill_md_skipped(self, isolated_home):
        _seed_skill("ghost")  # no file on disk
        n = sl.check_skill_edits()
        assert n == 0
        assert not _is_locked("ghost")

    def test_lock_is_sticky_across_runs(self, isolated_home):
        _seed_skill("alpha")
        _write_skill_md(isolated_home, "alpha", "v1")
        sl.check_skill_edits()
        _write_skill_md(isolated_home, "alpha", "v2-manual")
        sl.check_skill_edits()
        assert _is_locked("alpha")
        # A subsequent edit doesn't double-count (already locked → skipped).
        _write_skill_md(isolated_home, "alpha", "v3-manual")
        assert sl.check_skill_edits() == 0
        assert _is_locked("alpha")


class TestOnPostToolCall:
    def test_skill_manage_records_agent_managed(self, isolated_home):
        echo_db.get_echo_conn()
        sl.on_post_tool_call(
            tool_name="skill_manage",
            args={"action": "update", "name": "alpha"},
        )
        conn = echo_db.get_echo_conn()
        row = conn.execute(
            "SELECT agent_managed_at FROM echo_skill_content_hash WHERE skill_id='alpha'"
        ).fetchone()
        assert row is not None and row["agent_managed_at"] is not None

    def test_non_skill_manage_ignored(self, isolated_home):
        echo_db.get_echo_conn()
        sl.on_post_tool_call(tool_name="execute_bash", args={})
        conn = echo_db.get_echo_conn()
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM echo_skill_content_hash"
        ).fetchone()["n"]
        assert n == 0

    def test_read_action_not_recorded(self, isolated_home):
        echo_db.get_echo_conn()
        sl.on_post_tool_call(
            tool_name="skill_manage",
            args={"action": "read", "name": "alpha"},
        )
        conn = echo_db.get_echo_conn()
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM echo_skill_content_hash"
        ).fetchone()["n"]
        assert n == 0  # 'read' is not a content-mutating action
