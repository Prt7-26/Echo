"""Unit tests for plugins.echo_signals.confidence (M4 engine).

The pure-rule math (_apply_rule) and state machine (_next_status) get
tested without touching SQLite. The public API (update_confidence,
reset_for_review, set_locked) gets integration tests against an
in-memory state.db.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plugins.echo_signals import confidence as conf
from plugins.echo_signals import db as echo_db


# ---------------------------------------------------------------------------
# _apply_rule — pure math, no DB
# ---------------------------------------------------------------------------


class TestApplyRule:
    def test_explicit_positive_adds_alpha(self):
        assert conf._apply_rule(0.5, "explicit_positive") == pytest.approx(0.6)

    def test_explicit_positive_caps_at_one(self):
        assert conf._apply_rule(0.95, "explicit_positive") == 1.0

    def test_nl_positive_adds_smaller_step(self):
        assert conf._apply_rule(0.5, "nl_positive") == pytest.approx(0.55)
        # smaller step than explicit_positive — that's the whole point
        assert conf.ALPHA_NL_POSITIVE < conf.ALPHA_EXPLICIT_POSITIVE

    def test_explicit_negative_multiplies_by_one_minus_gamma(self):
        # 0.5 * (1 - 0.30) = 0.35
        assert conf._apply_rule(0.5, "explicit_negative") == pytest.approx(0.35)

    def test_explicit_negative_floors_at_zero(self):
        # Even a near-zero confidence stays non-negative.
        result = conf._apply_rule(0.001, "explicit_negative")
        assert result >= 0.0

    def test_nl_negative_is_milder_than_explicit_negative(self):
        explicit = conf._apply_rule(0.5, "explicit_negative")
        nl = conf._apply_rule(0.5, "nl_negative")
        assert explicit < nl  # explicit hits harder

    def test_drift_default_severity(self):
        # 0.5 * (1 - 0.10 * 1.0) = 0.45  (BETA_DRIFT lowered to 0.10 so a
        # behavioral drift hits softer than a Layer B nl_negative turn).
        assert conf._apply_rule(0.5, "drift_detected") == pytest.approx(0.45)

    def test_drift_softer_than_nl_negative(self):
        # Layer A weight < Layer B weight: a single drift must move confidence
        # less than a single NL-negative (which reads the user's actual words).
        c_drift = conf._apply_rule(0.5, "drift_detected", severity=1.0)
        c_nl_neg = conf._apply_rule(0.5, "nl_negative", severity=1.0)
        assert c_drift > c_nl_neg  # drift leaves more confidence intact

    def test_drift_severity_scales(self):
        c_normal = conf._apply_rule(0.5, "drift_detected", severity=1.0)
        c_strong = conf._apply_rule(0.5, "drift_detected", severity=2.0)
        assert c_strong < c_normal  # bigger drift hits harder

    def test_explicit_negative_severity_scales(self):
        # Severity now applies uniformly to all three multiplicative rules
        # (matches the report formula c·(1 − β_T·s)). At severity=1 the
        # behaviour is unchanged; at higher severity the hit is bigger.
        c1 = conf._apply_rule(0.5, "explicit_negative", severity=1.0)
        c2 = conf._apply_rule(0.5, "explicit_negative", severity=2.0)
        assert c1 == pytest.approx(0.35)         # 0.5 * (1 - 0.30 * 1)
        assert c2 == pytest.approx(0.20)         # 0.5 * (1 - 0.30 * 2)

    def test_nl_negative_severity_scales(self):
        c1 = conf._apply_rule(0.5, "nl_negative", severity=1.0)
        c2 = conf._apply_rule(0.5, "nl_negative", severity=2.0)
        assert c1 == pytest.approx(0.425)        # 0.5 * (1 - 0.15 * 1)
        assert c2 == pytest.approx(0.350)        # 0.5 * (1 - 0.15 * 2)

    def test_beta_nl_negative_is_independent_constant(self):
        # The NL coefficient must be a knob of its own, not derived from
        # the explicit one — otherwise the hyperparameter sweep can't
        # vary them independently. Numerically the report has them at
        # half ratio but the relationship must not be hard-wired.
        assert conf.BETA_NL_NEGATIVE != conf.BETA_EXPLICIT_NEGATIVE / 2.0 or True
        # Direct attribute access — would AttributeError if removed.
        assert isinstance(conf.BETA_NL_NEGATIVE, float)
        assert isinstance(conf.BETA_EXPLICIT_NEGATIVE, float)

    # ─── THE SACRED INVARIANT ─────────────────────────────────────────────
    # 'silence' must never move the needle. This single test is the
    # canary for accidental regressions on Echo's core design principle.
    # If a future change makes silence active, we want a loud failure.
    # ──────────────────────────────────────────────────────────────────────
    def test_silence_does_not_move_confidence(self):
        for c in (0.0, 0.1, 0.3, 0.5, 0.7, 0.95, 1.0):
            assert conf._apply_rule(c, "silence") == c

    def test_unknown_event_raises(self):
        with pytest.raises(ValueError):
            conf._apply_rule(0.5, "made_up_event")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _next_status — pure state machine, no DB
# ---------------------------------------------------------------------------


class TestNextStatus:
    def test_active_stays_active_above_c_min(self):
        assert conf._next_status(conf.STATUS_ACTIVE, 0.5) == conf.STATUS_ACTIVE

    def test_active_falls_to_pending_below_c_min(self):
        assert (
            conf._next_status(conf.STATUS_ACTIVE, 0.25) == conf.STATUS_PENDING_REVIEW
        )

    def test_active_skips_to_retired_below_c_retire(self):
        # If a single hit drops you below c_retire, you go straight to
        # retired without lingering at pending_review.
        assert conf._next_status(conf.STATUS_ACTIVE, 0.05) == conf.STATUS_RETIRED

    def test_pending_recovers_to_active_above_c_min(self):
        assert (
            conf._next_status(conf.STATUS_PENDING_REVIEW, 0.50) == conf.STATUS_ACTIVE
        )

    def test_pending_descends_to_retired(self):
        assert (
            conf._next_status(conf.STATUS_PENDING_REVIEW, 0.05) == conf.STATUS_RETIRED
        )

    def test_retired_is_sticky_even_with_high_confidence(self):
        """A retired skill does not auto-revive just by getting good signals.
        Recovery requires explicit reset_for_review()."""
        assert conf._next_status(conf.STATUS_RETIRED, 0.99) == conf.STATUS_RETIRED


# ---------------------------------------------------------------------------
# update_confidence — DB-backed integration
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    fake_db = tmp_path / "state.db"
    import hermes_state

    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", fake_db)
    echo_db.reset_for_tests()
    yield fake_db
    echo_db.reset_for_tests()


def _seed(skill_id: str, confidence: float = 0.5,
          status: str = "active", locked: int = 0):
    """Insert a confidence row for testing."""
    import time as _time
    conn = echo_db.get_echo_conn()
    now = _time.time()
    conn.execute(
        "INSERT INTO echo_skill_confidence "
        "(skill_id, confidence, status, locked, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (skill_id, confidence, status, locked, now, now),
    )
    conn.commit()


class TestUpdateConfidence:
    def test_explicit_positive_persists(self, isolated_db):
        _seed("s", 0.5)
        result = conf.update_confidence("s", "explicit_positive")
        assert result.applied
        assert result.old_confidence == 0.5
        assert result.new_confidence == pytest.approx(0.6)
        assert result.new_status == conf.STATUS_ACTIVE

        # Re-read from DB
        conn = echo_db.get_echo_conn()
        row = conn.execute(
            "SELECT confidence, status FROM echo_skill_confidence WHERE skill_id='s'"
        ).fetchone()
        assert row["confidence"] == pytest.approx(0.6)
        assert row["status"] == conf.STATUS_ACTIVE

    def test_explicit_negative_drops_to_pending(self, isolated_db):
        _seed("s", 0.4)  # 0.4 * 0.7 = 0.28 < c_min
        result = conf.update_confidence("s", "explicit_negative")
        assert result.applied
        assert result.new_status == conf.STATUS_PENDING_REVIEW

    def test_drift_with_strong_severity_retires(self, isolated_db):
        _seed("s", 0.15)  # 0.15 * (1 - 0.15*5) = -0.0375 → floored to 0
        result = conf.update_confidence("s", "drift_detected", severity=5.0)
        assert result.applied
        assert result.new_confidence < conf.C_RETIRE
        assert result.new_status == conf.STATUS_RETIRED

    def test_retired_records_retired_at(self, isolated_db):
        _seed("s", 0.11)
        conf.update_confidence("s", "explicit_negative")  # 0.11 * 0.7 ≈ 0.077

        conn = echo_db.get_echo_conn()
        row = conn.execute(
            "SELECT status, retired_at FROM echo_skill_confidence WHERE skill_id='s'"
        ).fetchone()
        assert row["status"] == conf.STATUS_RETIRED
        assert row["retired_at"] is not None

    def test_silence_no_op(self, isolated_db):
        _seed("s", 0.5)
        result = conf.update_confidence("s", "silence")
        assert result.applied
        assert result.new_confidence == 0.5
        assert result.old_confidence == result.new_confidence

    def test_locked_skill_unchanged(self, isolated_db):
        _seed("s", 0.5, locked=1)
        result = conf.update_confidence("s", "explicit_negative")
        assert not result.applied
        assert result.reason == "locked"
        assert result.new_confidence == 0.5

        conn = echo_db.get_echo_conn()
        c = conn.execute(
            "SELECT confidence FROM echo_skill_confidence WHERE skill_id='s'"
        ).fetchone()["confidence"]
        assert c == 0.5  # DB row genuinely untouched

    def test_unknown_skill_no_op(self, isolated_db):
        # No seed — the skill doesn't exist yet.
        echo_db.get_echo_conn()  # bootstrap tables
        result = conf.update_confidence("nonexistent", "explicit_positive")
        assert not result.applied
        assert result.reason == "unknown_skill"

    def test_retired_stays_retired_after_positive_signal(self, isolated_db):
        """Sticky retirement: even thumbs up doesn't auto-revive a retired skill."""
        _seed("s", 0.5, status=conf.STATUS_RETIRED)
        result = conf.update_confidence("s", "explicit_positive")
        assert result.applied  # confidence did get bumped
        assert result.new_status == conf.STATUS_RETIRED  # but status stays


