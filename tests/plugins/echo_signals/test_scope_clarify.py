"""Unit tests for M2 scope confirmation via in-conversation clarify."""

from __future__ import annotations

import json
import time

import pytest

from plugins.echo_signals import db as echo_db
from plugins.echo_signals import scope_clarify as scl


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    fake_db = tmp_path / "state.db"
    import hermes_state

    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", fake_db)
    echo_db.reset_for_tests()
    yield fake_db
    echo_db.reset_for_tests()


@pytest.fixture(autouse=True)
def _reset_impl():
    scl.reset_options_impl()
    yield
    scl.reset_options_impl()


def _seed_scope(skill_id, session_id, state="pending", options=None):
    conn = echo_db.get_echo_conn()
    now = time.time()
    conn.execute(
        "INSERT INTO echo_skill_confidence (skill_id, created_at, updated_at) "
        "VALUES (?, ?, ?)",
        (skill_id, now, now),
    )
    conn.execute(
        "INSERT INTO echo_skill_scope "
        "(skill_id, scope_level, created_at, updated_at, session_id, "
        " scope_options, scope_state) VALUES (?, 'unknown', ?, ?, ?, ?, ?)",
        (skill_id, now, now, session_id,
         json.dumps(options) if options else None, state),
    )
    conn.commit()


def _scope_row(skill_id):
    return echo_db.get_echo_conn().execute(
        "SELECT * FROM echo_skill_scope WHERE skill_id = ?", (skill_id,)
    ).fetchone()


# ---------------------------------------------------------------------------
# _parse_options
# ---------------------------------------------------------------------------


class TestParseOptions:
    def test_clean(self):
        text = ('{"options": [{"label": "只写中文演讲稿", "breadth": "narrow"}, '
                '{"label": "各类中文文稿", "breadth": "broad"}]}')
        opts = scl._parse_options(text)
        assert [o.label for o in opts] == ["只写中文演讲稿", "各类中文文稿"]
        assert opts[0].breadth == "narrow"

    def test_code_fence(self):
        text = '```json\n{"options": [{"label": "a"}, {"label": "b"}]}\n```'
        opts = scl._parse_options(text)
        assert len(opts) == 2
        assert opts[0].breadth == "medium"  # default when missing/invalid

    def test_caps_at_four(self):
        items = ",".join('{"label": "o%d"}' % i for i in range(6))
        opts = scl._parse_options('{"options": [%s]}' % items)
        assert len(opts) == scl.MAX_OPTIONS

    def test_garbage(self):
        assert scl._parse_options("not json") == []
        assert scl._parse_options('{"nope": 1}') == []


# ---------------------------------------------------------------------------
# generate_and_store
# ---------------------------------------------------------------------------


class TestGenerateAndStore:
    def test_stores_options_and_flips_state(self, isolated_db, monkeypatch):
        _seed_scope("speech", "sess-1", state="pending")
        monkeypatch.setattr(scl, "_read_skill",
                            lambda sid: ("speech", "desc", "body"))
        monkeypatch.setattr("plugins.echo_signals.aux_config.aux_enabled_for",
                            lambda task: True)
        scl.set_options_impl(lambda n, d, b: [
            scl.ScopeOption("只写中文演讲稿", "narrow"),
            scl.ScopeOption("各类中文文稿", "broad"),
        ])
        n = scl.generate_and_store("speech")
        assert n == 2
        row = _scope_row("speech")
        assert row["scope_state"] == "options_ready"
        assert "演讲稿" in row["scope_options"]

    def test_too_few_options_stays_pending(self, isolated_db, monkeypatch):
        _seed_scope("speech", "sess-1", state="pending")
        monkeypatch.setattr(scl, "_read_skill",
                            lambda sid: ("speech", "desc", "body"))
        monkeypatch.setattr("plugins.echo_signals.aux_config.aux_enabled_for",
                            lambda task: True)
        scl.set_options_impl(lambda n, d, b: [scl.ScopeOption("only one", "narrow")])
        assert scl.generate_and_store("speech") == 0
        assert _scope_row("speech")["scope_state"] == "pending"

    def test_aux_disabled_no_options(self, isolated_db, monkeypatch):
        _seed_scope("speech", "sess-1")
        monkeypatch.setattr("plugins.echo_signals.aux_config.aux_enabled_for",
                            lambda task: False)
        assert scl.generate_scope_options("speech") == []


