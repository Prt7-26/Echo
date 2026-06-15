"""Unit tests for plugins.echo_signals.signals.

Three categories:
  * record_signal: low-level INSERT correctness, side effects on n_signals.
  * Hook handlers (on_pre_llm_call, on_post_tool_call, on_session_end_signal):
    each one fires only when an invocation is active, and emits the
    correct signal_type + value columns.
  * End-to-end: a simulated session flow (set_session_context →
    bump_use → user turns → tool calls → session_end) writes the
    expected rows.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plugins.echo_signals import db as echo_db
from plugins.echo_signals import session_context as sc
from plugins.echo_signals import signals as sig
from plugins.echo_signals import usage_hook as uh


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Point Echo at a throwaway state.db."""
    fake_db = tmp_path / "state.db"
    import hermes_state

    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", fake_db)
    echo_db.reset_for_tests()
    sc.clear_session_context()
    yield fake_db
    sc.clear_session_context()
    echo_db.reset_for_tests()


@pytest.fixture
def active_invocation(isolated_db: Path):
    """Set up an active invocation by simulating a bump_use call."""
    uh.install_bump_use_hook()
    sc.set_session_context("test-session", "cli")
    import tools.skill_usage as _mod

    _mod.bump_use("test-skill")
    yield
    uh.uninstall_bump_use_hook()


# ---------------------------------------------------------------------------
# record_signal: low-level writes
# ---------------------------------------------------------------------------


class TestRecordSignal:
    def test_inserts_event_row(self, active_invocation):
        invocation_id = sc.get_current_invocation_id()
        assert invocation_id is not None

        sig.record_signal(
            invocation_id=invocation_id,
            layer="A",
            signal_type="user_turn",
        )
        conn = echo_db.get_echo_conn()
        rows = conn.execute(
            "SELECT skill_id, layer, signal_type "
            "FROM echo_signal_event WHERE invocation_id = ?",
            (invocation_id,),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["skill_id"] == "test-skill"
        assert rows[0]["layer"] == "A"
        assert rows[0]["signal_type"] == "user_turn"

    def test_denormalizes_skill_id_via_invocation(self, active_invocation):
        """signal row's skill_id is looked up from the invocation, not passed."""
        invocation_id = sc.get_current_invocation_id()
        sig.record_signal(
            invocation_id=invocation_id, layer="A", signal_type="tool_call",
            value_text="bash",
        )
        conn = echo_db.get_echo_conn()
        row = conn.execute(
            "SELECT skill_id, value_text FROM echo_signal_event"
        ).fetchone()
        assert row["skill_id"] == "test-skill"
        assert row["value_text"] == "bash"

    def test_increments_n_signals_on_confidence(self, active_invocation):
        invocation_id = sc.get_current_invocation_id()
        conn = echo_db.get_echo_conn()
        before = conn.execute(
            "SELECT n_signals FROM echo_skill_confidence WHERE skill_id = ?",
            ("test-skill",),
        ).fetchone()["n_signals"]

        sig.record_signal(invocation_id=invocation_id, layer="A", signal_type="user_turn")
        sig.record_signal(invocation_id=invocation_id, layer="A", signal_type="user_turn")

        after = conn.execute(
            "SELECT n_signals FROM echo_skill_confidence WHERE skill_id = ?",
            ("test-skill",),
        ).fetchone()["n_signals"]
        assert after - before == 2

    def test_unknown_invocation_id_silently_skipped(self, isolated_db: Path):
        """A signal pointing at a non-existent invocation logs and returns."""
        # Bootstrap the connection so the table exists, but don't insert
        # an invocation row.
        echo_db.get_echo_conn()
        sig.record_signal(
            invocation_id=999999, layer="A", signal_type="user_turn",
        )
        conn = echo_db.get_echo_conn()
        n = conn.execute("SELECT COUNT(*) AS n FROM echo_signal_event").fetchone()["n"]
        assert n == 0


# ---------------------------------------------------------------------------
# Hook handler short-circuits
# ---------------------------------------------------------------------------


class TestHookShortCircuits:
    def test_pre_llm_call_assistant_tool_skipped(self, active_invocation):
        # Explicit non-user tags are still ignored.
        sig.on_pre_llm_call(turn_type="assistant")
        sig.on_pre_llm_call(turn_type="tool")
        sig.on_pre_llm_call(turn_type="system")
        conn = echo_db.get_echo_conn()
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM echo_signal_event "
            "WHERE signal_type = 'user_turn'"
        ).fetchone()["n"]
        assert n == 0

    def test_pre_llm_call_untagged_is_user_turn(self, active_invocation):
        # Live Hermes fires pre_llm_call once per user turn WITHOUT a turn_type
        # kwarg — that must be treated as a user turn (regression: the old
        # turn_type=="user" gate suppressed every Layer A/B signal at runtime).
        sig.on_pre_llm_call(turn_type="")
        conn = echo_db.get_echo_conn()
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM echo_signal_event "
            "WHERE signal_type = 'user_turn'"
        ).fetchone()["n"]
        assert n == 1

    def test_pre_llm_call_user_turn_records(self, active_invocation):
        sig.on_pre_llm_call(turn_type="user")
        conn = echo_db.get_echo_conn()
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM echo_signal_event "
            "WHERE signal_type = 'user_turn'"
        ).fetchone()["n"]
        assert n == 1

    def test_no_active_invocation_pre_llm_call_skipped(self, isolated_db: Path):
        sc.clear_session_context()
        echo_db.get_echo_conn()
        sig.on_pre_llm_call(turn_type="user")
        conn = echo_db.get_echo_conn()
        n = conn.execute("SELECT COUNT(*) AS n FROM echo_signal_event").fetchone()["n"]
        assert n == 0