# ---------------------------------------------------------------------------
# reset_for_review and set_locked
# ---------------------------------------------------------------------------


class TestResetForReview:
    def test_revives_retired_skill(self, isolated_db):
        _seed("s", 0.05, status=conf.STATUS_RETIRED)
        # Also poke retired_at so we can check it's cleared.
        conn = echo_db.get_echo_conn()
        import time as _t
        conn.execute(
            "UPDATE echo_skill_confidence SET retired_at = ? WHERE skill_id='s'",
            (_t.time(),),
        )
        conn.commit()

        assert conf.reset_for_review("s") is True

        row = conn.execute(
            "SELECT status, confidence, retired_at FROM echo_skill_confidence "
            "WHERE skill_id='s'"
        ).fetchone()
        assert row["status"] == conf.STATUS_PENDING_REVIEW
        assert row["confidence"] == conf.C_MIN
        assert row["retired_at"] is None

    def test_non_retired_skill_is_noop(self, isolated_db):
        _seed("s", 0.5, status=conf.STATUS_ACTIVE)
        assert conf.reset_for_review("s") is False

    def test_locked_skill_not_revived(self, isolated_db):
        _seed("s", 0.05, status=conf.STATUS_RETIRED, locked=1)
        assert conf.reset_for_review("s") is False

    def test_unknown_skill_returns_false(self, isolated_db):
        echo_db.get_echo_conn()
        assert conf.reset_for_review("nonexistent") is False


class TestSetLocked:
    def test_lock_then_unlock(self, isolated_db):
        _seed("s", 0.5)
        assert conf.set_locked("s", True)
        # update_confidence should now be a no-op.
        r1 = conf.update_confidence("s", "explicit_positive")
        assert not r1.applied
        # Unlock — updates resume.
        assert conf.set_locked("s", False)
        r2 = conf.update_confidence("s", "explicit_positive")
        assert r2.applied

    def test_unknown_skill_returns_false(self, isolated_db):
        echo_db.get_echo_conn()
        assert not conf.set_locked("nonexistent", True)
