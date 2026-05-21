"""Unit tests for the Echo dashboard plugin's REST endpoints.

We mount the router on a fresh FastAPI app rather than going through
Hermes' full _mount_plugin_api_routes scanner. This isolates API
behavior from the discovery/mounting machinery (which has its own
coverage in tests/hermes_cli/test_plugins_cmd.py) and lets us assert
status codes, response shapes, ordering, and edge cases directly.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins.echo_signals import db as echo_db
from plugins.echo_signals.dashboard.plugin_api import router


@pytest.fixture
def client(tmp_path, monkeypatch):
    """FastAPI app + isolated state.db for one test."""
    fake_db = tmp_path / "state.db"
    import hermes_state

    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", fake_db)
    echo_db.reset_for_tests()

    app = FastAPI()
    app.include_router(router, prefix="/api/plugins/echo_signals")
    yield TestClient(app)

    echo_db.reset_for_tests()


# ---------------------------------------------------------------------------
# Test helpers — seed the DB with realistic fixtures
# ---------------------------------------------------------------------------


def _seed_skill(skill_id: str, confidence: float = 0.5,
                status: str = "active", n_invocations: int = 0,
                n_signals: int = 0, locked: int = 0):
    conn = echo_db.get_echo_conn()
    now = time.time()
    conn.execute(
        "INSERT INTO echo_skill_confidence "
        "(skill_id, confidence, status, locked, n_invocations, n_signals, "
        " created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (skill_id, confidence, status, locked, n_invocations, n_signals,
         now, now),
    )
    conn.commit()


def _seed_invocation(skill_id: str, session_id: str = "s1",
                     platform: str = "cli", started_at: float | None = None) -> int:
    conn = echo_db.get_echo_conn()
    if started_at is None:
        started_at = time.time()
    cur = conn.execute(
        "INSERT INTO echo_skill_invocation "
        "(skill_id, session_id, platform, started_at) "
        "VALUES (?, ?, ?, ?)",
        (skill_id, session_id, platform, started_at),
    )
    conn.commit()
    return cur.lastrowid


def _seed_event(invocation_id: int, skill_id: str, signal_type: str,
                layer: str = "A", value_text: str | None = None,
                ts: float | None = None):
    conn = echo_db.get_echo_conn()
    if ts is None:
        ts = time.time()
    conn.execute(
        "INSERT INTO echo_signal_event "
        "(invocation_id, skill_id, layer, signal_type, value_text, ts) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (invocation_id, skill_id, layer, signal_type, value_text, ts),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# GET /skills
# ---------------------------------------------------------------------------


class TestListSkills:
    def test_empty_db(self, client):
        r = client.get("/api/plugins/echo_signals/skills")
        assert r.status_code == 200
        assert r.json() == {"skills": []}

    def test_returns_all_columns(self, client):
        _seed_skill("alpha", confidence=0.7, n_invocations=3, n_signals=10)
        r = client.get("/api/plugins/echo_signals/skills")
        rows = r.json()["skills"]
        assert len(rows) == 1
        row = rows[0]
        assert row["skill_id"] == "alpha"
        assert row["confidence"] == 0.7
        assert row["n_invocations"] == 3
        assert row["n_signals"] == 10
        assert row["locked"] == 0

    def test_orders_by_confidence_ascending(self, client):
        """Worst-first ordering is the whole point — lock it down."""
        _seed_skill("healthy", confidence=0.9)
        _seed_skill("middling", confidence=0.5)
        _seed_skill("hurting", confidence=0.2)
        r = client.get("/api/plugins/echo_signals/skills")
        ids = [s["skill_id"] for s in r.json()["skills"]]
        assert ids == ["hurting", "middling", "healthy"]

    def test_status_filter(self, client):
        _seed_skill("a", confidence=0.9, status="active")
        _seed_skill("b", confidence=0.2, status="pending_review")
        _seed_skill("c", confidence=0.05, status="retired")
        r = client.get(
            "/api/plugins/echo_signals/skills?status=pending_review"
        )
        ids = [s["skill_id"] for s in r.json()["skills"]]
        assert ids == ["b"]

    def test_invalid_status_rejected(self, client):
        r = client.get("/api/plugins/echo_signals/skills?status=garbage")
        assert r.status_code == 422  # pydantic regex validation

    def test_limit_bounds(self, client):
        for i in range(5):
            _seed_skill(f"s{i}", confidence=0.1 * i)
        r = client.get("/api/plugins/echo_signals/skills?limit=3")
        assert len(r.json()["skills"]) == 3


# ---------------------------------------------------------------------------
# GET /skills/{skill_id}/timeline
# ---------------------------------------------------------------------------


class TestSkillTimeline:
    def test_returns_404_for_unknown_skill(self, client):
        r = client.get("/api/plugins/echo_signals/skills/ghost/timeline")
        assert r.status_code == 404
        assert "ghost" in r.json()["detail"]

    def test_includes_skill_summary_plus_events(self, client):
        _seed_skill("alpha", confidence=0.6)
        inv = _seed_invocation("alpha")
        _seed_event(inv, "alpha", "user_turn")
        _seed_event(inv, "alpha", "tool_call", value_text="bash")

        r = client.get("/api/plugins/echo_signals/skills/alpha/timeline")
        assert r.status_code == 200
        data = r.json()
        assert data["skill"]["skill_id"] == "alpha"
        assert data["skill"]["confidence"] == 0.6
        assert len(data["events"]) == 2

    def test_events_ordered_most_recent_first(self, client):
        _seed_skill("alpha")
        inv = _seed_invocation("alpha")
        # Older event first, then newer — DB insertion order. The API
        # must invert this for display.
        _seed_event(inv, "alpha", "user_turn", ts=100.0)
        _seed_event(inv, "alpha", "tool_call", ts=200.0)
        _seed_event(inv, "alpha", "session_ended", ts=300.0)

        r = client.get("/api/plugins/echo_signals/skills/alpha/timeline")
        types = [e["signal_type"] for e in r.json()["events"]]
        assert types == ["session_ended", "tool_call", "user_turn"]


# ---------------------------------------------------------------------------
# GET /status-distribution
# ---------------------------------------------------------------------------


class TestStatusDistribution:
    def test_normalizes_all_three_statuses_to_zero(self, client):
        """Empty DB still returns three buckets so the chart renders consistently."""
        r = client.get("/api/plugins/echo_signals/status-distribution")
        assert r.status_code == 200
        buckets = r.json()["distribution"]
        statuses = {b["status"]: b["count"] for b in buckets}
        assert statuses == {"active": 0, "pending_review": 0, "retired": 0}

    def test_counts_match(self, client):
        _seed_skill("a", status="active")
        _seed_skill("b", status="active")
        _seed_skill("c", status="active")
        _seed_skill("d", status="pending_review")
        _seed_skill("e", status="retired")
        r = client.get("/api/plugins/echo_signals/status-distribution")
        statuses = {b["status"]: b["count"] for b in r.json()["distribution"]}
        assert statuses == {"active": 3, "pending_review": 1, "retired": 1}

    def test_response_ordering_is_stable(self, client):
        _seed_skill("a", status="active")
        r1 = client.get("/api/plugins/echo_signals/status-distribution")
        r2 = client.get("/api/plugins/echo_signals/status-distribution")
        order = lambda r: [b["status"] for b in r.json()["distribution"]]
        assert order(r1) == order(r2) == ["active", "pending_review", "retired"]


# ---------------------------------------------------------------------------
# GET /invocations/recent
# ---------------------------------------------------------------------------


class TestRecentInvocations:
    def test_empty(self, client):
        r = client.get("/api/plugins/echo_signals/invocations/recent")
        assert r.json() == {"invocations": []}

    def test_includes_signal_count(self, client):
        _seed_skill("alpha")
        inv = _seed_invocation("alpha")
        _seed_event(inv, "alpha", "user_turn")
        _seed_event(inv, "alpha", "user_turn")
        _seed_event(inv, "alpha", "tool_call", value_text="bash")

        r = client.get("/api/plugins/echo_signals/invocations/recent")
        data = r.json()["invocations"]
        assert len(data) == 1
        assert data[0]["signal_count"] == 3

    def test_ordering_most_recent_first(self, client):
        _seed_skill("alpha")
        _seed_invocation("alpha", started_at=100.0)
        _seed_invocation("alpha", started_at=200.0)
        _seed_invocation("alpha", started_at=300.0)
        r = client.get("/api/plugins/echo_signals/invocations/recent")
        starts = [i["started_at"] for i in r.json()["invocations"]]
        assert starts == [300.0, 200.0, 100.0]


# ---------------------------------------------------------------------------
# POST /feedback
# ---------------------------------------------------------------------------


class TestFeedback:
    def test_positive_raises_confidence(self, client):
        _seed_skill("alpha", confidence=0.5)
        r = client.post(
            "/api/plugins/echo_signals/feedback",
            json={"skill_id": "alpha", "rating": 1},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["applied"] is True
        assert data["new_confidence"] == pytest.approx(0.6)
        assert data["event"] == "explicit_positive"

    def test_negative_lowers_confidence(self, client):
        _seed_skill("alpha", confidence=0.5)
        r = client.post(
            "/api/plugins/echo_signals/feedback",
            json={"skill_id": "alpha", "rating": -1},
        )
        data = r.json()
        assert data["applied"] is True
        assert data["new_confidence"] == pytest.approx(0.35)
        assert data["event"] == "explicit_negative"

    def test_invalid_rating_rejected(self, client):
        r = client.post(
            "/api/plugins/echo_signals/feedback",
            json={"skill_id": "alpha", "rating": 7},
        )
        assert r.status_code == 400

    def test_unknown_skill_returns_not_applied(self, client):
        # No seed — request a skill that isn't in the DB.
        echo_db.get_echo_conn()  # bootstrap tables
        r = client.post(
            "/api/plugins/echo_signals/feedback",
            json={"skill_id": "ghost", "rating": 1},
        )
        # We return 200 with applied=false (not 404) so the UI can
        # show "Echo doesn't know this skill yet" without an error toast.
        assert r.status_code == 200
        data = r.json()
        assert data["applied"] is False
        assert data["reason"] == "unknown_skill"

    def test_locked_skill_returns_not_applied(self, client):
        _seed_skill("alpha", confidence=0.5, locked=1)
        r = client.post(
            "/api/plugins/echo_signals/feedback",
            json={"skill_id": "alpha", "rating": 1},
        )
        data = r.json()
        assert data["applied"] is False
        assert data["reason"] == "locked"

    def test_missing_skill_id_rejected(self, client):
        r = client.post(
            "/api/plugins/echo_signals/feedback",
            json={"rating": 1},
        )
        assert r.status_code == 422

    def test_empty_skill_id_rejected(self, client):
        r = client.post(
            "/api/plugins/echo_signals/feedback",
            json={"skill_id": "", "rating": 1},
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# GET /preferences and DELETE /preferences/{id}
# ---------------------------------------------------------------------------


def _seed_pref(conn, *, task_request: str, agent_output: str, rating: int,
               skill_id=None, composite_score=None, created_at=None):
    import time as _t

    if created_at is None:
        created_at = _t.time()
    cur = conn.execute(
        "INSERT INTO echo_preference_example "
        "(task_request, task_embedding, agent_output, rating, skill_id, "
        " created_at, last_used_at, use_count, composite_score) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)",
        (task_request, b"\x00" * 1024, agent_output, rating, skill_id,
         created_at, None, composite_score),
    )
    conn.commit()
    return cur.lastrowid


class TestListPreferences:
    def test_empty_db(self, client):
        r = client.get("/api/plugins/echo_signals/preferences")
        assert r.status_code == 200
        assert r.json() == {"preferences": []}

    def test_returns_examples(self, client):
        conn = echo_db.get_echo_conn()
        eid = _seed_pref(
            conn,
            task_request="summarize this paper",
            agent_output="Summary…",
            rating=5,
            skill_id="research",
            composite_score=5.0,
        )
        r = client.get("/api/plugins/echo_signals/preferences")
        rows = r.json()["preferences"]
        assert len(rows) == 1
        assert rows[0]["example_id"] == eid
        assert rows[0]["task_request"] == "summarize this paper"
        assert rows[0]["rating"] == 5

    def test_sorted_by_composite_desc(self, client):
        conn = echo_db.get_echo_conn()
        _seed_pref(conn, task_request="low",  agent_output="x",
                   rating=4, composite_score=1.0)
        _seed_pref(conn, task_request="high", agent_output="x",
                   rating=5, composite_score=9.0)
        _seed_pref(conn, task_request="mid",  agent_output="x",
                   rating=4, composite_score=5.0)
        r = client.get("/api/plugins/echo_signals/preferences")
        tasks = [p["task_request"] for p in r.json()["preferences"]]
        assert tasks == ["high", "mid", "low"]

    def test_skill_filter(self, client):
        conn = echo_db.get_echo_conn()
        _seed_pref(conn, task_request="a", agent_output="x", rating=5,
                   skill_id="alpha", composite_score=5.0)
        _seed_pref(conn, task_request="b", agent_output="x", rating=5,
                   skill_id="beta", composite_score=5.0)
        r = client.get("/api/plugins/echo_signals/preferences?skill_id=alpha")
        tasks = [p["task_request"] for p in r.json()["preferences"]]
        assert tasks == ["a"]

    def test_min_rating_filter(self, client):
        conn = echo_db.get_echo_conn()
        _seed_pref(conn, task_request="low", agent_output="x", rating=3,
                   composite_score=3.0)
        _seed_pref(conn, task_request="high", agent_output="x", rating=5,
                   composite_score=5.0)
        r = client.get("/api/plugins/echo_signals/preferences?min_rating=5")
        tasks = [p["task_request"] for p in r.json()["preferences"]]
        assert tasks == ["high"]

    def test_limit(self, client):
        conn = echo_db.get_echo_conn()
        for i in range(5):
            _seed_pref(conn, task_request=f"t{i}", agent_output="x",
                       rating=5, composite_score=float(i))
        r = client.get("/api/plugins/echo_signals/preferences?limit=2")
        assert len(r.json()["preferences"]) == 2


class TestDeletePreference:
    def test_deletes_existing(self, client):
        conn = echo_db.get_echo_conn()
        eid = _seed_pref(conn, task_request="t", agent_output="x", rating=5)
        r = client.delete(f"/api/plugins/echo_signals/preferences/{eid}")
        assert r.status_code == 200
        assert r.json() == {"deleted": True, "example_id": eid}
        # Row gone.
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM echo_preference_example WHERE example_id=?",
            (eid,),
        ).fetchone()["n"]
        assert n == 0

    def test_delete_missing_returns_deleted_false(self, client):
        echo_db.get_echo_conn()  # bootstrap
        r = client.delete("/api/plugins/echo_signals/preferences/99999")
        assert r.status_code == 200
        assert r.json() == {"deleted": False, "example_id": 99999}


# ---------------------------------------------------------------------------
# POST /clipboard-signal (Tauri desktop shell)
# ---------------------------------------------------------------------------


def _seed_invocation_with_skill(conn, skill_id: str = "test-skill"):
    """Seed an invocation + its confidence anchor so clipboard signals attach."""
    import time as _time

    now = _time.time()
    conn.execute(
        "INSERT OR IGNORE INTO echo_skill_confidence "
        "(skill_id, created_at, updated_at) VALUES (?, ?, ?)",
        (skill_id, now, now),
    )
    cur = conn.execute(
        "INSERT INTO echo_skill_invocation "
        "(skill_id, session_id, platform, started_at) VALUES (?, ?, ?, ?)",
        (skill_id, "s-clip", "desktop", now),
    )
    conn.commit()
    return cur.lastrowid


class TestClipboardSignal:
    def test_records_when_invocation_exists(self, client):
        conn = echo_db.get_echo_conn()
        inv_id = _seed_invocation_with_skill(conn)

        r = client.post(
            "/api/plugins/echo_signals/clipboard-signal",
            json={
                "event_type": "clipboard_copy",
                "text": "Hello world",
                "text_length": 11,
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["recorded"] is True
        assert data["invocation_id"] == inv_id
        assert data["skill_id"] == "test-skill"

        row = conn.execute(
            "SELECT signal_type, value_int, value_text, layer "
            "FROM echo_signal_event WHERE skill_id = 'test-skill'"
        ).fetchone()
        assert row["signal_type"] == "clipboard_copy"
        assert row["value_int"] == 11
        assert row["value_text"] == "Hello world"
        assert row["layer"] == "A"

    def test_text_truncated_to_200_chars(self, client):
        conn = echo_db.get_echo_conn()
        _seed_invocation_with_skill(conn)

        long_text = "x" * 500
        r = client.post(
            "/api/plugins/echo_signals/clipboard-signal",
            json={"event_type": "clipboard_copy", "text": long_text},
        )
        assert r.status_code == 200

        row = conn.execute(
            "SELECT value_text, value_int FROM echo_signal_event "
            "WHERE signal_type = 'clipboard_copy'"
        ).fetchone()
        assert len(row["value_text"]) == 200  # value_text capped
        assert row["value_int"] == 500       # length preserved

    def test_no_invocation_drops_silently(self, client):
        echo_db.get_echo_conn()  # bootstrap tables
        r = client.post(
            "/api/plugins/echo_signals/clipboard-signal",
            json={"event_type": "clipboard_copy", "text": "x"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["recorded"] is False
        assert data["reason"] == "no_active_invocation"

    def test_window_focus_events_accepted(self, client):
        conn = echo_db.get_echo_conn()
        _seed_invocation_with_skill(conn)
        for et in ("window_focus", "window_blur"):
            r = client.post(
                "/api/plugins/echo_signals/clipboard-signal",
                json={"event_type": et},
            )
            assert r.status_code == 200
            assert r.json()["recorded"] is True
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM echo_signal_event "
            "WHERE signal_type IN ('window_focus', 'window_blur')"
        ).fetchone()["n"]
        assert n == 2

    def test_invalid_event_type_rejected(self, client):
        r = client.post(
            "/api/plugins/echo_signals/clipboard-signal",
            json={"event_type": "garbage"},
        )
        assert r.status_code == 422

    def test_oversized_text_rejected(self, client):
        r = client.post(
            "/api/plugins/echo_signals/clipboard-signal",
            json={"event_type": "clipboard_copy", "text": "x" * 9000},
        )
        assert r.status_code == 422  # max_length=8192

    def test_attributes_to_most_recent_invocation(self, client):
        """When two invocations exist, the signal attaches to the newer one."""
        conn = echo_db.get_echo_conn()
        import time as _time

        old_now = _time.time() - 1000
        conn.execute(
            "INSERT OR IGNORE INTO echo_skill_confidence "
            "(skill_id, created_at, updated_at) VALUES ('old', ?, ?)",
            (old_now, old_now),
        )
        conn.execute(
            "INSERT INTO echo_skill_invocation "
            "(skill_id, session_id, platform, started_at) "
            "VALUES ('old', 'sold', 'cli', ?)",
            (old_now,),
        )
        inv_new = _seed_invocation_with_skill(conn, "new")
        conn.commit()

        r = client.post(
            "/api/plugins/echo_signals/clipboard-signal",
            json={"event_type": "clipboard_copy", "text": "x"},
        )
        assert r.json()["invocation_id"] == inv_new
        assert r.json()["skill_id"] == "new"
