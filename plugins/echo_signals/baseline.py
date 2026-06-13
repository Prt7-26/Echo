"""Layer A behavioral baselines + drift detection.

This is the algorithmic core of M3 Layer A, and the bridge that finally
connects the raw signal stream (signals.py) to the confidence engine
(confidence.py).

For each (skill, metric) pair we maintain an online (mean, variance, n)
estimate via Welford's algorithm. Two metrics are tracked today:

  * ``modification_round_count`` — count of user_turn signals per invocation
  * ``tool_call_count``          — count of tool_call signals per invocation

These are both per-invocation, integer-valued, and roughly Gaussian
after enough samples. Drift detection is a simple per-sample z-score
test: when |z| exceeds DRIFT_THRESHOLD_Z, we feed a ``drift_detected``
event into the confidence engine with severity scaled to how far the
sample sits outside the band.

Cold start: see proposal §"Challenge 1". For n < N_WARM samples we only
accumulate baseline, no drift detection. This avoids the basic
pathology of "first three invocations happened to be mis-applications,
so baseline is poisoned and later correct invocations look like drift".

Should the baseline keep updating after a drift event? Proposal is
silent. We update unconditionally — that way a *real* shift in user
workflow (Instagram-style → Google-Ads-style, say) eventually gets
absorbed and stops triggering drift. The confidence hit during the
transition is the cost; future Layer C judges can disambiguate.

Severity cap: a wildly outlying single sample shouldn't be allowed to
wipe out a whole skill's confidence in one shot. We cap severity at
SEVERITY_CAP (= 3.0), so the worst a single drift can do is
c ← c × (1 − β × 3) = c × 0.55 with default β=0.15.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Iterable

from .db import get_echo_conn

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tunable constants. Match values in DevPlan/schema.md; will migrate to
# config.yaml when the rest of the engine does.
# ---------------------------------------------------------------------------

N_WARM = 20                  # require this many samples before drift detection
DRIFT_THRESHOLD_Z = 2.0      # |z| above this is "drift"
SEVERITY_CAP = 3.0           # one drift never multiplies confidence-loss past this

# The metrics we track per invocation. Adding a new one here is the only
# change required to extend Layer A; finalize_invocation iterates this tuple.
TRACKED_METRICS: tuple[str, ...] = (
    "modification_round_count",
    "tool_call_count",
)


# ---------------------------------------------------------------------------
# Data carriers
# ---------------------------------------------------------------------------


@dataclass
class DriftEvent:
    """One detected drift — value, baseline it deviated from, severity."""

    skill_id: str
    metric_name: str
    value: float
    baseline_mean: float
    baseline_variance: float
    z_score: float
    severity: float          # what we feed to confidence.update_confidence


# ---------------------------------------------------------------------------
# Metric extraction
# ---------------------------------------------------------------------------


def compute_invocation_metrics(invocation_id: int) -> dict[str, float]:
    """Aggregate echo_signal_event rows into Layer A metric values.

    Returns a dict keyed by TRACKED_METRICS names. Missing signals
    contribute 0 — an invocation with no user_turn events has
    modification_round_count = 0, which is itself a meaningful sample.
    """
    conn = get_echo_conn()
    row = conn.execute(
        "SELECT "
        "  SUM(CASE WHEN signal_type='user_turn' THEN 1 ELSE 0 END) AS user_turns, "
        "  SUM(CASE WHEN signal_type='tool_call' THEN 1 ELSE 0 END) AS tool_calls "
        "FROM echo_signal_event "
        "WHERE invocation_id = ?",
        (invocation_id,),
    ).fetchone()
    return {
        "modification_round_count": float(row["user_turns"] or 0),
        "tool_call_count": float(row["tool_calls"] or 0),
    }


# ---------------------------------------------------------------------------
# Welford's online stats — pure logic, no DB
# ---------------------------------------------------------------------------


def _welford_update(
    old_mean: float,
    old_M2: float,
    old_n: int,
    new_value: float,
) -> tuple[float, float, int]:
    """One Welford step. Returns (new_mean, new_M2, new_n).

    M2 is the sum of squared deviations from the running mean
    (Welford's "M2 aggregate"). Sample variance = M2 / (n-1). We store
    sample variance on disk, so the DB layer converts M2 ↔ variance.
    """
    new_n = old_n + 1
    delta = new_value - old_mean
    new_mean = old_mean + delta / new_n
    delta2 = new_value - new_mean
    new_M2 = old_M2 + delta * delta2
    return new_mean, new_M2, new_n


# ---------------------------------------------------------------------------
# Baseline read/write
# ---------------------------------------------------------------------------


def _read_baseline(skill_id: str, metric_name: str):
    """Return the echo_skill_baseline row or None."""
    conn = get_echo_conn()
    return conn.execute(
        "SELECT mean, variance, n, baseline_ready "
        "FROM echo_skill_baseline "
        "WHERE skill_id = ? AND metric_name = ?",
        (skill_id, metric_name),
    ).fetchone()


def update_baseline(
    skill_id: str,
    metric_name: str,
    value: float,
) -> tuple[float, float, int, bool]:
    """Apply one Welford update and persist.

    Returns ``(new_mean, new_variance, new_n, was_ready_before_update)``.
    ``was_ready_before_update`` answers "was this sample eligible for
    drift detection against the *prior* baseline?". Crucial: a sample
    that just barely crossed n=N_WARM gets the baseline-ready flag set
    AFTER this update, but should not yet be checked against itself.
    """
    conn = get_echo_conn()
    now = time.time()
    existing = _read_baseline(skill_id, metric_name)

    if existing is None:
        # Bootstrap row.
        conn.execute(
            "INSERT INTO echo_skill_baseline "
            "(skill_id, metric_name, mean, variance, n, baseline_ready, last_updated) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (skill_id, metric_name, value, 0.0, 1, 0, now),
        )
        conn.commit()
        return (value, 0.0, 1, False)

    old_mean = float(existing["mean"])
    old_n = int(existing["n"])
    # Sample variance → M2 conversion. For n=1 variance is 0 and M2 is 0.
    old_M2 = float(existing["variance"]) * (old_n - 1) if old_n > 1 else 0.0
    was_ready = bool(existing["baseline_ready"])

    new_mean, new_M2, new_n = _welford_update(old_mean, old_M2, old_n, value)
    new_variance = new_M2 / (new_n - 1) if new_n > 1 else 0.0
    new_ready = 1 if new_n >= N_WARM else 0

    conn.execute(
        "UPDATE echo_skill_baseline "
        "SET mean = ?, variance = ?, n = ?, baseline_ready = ?, last_updated = ? "
        "WHERE skill_id = ? AND metric_name = ?",
        (new_mean, new_variance, new_n, new_ready, now, skill_id, metric_name),
    )
    conn.commit()
    return (new_mean, new_variance, new_n, was_ready)


# ---------------------------------------------------------------------------
# Drift check
# ---------------------------------------------------------------------------


def check_drift(
    value: float,
    mean: float,
    variance: float,
) -> tuple[float, bool]:
    """Compute z-score and report whether it exceeds the configured band.

    Variance of 0 (all samples identical so far) is treated as "no
    deviation possible" — z=0, not drift, regardless of value. This
    prevents a divide-by-zero AND avoids spurious drift events when a
    new value happens to differ from a perfectly-constant baseline.
    Future samples will introduce variance and the test will start
    working normally.
    """
    if variance <= 0.0:
        return (0.0, False)
    std = variance ** 0.5
    z = (value - mean) / std
    return (z, abs(z) >= DRIFT_THRESHOLD_Z)


# ---------------------------------------------------------------------------
# The "an invocation is over, process it" workflow
# ---------------------------------------------------------------------------


def finalize_invocation(invocation_id: int) -> list[DriftEvent]:
    """Close out a completed invocation.

    Idempotent: if the invocation's ``finished_at`` is already set, this
    is a no-op (returns []). Otherwise:

      1. Compute Layer A metrics from echo_signal_event.
      2. For each metric: read prior baseline (if ready), check drift
         against *that* baseline, *then* update baseline with the new
         value. Order matters — we want to check the new sample against
         the historical distribution, not the distribution-that-includes-it.
      3. Mark invocation finished.
      4. Feed every detected drift into confidence.update_confidence.

    Returns the list of DriftEvents triggered, mainly for testing /
    logging. The actual confidence updates are performed as a side
    effect.
    """
    conn = get_echo_conn()
    row = conn.execute(
        "SELECT skill_id, finished_at "
        "FROM echo_skill_invocation "
        "WHERE invocation_id = ?",
        (invocation_id,),
    ).fetchone()
    if row is None:
        return []
    if row["finished_at"] is not None:
        return []  # already finalized

    skill_id = row["skill_id"]
    metrics = compute_invocation_metrics(invocation_id)
    drifts: list[DriftEvent] = []

    for metric_name in TRACKED_METRICS:
        value = metrics.get(metric_name, 0.0)
        prior = _read_baseline(skill_id, metric_name)

        # Drift check uses the BEFORE-update baseline so the new value
        # is compared against the historical distribution.
        if prior is not None and prior["baseline_ready"]:
            z, exceeded = check_drift(
                value,
                float(prior["mean"]),
                float(prior["variance"]),
            )
            if exceeded:
                # Scale severity: 1.0 at the threshold, growing linearly
                # with how far past the band the sample sits, capped at
                # SEVERITY_CAP so one extreme outlier can't kill a skill
                # in a single hit.
                raw_severity = (abs(z) - DRIFT_THRESHOLD_Z) + 1.0
                severity = min(raw_severity, SEVERITY_CAP)
                drifts.append(DriftEvent(
                    skill_id=skill_id,
                    metric_name=metric_name,
                    value=value,
                    baseline_mean=float(prior["mean"]),
                    baseline_variance=float(prior["variance"]),
                    z_score=z,
                    severity=severity,
                ))

        update_baseline(skill_id, metric_name, value)

    # Mark invocation finished. Done after metric work in case any of
    # the above raises — that way we'll retry on the next finalize call.
    now = time.time()
    conn.execute(
        "UPDATE echo_skill_invocation "
        "SET finished_at = ? "
        "WHERE invocation_id = ?",
        (now, invocation_id),
    )
    conn.commit()

    # Apply drift events to the confidence engine via the side-effectful
    # wrapper so a state-transition into pending_review starts the Layer
    # C judge. Late-imported to avoid import cycles.
    if drifts:
        from .confidence_actions import apply_signal_event
        from .signals import record_signal

        for drift in drifts:
            try:
                # Audit trail first: a Layer A drift_detected signal_event so
                # the dashboard timeline shows WHY confidence dropped (z-score
                # in value_real, which metric in value_text). Without this the
                # confidence move is invisible in the per-skill timeline.
                try:
                    record_signal(
                        invocation_id=invocation_id,
                        layer="A",
                        signal_type="drift_detected",
                        value_real=float(drift.z_score),
                        value_text=drift.metric_name,
                    )
                except Exception as exc:
                    logger.debug(
                        "drift signal_event write failed for %s/%s: %s",
                        drift.skill_id, drift.metric_name, exc, exc_info=True,
                    )

                apply_signal_event(
                    drift.skill_id,
                    "drift_detected",
                    severity=drift.severity,
                )
            except Exception as exc:
                # As elsewhere in Echo: a bug in our path must not break
                # the Hermes lifecycle that triggered finalize. Log and
                # move on.
                logger.debug(
                    "drift confidence update failed for %s/%s: %s",
                    drift.skill_id, drift.metric_name, exc, exc_info=True,
                )

    return drifts
