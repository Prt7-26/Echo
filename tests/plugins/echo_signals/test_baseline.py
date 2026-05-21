"""Unit tests for Layer A baseline + drift detection.

Three categories:
  * Welford math purity (no DB).
  * Per-function correctness against an in-memory DB (compute_metrics,
    update_baseline, check_drift, finalize_invocation).
  * End-to-end: a realistic 30-invocation flow where the first 20 are
    "normal", the 21st is a wild outlier, and we verify the confidence
    engine actually receives a drift_detected event.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from plugins.echo_signals import baseline as bl
from plugins.echo_signals import confidence as conf
from plugins.echo_signals import db as echo_db


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




def _seed_confidence(skill_id: str, confidence: float = 0.5):
    conn = echo_db.get_echo_conn()
    now = time.time()
    conn.execute(
        "INSERT INTO echo_skill_confidence "
        "(skill_id, confidence, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (skill_id, confidence, now, now),
    )
    conn.commit()


def _seed_invocation(skill_id: str, session_id: str = "s1") -> int:
    conn = echo_db.get_echo_conn()
    cur = conn.execute(
        "INSERT INTO echo_skill_invocation "
        "(skill_id, session_id, platform, started_at) VALUES (?, ?, ?, ?)",
        (skill_id, session_id, "cli", time.time()),
    )
    conn.commit()
    return cur.lastrowid


def _seed_events(invocation_id: int, skill_id: str,
                 user_turns: int = 0, tool_calls: int = 0):
    conn = echo_db.get_echo_conn()
    now = time.time()
    for _ in range(user_turns):
        conn.execute(
            "INSERT INTO echo_signal_event "
            "(invocation_id, skill_id, layer, signal_type, ts) "
            "VALUES (?, ?, 'A', 'user_turn', ?)",
            (invocation_id, skill_id, now),
        )
    for _ in range(tool_calls):
        conn.execute(
            "INSERT INTO echo_signal_event "
            "(invocation_id, skill_id, layer, signal_type, value_text, ts) "
            "VALUES (?, ?, 'A', 'tool_call', 'some_tool', ?)",
            (invocation_id, skill_id, now),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Pure Welford math — no DB
# ---------------------------------------------------------------------------


class TestWelfordUpdate:
    def test_first_sample(self):
        # Welford on (mean=0, M2=0, n=0) with new value 5 → (5, 0, 1)
        m, M2, n = bl._welford_update(0.0, 0.0, 0, 5.0)
        assert (m, M2, n) == (5.0, 0.0, 1)

    def test_two_samples(self):
        # First (0,0,0,3) → (3, 0, 1)
        m, M2, n = bl._welford_update(0.0, 0.0, 0, 3.0)
        # Then (3, 0, 1, 5) → mean = 4, M2 = 2
        m, M2, n = bl._welford_update(m, M2, n, 5.0)
        assert m == pytest.approx(4.0)
        assert n == 2
        # Sample variance for {3, 5} is ((3-4)² + (5-4)²)/(2-1) = 2.
        assert M2 == pytest.approx(2.0)

    def test_matches_textbook_formula(self):
        """Welford should produce the same mean/variance as np.var(ddof=1)."""
        samples = [1.0, 4.0, 9.0, 16.0, 25.0]
        m, M2, n = 0.0, 0.0, 0
        for x in samples:
            m, M2, n = bl._welford_update(m, M2, n, x)
        expected_mean = sum(samples) / len(samples)
        expected_var = sum((x - expected_mean) ** 2 for x in samples) / (len(samples) - 1)
        assert m == pytest.approx(expected_mean)
        assert M2 / (n - 1) == pytest.approx(expected_var)
        assert n == len(samples)


# ---------------------------------------------------------------------------
# check_drift — pure z-score math
# ---------------------------------------------------------------------------


class TestCheckDrift:
    def test_zero_variance_never_drifts(self):
        # All-identical history → variance is 0. Treat as "can't decide".
        z, exceeded = bl.check_drift(value=10.0, mean=5.0, variance=0.0)
        assert z == 0.0 and not exceeded

    def test_within_band(self):
        z, exceeded = bl.check_drift(value=6.0, mean=5.0, variance=4.0)
        # z = (6-5)/2 = 0.5
        assert z == pytest.approx(0.5)
        assert not exceeded

    def test_exceeds_threshold(self):
        # variance=1 → std=1. z = (value - 5)/1.
        z, exceeded = bl.check_drift(value=8.0, mean=5.0, variance=1.0)
        assert z == pytest.approx(3.0)
        assert exceeded

    def test_negative_z_also_detected(self):
        z, exceeded = bl.check_drift(value=0.0, mean=5.0, variance=1.0)
        assert z == pytest.approx(-5.0)
        assert exceeded


# ---------------------------------------------------------------------------
# compute_invocation_metrics
# ---------------------------------------------------------------------------


class TestComputeMetrics:
    def test_counts_signals_per_invocation(self, isolated_db):
        _seed_confidence("alpha")
        inv = _seed_invocation("alpha")
        _seed_events(inv, "alpha", user_turns=3, tool_calls=2)
        m = bl.compute_invocation_metrics(inv)
        assert m == {"modification_round_count": 3.0, "tool_call_count": 2.0}

    def test_zero_signals_returns_zero(self, isolated_db):
        _seed_confidence("alpha")
        inv = _seed_invocation("alpha")
        m = bl.compute_invocation_metrics(inv)
        assert m == {"modification_round_count": 0.0, "tool_call_count": 0.0}

    def test_isolation_between_invocations(self, isolated_db):
        _seed_confidence("alpha")
        inv1 = _seed_invocation("alpha")
        inv2 = _seed_invocation("alpha")
        _seed_events(inv1, "alpha", user_turns=5)
        _seed_events(inv2, "alpha", user_turns=2)
        assert bl.compute_invocation_metrics(inv1)["modification_round_count"] == 5.0
        assert bl.compute_invocation_metrics(inv2)["modification_round_count"] == 2.0


# ---------------------------------------------------------------------------
# update_baseline (persistence layer)
# ---------------------------------------------------------------------------


class TestUpdateBaseline:
    def test_first_sample_bootstraps_row(self, isolated_db):
        _seed_confidence("alpha")
        mean, var, n, was_ready = bl.update_baseline("alpha", "modification_round_count", 5.0)
        assert (mean, var, n, was_ready) == (5.0, 0.0, 1, False)
        # And it's persisted.
        conn = echo_db.get_echo_conn()
        row = conn.execute(
            "SELECT * FROM echo_skill_baseline WHERE skill_id='alpha'"
        ).fetchone()
        assert row is not None
        assert row["mean"] == 5.0
        assert row["n"] == 1
        assert row["baseline_ready"] == 0

    def test_baseline_ready_flips_at_n_warm(self, isolated_db):
        _seed_confidence("alpha")
        for i in range(bl.N_WARM):
            _, _, n, was_ready = bl.update_baseline(
                "alpha", "modification_round_count", float(i),
            )
            # was_ready reflects the *prior* state; only true once n was
            # already >= N_WARM before this call. The first N_WARM samples
            # are never "checked against".
            assert not was_ready
        # After N_WARM samples the row's baseline_ready flag is set.
        conn = echo_db.get_echo_conn()
        row = conn.execute(
            "SELECT baseline_ready, n FROM echo_skill_baseline "
            "WHERE skill_id='alpha' AND metric_name='modification_round_count'"
        ).fetchone()
        assert row["n"] == bl.N_WARM
        assert row["baseline_ready"] == 1

        # The N_WARM+1-th sample sees was_ready=True.
        _, _, _, was_ready_now = bl.update_baseline(
            "alpha", "modification_round_count", 42.0,
        )
        assert was_ready_now is True

    def test_mean_and_variance_track_input(self, isolated_db):
        _seed_confidence("alpha")
        samples = [1.0, 2.0, 3.0, 4.0, 5.0]
        for v in samples:
            bl.update_baseline("alpha", "modification_round_count", v)
        conn = echo_db.get_echo_conn()
        row = conn.execute(
            "SELECT mean, variance, n FROM echo_skill_baseline "
            "WHERE skill_id='alpha' AND metric_name='modification_round_count'"
        ).fetchone()
        assert row["mean"] == pytest.approx(3.0)
        # Sample variance of {1,2,3,4,5} = 2.5
        assert row["variance"] == pytest.approx(2.5)
        assert row["n"] == 5


# ---------------------------------------------------------------------------
# finalize_invocation — orchestration
# ---------------------------------------------------------------------------


class TestFinalizeInvocation:
    def test_unknown_invocation_id_is_noop(self, isolated_db):
        echo_db.get_echo_conn()  # bootstrap
        result = bl.finalize_invocation(999999)
        assert result == []

    def test_cold_start_no_drift_events(self, isolated_db):
        _seed_confidence("alpha")
        # Three invocations all with 5 user_turns each. n < N_WARM, so
        # no drift should ever fire, even though the 3rd is identical
        # to the prior 2.
        for _ in range(3):
            inv = _seed_invocation("alpha")
            _seed_events(inv, "alpha", user_turns=5)
            drifts = bl.finalize_invocation(inv)
            assert drifts == []

    def test_marks_invocation_finished(self, isolated_db):
        _seed_confidence("alpha")
        inv = _seed_invocation("alpha")
        _seed_events(inv, "alpha", user_turns=2)
        before = time.time()
        bl.finalize_invocation(inv)
        conn = echo_db.get_echo_conn()
        row = conn.execute(
            "SELECT finished_at FROM echo_skill_invocation WHERE invocation_id=?",
            (inv,),
        ).fetchone()
        assert row["finished_at"] is not None
        assert row["finished_at"] >= before

    def test_idempotent_double_finalize(self, isolated_db):
        _seed_confidence("alpha")
        inv = _seed_invocation("alpha")
        _seed_events(inv, "alpha", user_turns=2)
        first = bl.finalize_invocation(inv)
        second = bl.finalize_invocation(inv)
        # Second call returns empty because the row is already finished;
        # no double-counted baseline updates.
        assert second == []
        conn = echo_db.get_echo_conn()
        n = conn.execute(
            "SELECT n FROM echo_skill_baseline "
            "WHERE skill_id='alpha' AND metric_name='modification_round_count'"
        ).fetchone()["n"]
        assert n == 1  # only counted once

    def test_drift_after_baseline_ready(self, isolated_db):
        """The headline test: 20 normal invocations + 1 outlier triggers drift."""
        _seed_confidence("alpha", confidence=0.5)
        # Build a tight baseline around 5 user_turns / 2 tool_calls.
        for i in range(bl.N_WARM):
            inv = _seed_invocation("alpha")
            # Add some spread so variance > 0 — values in {4,5,6}.
            user_turns = 4 + (i % 3)
            tool_calls = 1 + (i % 2)
            _seed_events(inv, "alpha", user_turns=user_turns, tool_calls=tool_calls)
            drifts = bl.finalize_invocation(inv)
            assert drifts == []

        # 21st invocation: way outside the band. 50 user_turns vs ~5.
        outlier_inv = _seed_invocation("alpha")
        _seed_events(outlier_inv, "alpha", user_turns=50, tool_calls=2)
        drifts = bl.finalize_invocation(outlier_inv)

        # At least one drift event for modification_round_count.
        names = [d.metric_name for d in drifts]
        assert "modification_round_count" in names

        # And the confidence engine was actually called — confidence
        # should now be below 0.5.
        conn = echo_db.get_echo_conn()
        c = conn.execute(
            "SELECT confidence FROM echo_skill_confidence WHERE skill_id='alpha'"
        ).fetchone()["confidence"]
        assert c < 0.5

    def test_severity_capped(self, isolated_db):
        """Even an extreme z-score can't push severity past SEVERITY_CAP."""
        _seed_confidence("alpha", confidence=0.5)
        # Tight baseline: all 5's.
        for _ in range(bl.N_WARM):
            inv = _seed_invocation("alpha")
            _seed_events(inv, "alpha", user_turns=5, tool_calls=2)
            bl.finalize_invocation(inv)

        # Manually nudge variance so it's nonzero (otherwise check_drift
        # short-circuits to z=0).
        conn = echo_db.get_echo_conn()
        conn.execute(
            "UPDATE echo_skill_baseline SET variance = 0.25 "
            "WHERE skill_id='alpha' AND metric_name='modification_round_count'"
        )
        conn.commit()

        # Wild outlier: 100 turns.
        outlier_inv = _seed_invocation("alpha")
        _seed_events(outlier_inv, "alpha", user_turns=100, tool_calls=2)
        drifts = bl.finalize_invocation(outlier_inv)
        # severity in [1.0, SEVERITY_CAP]
        mod_drift = next(d for d in drifts if d.metric_name == "modification_round_count")
        assert 1.0 <= mod_drift.severity <= bl.SEVERITY_CAP
        assert mod_drift.severity == bl.SEVERITY_CAP  # this z is huge

    def test_baseline_keeps_updating_after_drift(self, isolated_db):
        """A drift event doesn't stop the baseline from adapting."""
        _seed_confidence("alpha", confidence=0.5)
        for _ in range(bl.N_WARM):
            inv = _seed_invocation("alpha")
            _seed_events(inv, "alpha", user_turns=5, tool_calls=2)
            bl.finalize_invocation(inv)

        conn = echo_db.get_echo_conn()
        n_before = conn.execute(
            "SELECT n FROM echo_skill_baseline WHERE metric_name='modification_round_count'"
        ).fetchone()["n"]

        outlier_inv = _seed_invocation("alpha")
        _seed_events(outlier_inv, "alpha", user_turns=50, tool_calls=2)
        bl.finalize_invocation(outlier_inv)

        n_after = conn.execute(
            "SELECT n FROM echo_skill_baseline WHERE metric_name='modification_round_count'"
        ).fetchone()["n"]
        assert n_after == n_before + 1  # outlier WAS absorbed


