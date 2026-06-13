"""Unit tests for M2 — scope_dialog hook and the matching dashboard API.

Three categories:
  * _extract_action_and_name / _looks_like_create_success — pure parsing.
  * on_post_tool_call — only fires the scope row for skill_manage(create).
  * Integration with the dashboard's /scope endpoints — list pending
    queue, write user choice, idempotence, overwrite semantics.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins.echo_signals import db as echo_db
from plugins.echo_signals import scope_dialog as sd
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


# ---------------------------------------------------------------------------
# _extract_action_and_name — pure
# ---------------------------------------------------------------------------


class TestExtractActionAndName:
    def test_clean_dict(self):
        assert sd._extract_action_and_name(
            {"action": "create", "name": "draft-email"}
        ) == ("create", "draft-email")

    def test_strips_whitespace(self):
        assert sd._extract_action_and_name(
            {"action": "  create  ", "name": "  x  "}
        ) == ("create", "x")

    def test_non_dict_returns_empty(self):
        assert sd._extract_action_and_name(None) == ("", "")
        assert sd._extract_action_and_name("a string") == ("", "")
        assert sd._extract_action_and_name(42) == ("", "")

    def test_non_string_values_return_empty(self):
        # action=int: type check fails → ("", "")
        assert sd._extract_action_and_name({"action": 1, "name": "x"}) == ("", "")
        # name=None: `or ""` coerces, ends up ("create", ""); the caller's
        # `if not skill_name: return` short-circuits so no scope row written.
        assert sd._extract_action_and_name({"action": "create", "name": None}) == ("create", "")

    def test_missing_keys(self):
        assert sd._extract_action_and_name({}) == ("", "")
        assert sd._extract_action_and_name({"action": "create"}) == ("create", "")


# ---------------------------------------------------------------------------
# _looks_like_create_success — heuristic
# ---------------------------------------------------------------------------


class TestLooksLikeCreateSuccess:
    def test_none_is_failure(self):
        assert sd._looks_like_create_success(None) is False

    def test_dict_with_error_key(self):
        assert sd._looks_like_create_success({"error": "name collision"}) is False

    def test_dict_with_failed_flag(self):
        assert sd._looks_like_create_success({"failed": True, "skill": "x"}) is False

    def test_clean_dict_passes(self):
        assert sd._looks_like_create_success({"skill_id": "x"}) is True

    def test_string_starting_with_error_payload(self):
        assert sd._looks_like_create_success('{"error": "no"}') is False
        assert sd._looks_like_create_success("error: nope") is False

    def test_normal_string_passes(self):
        assert sd._looks_like_create_success("Created skill 'foo'.") is True


# ---------------------------------------------------------------------------
# on_post_tool_call — wedge point behavior
# ---------------------------------------------------------------------------


class TestOnPostToolCall:
    def test_non_skill_manage_tool_ignored(self, isolated_db):
        sd.on_post_tool_call(tool_name="bash", args={"command": "ls"}, result="ok")
        conn = echo_db.get_echo_conn()
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM echo_skill_scope"
        ).fetchone()["n"]
        assert n == 0

    def test_skill_manage_non_create_action_ignored(self, isolated_db):
        sd.on_post_tool_call(
            tool_name="skill_manage",
            args={"action": "delete", "name": "x"},
            result="ok",
        )
        conn = echo_db.get_echo_conn()
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM echo_skill_scope"
        ).fetchone()["n"]
        assert n == 0

    def test_failed_create_ignored(self, isolated_db):
        sd.on_post_tool_call(
            tool_name="skill_manage",
            args={"action": "create", "name": "doomed"},
            result={"error": "name collision"},
        )
        conn = echo_db.get_echo_conn()
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM echo_skill_scope"
        ).fetchone()["n"]
        assert n == 0

    def test_successful_create_writes_pending_row(self, isolated_db):
        sd.on_post_tool_call(
            tool_name="skill_manage",
            args={"action": "create", "name": "fresh-skill"},
            result="Created.",
        )
        conn = echo_db.get_echo_conn()
        row = conn.execute(
            "SELECT skill_id, scope_level FROM echo_skill_scope "
            "WHERE skill_id = 'fresh-skill'"
        ).fetchone()
        assert row is not None
        assert row["scope_level"] == "unknown"

    def test_successful_create_seeds_confidence_anchor(self, isolated_db):
        """We also seed echo_skill_confidence so the dashboard shows the
        new skill in the ranking immediately."""
        sd.on_post_tool_call(
            tool_name="skill_manage",
            args={"action": "create", "name": "fresh-skill"},
            result="Created.",
        )
        conn = echo_db.get_echo_conn()
        row = conn.execute(
            "SELECT skill_id, confidence FROM echo_skill_confidence "
            "WHERE skill_id = 'fresh-skill'"
        ).fetchone()
        assert row is not None
        assert row["confidence"] == 0.5

    def test_save_intent_raises_initial_confidence(self, isolated_db):
        """proposal §M4: a skill created right after the user said 'save
        this' starts with a higher initial confidence than the neutral
        default."""
        conn = echo_db.get_echo_conn()
        now = time.time()
        # An m1_save_intent signal needs an invocation to hang off (FK).
        conn.execute(
            "INSERT INTO echo_skill_confidence (skill_id, created_at, updated_at) "
            "VALUES ('prior', ?, ?)", (now, now),
        )
        cur = conn.execute(
            "INSERT INTO echo_skill_invocation (skill_id, platform, started_at) "
            "VALUES ('prior', 'cli', ?)", (now,),
        )
        inv = cur.lastrowid
        conn.execute(
            "INSERT INTO echo_signal_event "
            "(invocation_id, skill_id, layer, signal_type, ts) "
            "VALUES (?, 'prior', 'B', 'm1_save_intent', ?)", (inv, now),
        )
        conn.commit()

        sd.on_post_tool_call(
            tool_name="skill_manage",
            args={"action": "create", "name": "saved-skill"},
            result="Created.",
        )
        c = conn.execute(
            "SELECT confidence FROM echo_skill_confidence WHERE skill_id='saved-skill'"
        ).fetchone()["confidence"]
        from plugins.echo_signals.confidence import INITIAL_CONFIDENCE_SAVE_INTENT
        assert c == pytest.approx(INITIAL_CONFIDENCE_SAVE_INTENT)

    def test_stale_save_intent_does_not_raise_confidence(self, isolated_db):
        """A save-intent from long ago (outside the lookback window) must
        NOT bump a freshly created skill's initial confidence."""
        conn = echo_db.get_echo_conn()
        now = time.time()
        old = now - sd.SAVE_INTENT_LOOKBACK_SECONDS - 60
        conn.execute(
            "INSERT INTO echo_skill_confidence (skill_id, created_at, updated_at) "
            "VALUES ('prior', ?, ?)", (now, now),
        )
        cur = conn.execute(
            "INSERT INTO echo_skill_invocation (skill_id, platform, started_at) "
            "VALUES ('prior', 'cli', ?)", (old,),
        )
        inv = cur.lastrowid
        conn.execute(
            "INSERT INTO echo_signal_event "
            "(invocation_id, skill_id, layer, signal_type, ts) "
            "VALUES (?, 'prior', 'B', 'm1_save_intent', ?)", (inv, old),
        )
        conn.commit()
        sd.on_post_tool_call(
            tool_name="skill_manage",
            args={"action": "create", "name": "later-skill"},
            result="Created.",
        )
        c = conn.execute(
            "SELECT confidence FROM echo_skill_confidence WHERE skill_id='later-skill'"
        ).fetchone()["confidence"]
        assert c == pytest.approx(0.5)

    def test_idempotent_on_recreate(self, isolated_db):
        sd.on_post_tool_call(
            tool_name="skill_manage",
            args={"action": "create", "name": "x"},
            result="Created.",
        )
        # Simulate user later confirming scope_level via the API
        conn = echo_db.get_echo_conn()
        now = time.time()
        conn.execute(
            "UPDATE echo_skill_scope SET scope_level='broad', user_confirmed_at=? "
            "WHERE skill_id='x'",
            (now,),
        )
        conn.commit()
        # Re-create the skill (delete + create workflow)
        sd.on_post_tool_call(
            tool_name="skill_manage",
            args={"action": "create", "name": "x"},
            result="Created.",
        )
        # User's prior 'broad' choice must NOT be clobbered back to 'unknown'.
        row = conn.execute(
            "SELECT scope_level FROM echo_skill_scope WHERE skill_id='x'"
        ).fetchone()
        assert row["scope_level"] == "broad"

    def test_missing_name_ignored(self, isolated_db):
        sd.on_post_tool_call(
            tool_name="skill_manage",
            args={"action": "create"},
            result="ok",
        )
        conn = echo_db.get_echo_conn()
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM echo_skill_scope"
        ).fetchone()["n"]
        assert n == 0