class TestPreLlmCallSetsSessionContext:
    """Regression: resumed conversations never fire on_session_start, so the
    session contextvar must be (re)set from pre_llm_call — otherwise bump_use
    writes invocations with a NULL session_id and the per-conversation rating
    queue never surfaces them.
    """

    def test_sets_context_from_session_id(self, isolated_db: Path):
        sc.clear_session_context()
        assert sc.get_session_id() is None
        sig.on_pre_llm_call(session_id="resumed-sess", platform="tui")
        assert sc.get_session_id() == "resumed-sess"
        assert sc.get_platform() == "tui"

    def test_runs_before_turn_type_gate(self, isolated_db: Path):
        # Even a non-user fire (no signal recorded) must still refresh context.
        sc.clear_session_context()
        sig.on_pre_llm_call(turn_type="assistant", session_id="sess-A")
        assert sc.get_session_id() == "sess-A"

    def test_missing_session_id_leaves_context_untouched(self, isolated_db: Path):
        sc.set_session_context("existing", "cli")
        sig.on_pre_llm_call(turn_type="assistant")  # no session_id kwarg
        assert sc.get_session_id() == "existing"

    def test_post_tool_call_records_tool_name(self, active_invocation):
        sig.on_post_tool_call(tool_name="execute_bash")
        conn = echo_db.get_echo_conn()
        row = conn.execute(
            "SELECT value_text FROM echo_signal_event "
            "WHERE signal_type = 'tool_call'"
        ).fetchone()
        assert row["value_text"] == "execute_bash"

    def test_post_tool_call_no_invocation_skipped(self, isolated_db: Path):
        echo_db.get_echo_conn()
        sig.on_post_tool_call(tool_name="execute_bash")
        conn = echo_db.get_echo_conn()
        n = conn.execute("SELECT COUNT(*) AS n FROM echo_signal_event").fetchone()["n"]
        assert n == 0

    def test_post_tool_call_no_result_records_only_tool_call(self, active_invocation):
        """No result kwarg → no failure claim, just the tool_call event."""
        sig.on_post_tool_call(tool_name="execute_bash")
        conn = echo_db.get_echo_conn()
        types = [r["signal_type"] for r in conn.execute(
            "SELECT signal_type FROM echo_signal_event").fetchall()]
        assert types == ["tool_call"]  # no tool_error fabricated

    def test_post_tool_call_failed_result_records_tool_error(self, active_invocation):
        sig.on_post_tool_call(tool_name="execute_bash",
                              result={"error": "command not found"})
        conn = echo_db.get_echo_conn()
        types = sorted(r["signal_type"] for r in conn.execute(
            "SELECT signal_type FROM echo_signal_event").fetchall())
        assert types == ["tool_call", "tool_error"]

    def test_post_tool_call_success_result_no_error(self, active_invocation):
        sig.on_post_tool_call(tool_name="read_file", result="file contents here")
        conn = echo_db.get_echo_conn()
        types = [r["signal_type"] for r in conn.execute(
            "SELECT signal_type FROM echo_signal_event").fetchall()]
        assert types == ["tool_call"]


