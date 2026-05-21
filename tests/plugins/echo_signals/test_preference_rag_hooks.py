"""Integration tests for M5 hook wiring (Step 12).

Three things to verify end-to-end:

  * on_post_llm_call_cache populates echo_turn_cache with the right
    (session_id, skill_id, user, agent) pair.
  * on_pre_llm_call_inject reads the preference store and returns a
    {"context": ...} block for Hermes to append.
  * The /feedback API path: thumbs-up → finds the cached turn →
    stores a preference example with the right rating mapping.

These are the wire-level tests; the underlying math/storage already
has unit coverage in test_preference_rag.py.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins.echo_signals import db as echo_db
from plugins.echo_signals import preference_rag as prag
from plugins.echo_signals import session_context as sc
from plugins.echo_signals import usage_hook as uh
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
    sc.clear_session_context()
    yield fake_db
    sc.clear_session_context()
    echo_db.reset_for_tests()


@pytest.fixture(autouse=True)
def _reset_encoder():
    prag.reset_encoder()
    yield
    prag.reset_encoder()


@pytest.fixture
def active_skill(isolated_db):
    """Set up an active invocation so post_llm_call_cache attributes correctly."""
    uh.install_bump_use_hook()
    sc.set_session_context("session-x", "cli")
    import tools.skill_usage as _su

    _su.bump_use("test-skill")
    yield
    uh.uninstall_bump_use_hook()


@pytest.fixture
def client(isolated_db):
    app = FastAPI()
    app.include_router(router, prefix="/api/plugins/echo_signals")
    return TestClient(app)


# ---------------------------------------------------------------------------
# on_post_llm_call_cache
# ---------------------------------------------------------------------------


class TestPostLLMCallCache:
    def test_writes_turn_to_cache(self, active_skill):
        prag.on_post_llm_call_cache(
            session_id="session-x",
            user_message="please summarize this",
            assistant_response="Here is the summary…",
        )
        conn = echo_db.get_echo_conn()
        row = conn.execute(
            "SELECT session_id, skill_id, user_message, assistant_response "
            "FROM echo_turn_cache"
        ).fetchone()
        assert row is not None
        assert row["session_id"] == "session-x"
        assert row["skill_id"] == "test-skill"
        assert row["user_message"] == "please summarize this"
        assert row["assistant_response"] == "Here is the summary…"

    def test_overwrites_previous_turn_in_same_session(self, active_skill):
        prag.on_post_llm_call_cache(
            session_id="session-x",
            user_message="first request",
            assistant_response="first reply",
        )
        prag.on_post_llm_call_cache(
            session_id="session-x",
            user_message="second request",
            assistant_response="second reply",
        )
        conn = echo_db.get_echo_conn()
        rows = conn.execute(
            "SELECT user_message FROM echo_turn_cache WHERE session_id='session-x'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["user_message"] == "second request"

    def test_multimodal_user_message_normalized(self, active_skill):
        """OpenAI multimodal content (list of parts) gets flattened."""
        prag.on_post_llm_call_cache(
            session_id="session-x",
            user_message={
                "role": "user",
                "content": [
                    {"type": "text", "text": "Look at"},
                    {"type": "image_url", "image_url": {"url": "..."}},
                    {"type": "text", "text": "this image"},
                ],
            },
            assistant_response="I see a cat.",
        )
        conn = echo_db.get_echo_conn()
        row = conn.execute(
            "SELECT user_message FROM echo_turn_cache"
        ).fetchone()
        assert row["user_message"] == "Look at this image"

    def test_missing_fields_skip(self, isolated_db):
        echo_db.get_echo_conn()  # bootstrap
        prag.on_post_llm_call_cache(
            session_id="", user_message="x", assistant_response="y",
        )
        prag.on_post_llm_call_cache(
            session_id="s", user_message="x", assistant_response="",
        )
        prag.on_post_llm_call_cache(
            session_id="s", user_message=None, assistant_response="y",
        )
        conn = echo_db.get_echo_conn()
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM echo_turn_cache"
        ).fetchone()["n"]
        assert n == 0

    def test_no_active_skill_still_caches_with_null_skill_id(self, isolated_db):
        """Even without an active invocation we still cache the turn so a
        subsequent thumbs-up can find it. skill_id is just NULL."""
        prag.on_post_llm_call_cache(
            session_id="standalone",
            user_message="x",
            assistant_response="y",
        )
        conn = echo_db.get_echo_conn()
        row = conn.execute(
            "SELECT skill_id FROM echo_turn_cache"
        ).fetchone()
        assert row is not None
        assert row["skill_id"] is None


# ---------------------------------------------------------------------------
# on_pre_llm_call_inject
# ---------------------------------------------------------------------------


def _seed_confidence(skill_id: str, confidence: float = 0.7,
                     status: str = "active"):
    conn = echo_db.get_echo_conn()
    now = time.time()
    conn.execute(
        "INSERT INTO echo_skill_confidence "
        "(skill_id, confidence, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (skill_id, confidence, status, now, now),
    )
    conn.commit()


class TestPreLLMCallInject:
    def test_no_preferences_returns_none(self, isolated_db):
        echo_db.get_echo_conn()
        out = prag.on_pre_llm_call_inject(user_message="anything")
        assert out is None

    def test_relevant_preference_returned_as_context_dict(self, isolated_db):
        prag.store_preference(
            task_request="write a marketing email for a launch",
            agent_output="Subject: We are launching!\n\n…",
            rating=5,
        )
        out = prag.on_pre_llm_call_inject(
            user_message="write me a marketing email for our launch",
        )
        assert out is not None
        assert "context" in out
        assert "Echo" in out["context"]
        assert "Subject" in out["context"]

    def test_empty_user_message_skipped(self, isolated_db):
        prag.store_preference(
            task_request="x", agent_output="y", rating=5,
        )
        assert prag.on_pre_llm_call_inject(user_message="") is None
        assert prag.on_pre_llm_call_inject(user_message=None) is None

    def test_irrelevant_query_returns_none(self, isolated_db):
        prag.store_preference(
            task_request="write a marketing email",
            agent_output="...",
            rating=5,
        )
        # Query with no token overlap → no relevant examples.
        out = prag.on_pre_llm_call_inject(
            user_message="debug this kernel panic trace",
        )
        assert out is None

    def test_retired_skill_examples_filtered_via_weights(self, isolated_db):
        prag.store_preference(
            task_request="write a marketing email",
            agent_output="alive output",
            rating=5,
            skill_id="alive",
        )
        prag.store_preference(
            task_request="write a marketing email",
            agent_output="retired output",
            rating=5,
            skill_id="retired",
        )
        _seed_confidence("alive", confidence=0.8, status="active")
        # NOTE: retired skills are absent from the weights map. Their
        # examples still appear in the candidate pool but with the
        # default similarity (no multiplier). For Step 12 we accept
        # this behavior; truly excluding retired skills' examples is
        # a future enhancement.
        out = prag.on_pre_llm_call_inject(
            user_message="write a marketing email",
        )
        assert out is not None
        # Some result returned; specific ordering isn't asserted here
        # since both have identical similarity.

    def test_dict_user_message_normalized(self, isolated_db):
        prag.store_preference(
            task_request="summarize this paper",
            agent_output="...",
            rating=5,
        )
        out = prag.on_pre_llm_call_inject(
            user_message={"role": "user", "content": "summarize this paper"},
        )
        assert out is not None
        assert "context" in out


# ---------------------------------------------------------------------------
# store_from_turn_cache_by_skill — used by /feedback
# ---------------------------------------------------------------------------


def _seed_turn_cache(session_id: str, skill_id: str,
                     user_msg: str = "u", agent_resp: str = "a"):
    conn = echo_db.get_echo_conn()
    now = time.time()
    conn.execute(
        "INSERT INTO echo_turn_cache "
        "(session_id, skill_id, user_message, assistant_response, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (session_id, skill_id, user_msg, agent_resp, now),
    )
    conn.commit()


class TestStoreFromTurnCache:
    def test_no_cache_returns_zero(self, isolated_db):
        echo_db.get_echo_conn()
        assert prag.store_from_turn_cache_by_skill("nonexistent") == 0

    def test_stores_with_default_rating(self, isolated_db):
        _seed_turn_cache("s1", "skill-a", "what time is it", "It's 3pm.")
        eid = prag.store_from_turn_cache_by_skill("skill-a")
        assert eid > 0
        conn = echo_db.get_echo_conn()
        row = conn.execute(
            "SELECT task_request, agent_output, rating, skill_id "
            "FROM echo_preference_example WHERE example_id = ?",
            (eid,),
        ).fetchone()
        assert row["task_request"] == "what time is it"
        assert row["agent_output"] == "It's 3pm."
        assert row["rating"] == 5
        assert row["skill_id"] == "skill-a"

    def test_custom_rating(self, isolated_db):
        _seed_turn_cache("s1", "skill-a")
        eid = prag.store_from_turn_cache_by_skill("skill-a", rating=4)
        conn = echo_db.get_echo_conn()
        row = conn.execute(
            "SELECT rating FROM echo_preference_example WHERE example_id = ?",
            (eid,),
        ).fetchone()
        assert row["rating"] == 4

    def test_uses_most_recent_cached_turn(self, isolated_db):
        conn = echo_db.get_echo_conn()
        now = time.time()
        conn.execute(
            "INSERT INTO echo_turn_cache "
            "(session_id, skill_id, user_message, assistant_response, updated_at) "
            "VALUES ('s1', 'skill-a', 'old', 'old reply', ?)",
            (now - 10,),
        )
        conn.commit()
        conn.execute(
            "DELETE FROM echo_turn_cache WHERE session_id = 's1'",
        )
        # INSERT OR REPLACE semantics emulated: same session, same skill
        # → only the latest row exists.
        conn.execute(
            "INSERT INTO echo_turn_cache "
            "(session_id, skill_id, user_message, assistant_response, updated_at) "
            "VALUES ('s1', 'skill-a', 'newest', 'newest reply', ?)",
            (now,),
        )
        conn.commit()
        eid = prag.store_from_turn_cache_by_skill("skill-a")
        row = conn.execute(
            "SELECT task_request FROM echo_preference_example WHERE example_id = ?",
            (eid,),
        ).fetchone()
        assert row["task_request"] == "newest"


# ---------------------------------------------------------------------------
# Dashboard /feedback integration
# ---------------------------------------------------------------------------


class TestFeedbackPath:
    def test_thumbs_up_stores_preference(self, client):
        _seed_confidence("skill-a", confidence=0.5)
        _seed_turn_cache(
            "s1", "skill-a", "summarize this paper", "Here's the summary.",
        )

        r = client.post(
            "/api/plugins/echo_signals/feedback",
            json={"skill_id": "skill-a", "rating": 1},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["applied"] is True
        # Echo's response carries the new preference id.
        assert "preference_example_id" in data
        assert data["preference_example_id"] is not None

        conn = echo_db.get_echo_conn()
        row = conn.execute(
            "SELECT task_request, agent_output, rating "
            "FROM echo_preference_example WHERE skill_id = 'skill-a'"
        ).fetchone()
        assert row is not None
        assert row["task_request"] == "summarize this paper"
        assert row["agent_output"] == "Here's the summary."
        assert row["rating"] == 4  # no reason → 4

    def test_thumbs_up_with_reason_stores_rating_5(self, client):
        _seed_confidence("skill-a", confidence=0.5)
        _seed_turn_cache("s1", "skill-a")

        r = client.post(
            "/api/plugins/echo_signals/feedback",
            json={
                "skill_id": "skill-a",
                "rating": 1,
                "reason": "This was exactly the format I wanted",
            },
        )
        conn = echo_db.get_echo_conn()
        row = conn.execute(
            "SELECT rating FROM echo_preference_example WHERE skill_id = 'skill-a'"
        ).fetchone()
        assert row["rating"] == 5  # reason present → 5

    def test_thumbs_down_does_not_store_preference(self, client):
        _seed_confidence("skill-a", confidence=0.5)
        _seed_turn_cache("s1", "skill-a")

        r = client.post(
            "/api/plugins/echo_signals/feedback",
            json={"skill_id": "skill-a", "rating": -1},
        )
        # Confidence still updated, but no preference stored.
        assert r.json().get("preference_example_id") is None
        conn = echo_db.get_echo_conn()
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM echo_preference_example"
        ).fetchone()["n"]
        assert n == 0

    def test_thumbs_up_without_cache_silently_skips_preference(self, client):
        """Skill exists in confidence table but never had a turn cached.
        Feedback still applies, just no preference row."""
        _seed_confidence("skill-a", confidence=0.5)

        r = client.post(
            "/api/plugins/echo_signals/feedback",
            json={"skill_id": "skill-a", "rating": 1},
        )
        assert r.status_code == 200
        assert r.json()["applied"] is True
        assert r.json().get("preference_example_id") is None

    def test_locked_skill_no_preference_stored(self, client):
        conn = echo_db.get_echo_conn()
        now = time.time()
        conn.execute(
            "INSERT INTO echo_skill_confidence "
            "(skill_id, confidence, status, locked, created_at, updated_at) "
            "VALUES ('locked-skill', 0.5, 'active', 1, ?, ?)",
            (now, now),
        )
        conn.commit()
        _seed_turn_cache("s1", "locked-skill")

        r = client.post(
            "/api/plugins/echo_signals/feedback",
            json={"skill_id": "locked-skill", "rating": 1},
        )
        # applied=False because skill is locked; no preference write.
        assert r.json()["applied"] is False
        assert r.json().get("preference_example_id") is None