# ---------------------------------------------------------------------------
# Dashboard API — /scope/pending and /scope
# ---------------------------------------------------------------------------


def _seed_scope(skill_id: str, level: str = "unknown",
                created_at: float | None = None):
    conn = echo_db.get_echo_conn()
    if created_at is None:
        created_at = time.time()
    # Also need the confidence anchor for FK consistency.
    conn.execute(
        "INSERT OR IGNORE INTO echo_skill_confidence "
        "(skill_id, created_at, updated_at) VALUES (?, ?, ?)",
        (skill_id, created_at, created_at),
    )
    conn.execute(
        "INSERT INTO echo_skill_scope "
        "(skill_id, scope_level, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (skill_id, level, created_at, created_at),
    )
    conn.commit()


class TestScopeAPI:
    def test_pending_empty(self, client):
        r = client.get("/api/plugins/echo_signals/scope/pending")
        assert r.status_code == 200
        assert r.json() == {"pending": []}

    def test_pending_lists_unknown_only(self, client):
        _seed_scope("a", level="unknown")
        _seed_scope("b", level="broad")
        _seed_scope("c", level="unknown")
        r = client.get("/api/plugins/echo_signals/scope/pending")
        ids = sorted(p["skill_id"] for p in r.json()["pending"])
        assert ids == ["a", "c"]

    def test_pending_most_recent_first(self, client):
        _seed_scope("old", created_at=100.0)
        _seed_scope("middle", created_at=200.0)
        _seed_scope("newest", created_at=300.0)
        r = client.get("/api/plugins/echo_signals/scope/pending")
        ids = [p["skill_id"] for p in r.json()["pending"]]
        assert ids == ["newest", "middle", "old"]

    def test_post_writes_broad(self, client):
        _seed_scope("a")
        r = client.post(
            "/api/plugins/echo_signals/scope",
            json={"skill_id": "a", "scope_level": "broad"},
        )
        assert r.status_code == 200
        assert r.json()["scope_level"] == "broad"

        conn = echo_db.get_echo_conn()
        row = conn.execute(
            "SELECT scope_level, user_confirmed_at FROM echo_skill_scope WHERE skill_id='a'"
        ).fetchone()
        assert row["scope_level"] == "broad"
        assert row["user_confirmed_at"] is not None

    def test_post_writes_narrow(self, client):
        _seed_scope("a")
        r = client.post(
            "/api/plugins/echo_signals/scope",
            json={"skill_id": "a", "scope_level": "narrow"},
        )
        assert r.json()["scope_level"] == "narrow"

    def test_post_overwrites_previous_choice(self, client):
        _seed_scope("a")
        client.post(
            "/api/plugins/echo_signals/scope",
            json={"skill_id": "a", "scope_level": "broad"},
        )
        r = client.post(
            "/api/plugins/echo_signals/scope",
            json={"skill_id": "a", "scope_level": "narrow"},
        )
        assert r.json()["scope_level"] == "narrow"

    def test_post_creates_row_when_absent(self, client):
        """A skill without any prior scope row — POST should still land."""
        # Echo needs the confidence anchor row only for FK; the scope row
        # itself can be created fresh.
        echo_db.get_echo_conn()  # bootstrap
        conn = echo_db.get_echo_conn()
        now = time.time()
        conn.execute(
            "INSERT INTO echo_skill_confidence "
            "(skill_id, created_at, updated_at) VALUES ('new-skill', ?, ?)",
            (now, now),
        )
        conn.commit()

        r = client.post(
            "/api/plugins/echo_signals/scope",
            json={"skill_id": "new-skill", "scope_level": "broad"},
        )
        assert r.status_code == 200
        row = conn.execute(
            "SELECT scope_level FROM echo_skill_scope WHERE skill_id='new-skill'"
        ).fetchone()
        assert row["scope_level"] == "broad"

    def test_post_rejects_invalid_level(self, client):
        _seed_scope("a")
        r = client.post(
            "/api/plugins/echo_signals/scope",
            json={"skill_id": "a", "scope_level": "garbage"},
        )
        assert r.status_code == 422

    def test_post_rejects_unknown_level(self, client):
        """'unknown' is not a user-settable value — it's the pending state."""
        _seed_scope("a")
        r = client.post(
            "/api/plugins/echo_signals/scope",
            json={"skill_id": "a", "scope_level": "unknown"},
        )
        assert r.status_code == 422