class TestToolCallFailed:
    def test_none_is_not_failure(self):
        # Conservative: no positive failure evidence → success.
        assert sig.tool_call_failed(None) is False

    def test_empty_string_is_not_failure(self):
        # Many tools succeed with no output.
        assert sig.tool_call_failed("   ") is False
        assert sig.tool_call_failed("") is False

    def test_error_prefix_string(self):
        assert sig.tool_call_failed("Error: boom") is True
        assert sig.tool_call_failed("Traceback (most recent call last)") is True

    def test_normal_string_ok(self):
        assert sig.tool_call_failed("done, wrote 3 files") is False

    def test_dict_error_key(self):
        assert sig.tool_call_failed({"error": "nope"}) is True
        assert sig.tool_call_failed({"ok": False}) is True
        assert sig.tool_call_failed({"exit_code": 1}) is True

    def test_dict_clean(self):
        assert sig.tool_call_failed({"ok": True, "output": "x"}) is False
        assert sig.tool_call_failed({"exit_code": 0}) is False

    def test_session_end_signal_records(self, active_invocation):
        sig.on_session_end_signal()
        conn = echo_db.get_echo_conn()
        row = conn.execute(
            "SELECT signal_type FROM echo_signal_event "
            "WHERE signal_type = 'session_ended'"
        ).fetchone()
        assert row is not None

    def test_session_end_signal_no_invocation_skipped(self, isolated_db: Path):
        echo_db.get_echo_conn()
        sig.on_session_end_signal()
        conn = echo_db.get_echo_conn()
        n = conn.execute("SELECT COUNT(*) AS n FROM echo_signal_event").fetchone()["n"]
        assert n == 0


# ---------------------------------------------------------------------------
# End-to-end: realistic session flow
# ---------------------------------------------------------------------------


class TestRealisticFlow:
    """Simulate: user loads skill, has 3 turns with 2 tool calls, ends session."""

    def test_session_flow_writes_expected_rows(self, isolated_db: Path):
        from plugins.echo_signals import _on_session_end, _on_session_start

        uh.install_bump_use_hook()
        try:
            # 1. Session starts
            _on_session_start(session_id="session-42", platform="cli")

            # 2. User invokes a skill
            import tools.skill_usage as _mod

            _mod.bump_use("draft-email")

            # 3. Three user turns
            sig.on_pre_llm_call(turn_type="user")
            sig.on_pre_llm_call(turn_type="user")
            sig.on_pre_llm_call(turn_type="user")

            # 4. Some internal/assistant pre_llm_call noise we should ignore
            sig.on_pre_llm_call(turn_type="assistant")
            sig.on_pre_llm_call(turn_type="tool")

            # 5. Two tool calls during the session
            sig.on_post_tool_call(tool_name="send_email")
            sig.on_post_tool_call(tool_name="read_file")

            # 6. Session ends
            _on_session_end()

            # ── Verify ──
            conn = echo_db.get_echo_conn()

            # Invocation row exists with correct attribution
            inv = conn.execute(
                "SELECT skill_id, session_id, platform "
                "FROM echo_skill_invocation WHERE skill_id = 'draft-email'"
            ).fetchone()
            assert inv["session_id"] == "session-42"
            assert inv["platform"] == "cli"

            # Three user_turn signals (assistant/tool turn_types ignored)
            n_user = conn.execute(
                "SELECT COUNT(*) AS n FROM echo_signal_event "
                "WHERE signal_type = 'user_turn'"
            ).fetchone()["n"]
            assert n_user == 3

            # Two tool_call signals with the right names
            tool_rows = conn.execute(
                "SELECT value_text FROM echo_signal_event "
                "WHERE signal_type = 'tool_call' ORDER BY event_id"
            ).fetchall()
            assert [r["value_text"] for r in tool_rows] == ["send_email", "read_file"]

            # One session_ended signal
            n_end = conn.execute(
                "SELECT COUNT(*) AS n FROM echo_signal_event "
                "WHERE signal_type = 'session_ended'"
            ).fetchone()["n"]
            assert n_end == 1

            # confidence.n_signals = 3 + 2 + 1 = 6
            conf = conn.execute(
                "SELECT n_signals, n_invocations FROM echo_skill_confidence "
                "WHERE skill_id = 'draft-email'"
            ).fetchone()
            assert conf["n_signals"] == 6
            assert conf["n_invocations"] == 1

            # After session end, current invocation should be cleared
            assert sc.get_current_invocation_id() is None
        finally:
            uh.uninstall_bump_use_hook()

    def test_last_skill_wins_attribution(self, isolated_db: Path):
        """If a user loads two skills in one session, later signals go to the second."""
        from plugins.echo_signals import _on_session_start

        uh.install_bump_use_hook()
        try:
            _on_session_start(session_id="multi", platform="cli")

            import tools.skill_usage as _mod

            _mod.bump_use("first-skill")
            sig.on_pre_llm_call(turn_type="user")  # attributed to first-skill

            _mod.bump_use("second-skill")
            sig.on_pre_llm_call(turn_type="user")  # attributed to second-skill
            sig.on_post_tool_call(tool_name="some_tool")  # second-skill too

            conn = echo_db.get_echo_conn()
            rows = conn.execute(
                "SELECT skill_id, signal_type FROM echo_signal_event "
                "ORDER BY event_id"
            ).fetchall()
            assert [(r["skill_id"], r["signal_type"]) for r in rows] == [
                ("first-skill", "user_turn"),
                ("second-skill", "user_turn"),
                ("second-skill", "tool_call"),
            ]
        finally:
            sc.clear_session_context()
            uh.uninstall_bump_use_hook()