# ---------------------------------------------------------------------------
# End-to-end through usage_hook + signal + finalize
# ---------------------------------------------------------------------------


class TestLifecycleIntegration:
    def test_skill_switch_finalizes_prior(self, isolated_db):
        """bump_use to a new skill should finalize the prior invocation."""
        from plugins.echo_signals import session_context as sc
        from plugins.echo_signals import usage_hook as uh

        uh.install_bump_use_hook()
        try:
            sc.set_session_context("session-1", "cli")

            import tools.skill_usage as _su
            _su.bump_use("first-skill")
            first_invocation = sc.get_current_invocation_id()

            # Simulate some signals on the first invocation.
            conn = echo_db.get_echo_conn()
            for _ in range(2):
                conn.execute(
                    "INSERT INTO echo_signal_event "
                    "(invocation_id, skill_id, layer, signal_type, ts) "
                    "VALUES (?, 'first-skill', 'A', 'user_turn', ?)",
                    (first_invocation, time.time()),
                )
            conn.commit()

            # Switch to a second skill.
            _su.bump_use("second-skill")
            second_invocation = sc.get_current_invocation_id()

            assert first_invocation != second_invocation

            # First invocation should now be finalized.
            first_row = conn.execute(
                "SELECT finished_at FROM echo_skill_invocation WHERE invocation_id = ?",
                (first_invocation,),
            ).fetchone()
            assert first_row["finished_at"] is not None

            # And first-skill should have a baseline row now.
            first_baseline = conn.execute(
                "SELECT n FROM echo_skill_baseline "
                "WHERE skill_id='first-skill' AND metric_name='modification_round_count'"
            ).fetchone()
            assert first_baseline is not None
            assert first_baseline["n"] == 1
        finally:
            uh.uninstall_bump_use_hook()
            sc.clear_session_context()

    def test_session_end_finalizes_current(self, isolated_db):
        """on_session_end (via the public __init__ hook) finalizes the current invocation."""
        from plugins.echo_signals import _on_session_end, _on_session_start
        from plugins.echo_signals import session_context as sc
        from plugins.echo_signals import usage_hook as uh

        uh.install_bump_use_hook()
        try:
            _on_session_start(session_id="session-2", platform="cli")
            import tools.skill_usage as _su
            _su.bump_use("alpha")
            invocation_id = sc.get_current_invocation_id()

            _on_session_end()

            conn = echo_db.get_echo_conn()
            row = conn.execute(
                "SELECT finished_at FROM echo_skill_invocation WHERE invocation_id = ?",
                (invocation_id,),
            ).fetchone()
            assert row["finished_at"] is not None

            # Baseline got written for the alpha skill.
            baseline_rows = conn.execute(
                "SELECT n FROM echo_skill_baseline WHERE skill_id='alpha'"
            ).fetchall()
            assert len(baseline_rows) == 2  # one per tracked metric
        finally:
            uh.uninstall_bump_use_hook()
            sc.clear_session_context()