# ---------------------------------------------------------------------------
# consume_scope_nudge
# ---------------------------------------------------------------------------


class TestConsumeScopeNudge:
    OPTS = [{"label": "只写中文演讲稿", "breadth": "narrow"},
            {"label": "各类中文文稿", "breadth": "broad"}]

    def test_options_ready_returns_nudge(self, isolated_db):
        _seed_scope("speech", "s1", state="options_ready", options=self.OPTS)
        text = scl.consume_scope_nudge("s1")
        assert text and "clarify" in text
        assert "只写中文演讲稿" in text
        # state advanced to asked → second call no nudge
        assert scl.consume_scope_nudge("s1") is None
        assert _scope_row("speech")["scope_state"] == "asked"

    def test_pending_state_no_nudge(self, isolated_db):
        _seed_scope("speech", "s1", state="pending")
        assert scl.consume_scope_nudge("s1") is None

    def test_other_session_no_nudge(self, isolated_db):
        _seed_scope("speech", "s1", state="options_ready", options=self.OPTS)
        assert scl.consume_scope_nudge("different") is None

    def test_none_session(self, isolated_db):
        assert scl.consume_scope_nudge(None) is None


# ---------------------------------------------------------------------------
# capture_scope_from_history
# ---------------------------------------------------------------------------


def _clarify_msg(question, choices, response):
    return {
        "role": "tool",
        "content": json.dumps({
            "question": question,
            "choices_offered": choices,
            "user_response": response,
        }),
    }


class TestCaptureScope:
    OPTS = [{"label": "只写中文演讲稿", "breadth": "narrow"},
            {"label": "各类中文文稿", "breadth": "broad"}]

    def test_exact_choice_captured(self, isolated_db):
        _seed_scope("speech", "s1", state="asked", options=self.OPTS)
        history = [
            {"role": "user", "content": "..."},
            _clarify_msg("适用范围?", ["只写中文演讲稿", "各类中文文稿"], "各类中文文稿"),
        ]
        assert scl.capture_scope_from_history("s1", history) is True
        row = _scope_row("speech")
        assert row["scope_choice"] == "各类中文文稿"
        assert row["scope_state"] == "confirmed"
        assert row["scope_level"] == "broad"

    def test_no_clarify_no_capture(self, isolated_db):
        _seed_scope("speech", "s1", state="asked", options=self.OPTS)
        assert scl.capture_scope_from_history("s1", [{"role": "user", "content": "hi"}]) is False
        assert _scope_row("speech")["scope_state"] == "asked"

    def test_not_in_asked_state_ignored(self, isolated_db):
        _seed_scope("speech", "s1", state="options_ready", options=self.OPTS)
        history = [_clarify_msg("q", ["只写中文演讲稿"], "只写中文演讲稿")]
        assert scl.capture_scope_from_history("s1", history) is False

    def test_freetext_other_response_stored_raw(self, isolated_db):
        _seed_scope("speech", "s1", state="asked", options=self.OPTS)
        # User typed a custom "Other" answer not among the options.
        history = [_clarify_msg("q", ["只写中文演讲稿", "各类中文文稿"], "只用于毕业致辞")]
        assert scl.capture_scope_from_history("s1", history) is True
        assert _scope_row("speech")["scope_choice"] == "只用于毕业致辞"
        assert _scope_row("speech")["scope_level"] == "unknown"

    def test_prefixed_response_matched(self, isolated_db):
        _seed_scope("speech", "s1", state="asked", options=self.OPTS)
        history = [_clarify_msg("q", ["只写中文演讲稿", "各类中文文稿"], "A · 各类中文文稿")]
        assert scl.capture_scope_from_history("s1", history) is True
        assert _scope_row("speech")["scope_choice"] == "各类中文文稿"

    def test_idempotent_after_confirm(self, isolated_db):
        _seed_scope("speech", "s1", state="asked", options=self.OPTS)
        history = [_clarify_msg("q", ["各类中文文稿"], "各类中文文稿")]
        assert scl.capture_scope_from_history("s1", history) is True
        # second call: already confirmed → no-op
        assert scl.capture_scope_from_history("s1", history) is False
