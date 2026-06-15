"""Tests for the LLM reason-scorer (Layer B+) and its confidence wiring.

Covers:
  * _parse_score   — tolerant JSON extraction + clamping + fail-to-0
  * score_reason   — aux-config gating, fail-soft, impl injection
  * score_reason_async — fire-and-forget + callback
  * confidence._apply_rule — positive rules now scale with severity
  * /feedback integration — a reason drives a graded same-direction step,
    and a reason that contradicts the click pulls confidence back
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins.echo_signals import confidence as conf
from plugins.echo_signals import db as echo_db
from plugins.echo_signals import reason_scorer as rscore
from plugins.echo_signals.reason_scorer import ReasonScore, _parse_score
from plugins.echo_signals.dashboard.plugin_api import router


# ---------------------------------------------------------------------------
# _parse_score
# ---------------------------------------------------------------------------


class TestParseScore:
    def test_plain_json(self):
        assert _parse_score('{"score": 4, "rationale": "clear praise"}').score == 4

    def test_negative(self):
        rs = _parse_score('{"score": -3}')
        assert rs.score == -3 and rs.rationale is None

    def test_code_fenced(self):
        rs = _parse_score('```json\n{"score": 2}\n```')
        assert rs.score == 2

    def test_prose_wrapped(self):
        rs = _parse_score('Sure! Here you go: {"score": 5} — hope that helps')
        assert rs.score == 5

    def test_clamped_above(self):
        assert _parse_score('{"score": 99}').score == 5

    def test_clamped_below(self):
        assert _parse_score('{"score": -42}').score == -5

    def test_float_rounded(self):
        assert _parse_score('{"score": 2.6}').score == 3

    def test_garbage_is_zero(self):
        assert _parse_score("not json at all").score == 0

    def test_non_numeric_score_is_zero(self):
        assert _parse_score('{"score": "great"}').score == 0

    def test_non_string_rationale_dropped(self):
        rs = _parse_score('{"score": 1, "rationale": 7}')
        assert rs.score == 1 and rs.rationale is None


# ---------------------------------------------------------------------------
# score_reason — gating + fail-soft + injection
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_impl():
    rscore.reset_reason_score_impl()
    yield
    rscore.reset_reason_score_impl()


class TestScoreReason:
    def test_empty_reason_is_zero_no_impl_call(self, monkeypatch):
        called = []
        rscore.set_reason_score_impl(lambda *a: called.append(a) or ReasonScore(5))
        assert rscore.score_reason("up", "skill", "   ").score == 0
        assert called == []  # never reached the impl

    def test_disabled_channel_is_zero_no_impl_call(self, monkeypatch):
        called = []
        rscore.set_reason_score_impl(lambda *a: called.append(a) or ReasonScore(5))
        monkeypatch.setattr(
            "plugins.echo_signals.aux_config.reason_scorer_enabled", lambda: False
        )
        assert rscore.score_reason("up", "skill", "great job").score == 0
        assert called == []  # gated off → no LLM call

    def test_injected_impl_used(self, monkeypatch):
        monkeypatch.setattr(
            "plugins.echo_signals.aux_config.reason_scorer_enabled", lambda: True
        )
        rscore.set_reason_score_impl(lambda d, s, r: ReasonScore(score=-4, rationale="bad"))
        rs = rscore.score_reason("down", "skill", "this was wrong")
        assert rs.score == -4 and rs.rationale == "bad"

    def test_impl_raising_is_zero(self, monkeypatch):
        monkeypatch.setattr(
            "plugins.echo_signals.aux_config.reason_scorer_enabled", lambda: True
        )

        def _boom(*_a):
            raise RuntimeError("llm down")

        rscore.set_reason_score_impl(_boom)
        assert rscore.score_reason("up", "skill", "great").score == 0

    def test_prompt_receives_direction_and_skill(self, monkeypatch):
        monkeypatch.setattr(
            "plugins.echo_signals.aux_config.reason_scorer_enabled", lambda: True
        )
        seen = {}

        def _impl(direction, skill_id, reason):
            seen.update(direction=direction, skill_id=skill_id, reason=reason)
            return ReasonScore(3)

        rscore.set_reason_score_impl(_impl)
        rscore.score_reason("down", "my-skill", "meh")
        assert seen == {"direction": "down", "skill_id": "my-skill", "reason": "meh"}


class TestScoreReasonAsync:
    def test_fires_and_calls_back(self, monkeypatch, real_reason_scorer):
        monkeypatch.setattr(
            "plugins.echo_signals.aux_config.reason_scorer_enabled", lambda: True
        )
        rscore.set_reason_score_impl(lambda d, s, r: ReasonScore(score=2))
        out = []
        t = rscore.score_reason_async("up", "skill", "nice", out.append)
        assert t is not None
        t.join(timeout=5)
        assert len(out) == 1 and out[0].score == 2

    def test_empty_reason_no_thread(self):
        assert rscore.score_reason_async("up", "skill", "", lambda r: None) is None


# ---------------------------------------------------------------------------
# confidence — positive rules scale with severity now
# ---------------------------------------------------------------------------


class TestPositiveSeverity:
    def test_explicit_positive_full_severity_unchanged(self):
        # severity 1.0 (default callers) → α exactly, no behaviour change.
        c0 = 0.5
        assert conf._apply_rule(c0, "explicit_positive", severity=1.0) == pytest.approx(
            min(c0 + conf.ALPHA_EXPLICIT_POSITIVE, 1.0)
        )

    def test_explicit_positive_half_severity(self):
        c0 = 0.5
        got = conf._apply_rule(c0, "explicit_positive", severity=0.5)
        assert got == pytest.approx(c0 + conf.ALPHA_EXPLICIT_POSITIVE * 0.5)

    def test_explicit_positive_zero_severity_is_noop(self):
        assert conf._apply_rule(0.5, "explicit_positive", severity=0.0) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# /feedback integration — reason drives a graded step
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    fake_db = tmp_path / "state.db"
    import hermes_state

    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", fake_db)
    echo_db.reset_for_tests()
    app = FastAPI()
    app.include_router(router, prefix="/api/plugins/echo_signals")
    yield TestClient(app)
    echo_db.reset_for_tests()


def _seed(skill_id="s", confidence=0.5):
    conn = echo_db.get_echo_conn()
    now = time.time()
    conn.execute(
        "INSERT INTO echo_skill_confidence "
        "(skill_id, confidence, status, locked, n_invocations, n_signals, "
        " created_at, updated_at) VALUES (?, ?, 'active', 0, 0, 0, ?, ?)",
        (skill_id, confidence, now, now),
    )
    cur = conn.execute(
        "INSERT INTO echo_skill_invocation (skill_id, session_id, platform, started_at) "
        "VALUES (?, 's1', 'cli', ?)",
        (skill_id, now),
    )
    conn.commit()
    return cur.lastrowid


def _patch_sync_scorer(monkeypatch, score, rationale=None):
    """Run the reason scorer synchronously with a fixed score so the test can
    assert the post-callback DB state deterministically."""
    monkeypatch.setattr(
        "plugins.echo_signals.aux_config.reason_scorer_enabled", lambda: True
    )
    rscore.set_reason_score_impl(lambda d, s, r: ReasonScore(score=score, rationale=rationale))

    def _sync(direction, skill_id, reason, on_result):
        on_result(rscore.score_reason(direction, skill_id, reason))
        return None

    # Patch the name the dashboard imports at call time.
    monkeypatch.setattr(rscore, "score_reason_async", _sync)


class TestFeedbackReasonIntegration:
    def test_aligned_reason_adds_graded_bonus(self, client, monkeypatch, real_reason_scorer):
        inv = _seed("s", confidence=0.5)
        _patch_sync_scorer(monkeypatch, score=5, rationale="strong praise")
        r = client.post(
            "/api/plugins/echo_signals/feedback",
            json={"skill_id": "s", "rating": 1, "reason": "amazing", "invocation_id": inv},
        )
        assert r.status_code == 200
        conn = echo_db.get_echo_conn()
        c = conn.execute(
            "SELECT confidence FROM echo_skill_confidence WHERE skill_id='s'"
        ).fetchone()["confidence"]
        # base 0.5 + α(0.10) = 0.60, then reason +5 → severity 1.0 → +α(0.10) = 0.70
        assert c == pytest.approx(0.70, abs=1e-6)
        n = conn.execute(
            "SELECT value_real FROM echo_signal_event WHERE signal_type='reason_score'"
        ).fetchone()
        assert n is not None and n["value_real"] == pytest.approx(5.0)

    def test_contradicting_reason_pulls_back(self, client, monkeypatch, real_reason_scorer):
        inv = _seed("s", confidence=0.5)
        # User clicked 👍 but the LLM scores the words as criticism (-5).
        _patch_sync_scorer(monkeypatch, score=-5)
        client.post(
            "/api/plugins/echo_signals/feedback",
            json={"skill_id": "s", "rating": 1, "reason": "actually the layout is broken", "invocation_id": inv},
        )
        conn = echo_db.get_echo_conn()
        c = conn.execute(
            "SELECT confidence FROM echo_skill_confidence WHERE skill_id='s'"
        ).fetchone()["confidence"]
        # base 👍: 0.5 + 0.10 = 0.60, then reason -5 → explicit_negative sev 1.0
        # → 0.60 * (1 - 0.30) = 0.42 (pulled back below where the bare 👍 left it)
        assert c == pytest.approx(0.42, abs=1e-6)

    def test_zero_score_leaves_base_only(self, client, monkeypatch, real_reason_scorer):
        inv = _seed("s", confidence=0.5)
        _patch_sync_scorer(monkeypatch, score=0)
        client.post(
            "/api/plugins/echo_signals/feedback",
            json={"skill_id": "s", "rating": 1, "reason": "do it again differently", "invocation_id": inv},
        )
        conn = echo_db.get_echo_conn()
        c = conn.execute(
            "SELECT confidence FROM echo_skill_confidence WHERE skill_id='s'"
        ).fetchone()["confidence"]
        # base 👍 only: 0.5 + 0.10 = 0.60 (score 0 → no extra move)
        assert c == pytest.approx(0.60, abs=1e-6)

    def test_no_reason_does_not_score(self, client, monkeypatch, real_reason_scorer):
        inv = _seed("s", confidence=0.5)
        called = []
        monkeypatch.setattr(
            rscore, "score_reason_async",
            lambda *a, **k: called.append(a) or None,
        )
        client.post(
            "/api/plugins/echo_signals/feedback",
            json={"skill_id": "s", "rating": 1, "invocation_id": inv},
        )
        assert called == []  # no reason → scorer never invoked
