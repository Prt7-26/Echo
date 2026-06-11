"""M4 — confidence update engine and skill lifecycle state machine.

This module is a pure logic layer: it knows how to apply one confidence
update to one skill row, and what state transitions follow. It is *not*
wired into the signal-collection path (signals.py) yet — that wiring
arrives once Layer B (explicit/NL feedback) and Layer A drift detection
exist to supply meaningful inputs. The current raw signals (user_turn,
tool_call, session_ended) describe activity, not quality, so calling
update_confidence() on them now would conflate the two.

Five update rules per the proposal (DevPlan/proposal.tex §M4):

    explicit_positive  →  c ← min(c + α, 1)            α = 0.10
    nl_positive        →  c ← min(c + α', 1)           α' = 0.05
    explicit_negative  →  c ← c · (1 − γ)              γ = 0.30
    drift_detected     →  c ← c · (1 − β · severity)   β = 0.15
    silence            →  c unchanged                  (sacred invariant)

State machine (echo_skill_confidence.status):

    active  ──[c falls below c_min]──→  pending_review
            ←[update raises c above]─

    pending_review  ──[c falls below c_retire]──→  retired
                    ←[reset_for_review]─

Locked skills (locked = 1) are immune to all updates. The lock is set
externally when a user manually edits the SKILL.md, so we do not
overwrite their work. (Detection of "user edited the file" is not in
this module — a future filesystem-watch hook will set the flag.)
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Literal, Optional

from .db import get_echo_conn


def _is_disabled() -> bool:
    """Ablation switch: ECHO_DISABLE_CONFIDENCE=1 short-circuits update_confidence.

    Used by the evaluation harness to compare full Echo against a
    'signals-only' baseline — hooks still record echo_signal_event rows,
    but confidence never moves and the state machine never advances.
    Checked every call (not memoised) so a test can toggle mid-run.
    """
    return os.environ.get("ECHO_DISABLE_CONFIDENCE", "").strip() in ("1", "true", "yes")

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Defaults — picked up from DevPlan/proposal.tex §M4. These will move to
# config.yaml once Hermes' config layer is integrated; for now constants
# here keep Step 4 self-contained and testable.
# ---------------------------------------------------------------------------

INITIAL_CONFIDENCE = 0.5
ALPHA_EXPLICIT_POSITIVE = 0.10     # explicit thumbs up
ALPHA_NL_POSITIVE = 0.05           # NL-classified positive sentiment
GAMMA_EXPLICIT_NEGATIVE = 0.30     # explicit thumbs down (large hit)
BETA_DRIFT = 0.15                  # Layer A behavior-drift detected
C_MIN = 0.30                       # falling below → pending_review
C_RETIRE = 0.10                    # falling below → retired

STATUS_ACTIVE = "active"
STATUS_PENDING_REVIEW = "pending_review"
STATUS_RETIRED = "retired"


EventType = Literal[
    "explicit_positive",
    "explicit_negative",
    "nl_positive",
    "nl_negative",
    "drift_detected",
    "silence",
]


# ---------------------------------------------------------------------------
# Result type for callers that want to know what happened
# ---------------------------------------------------------------------------


@dataclass
class UpdateResult:
    """What update_confidence() did. Useful for tests and audit logging."""

    skill_id: str
    old_confidence: float
    new_confidence: float
    old_status: str
    new_status: str
    event: EventType
    applied: bool                       # False if locked / unknown skill
    reason: Optional[str] = None        # populated when applied=False


# ---------------------------------------------------------------------------
# Per-rule confidence math (pure functions — no DB, easy to test)
# ---------------------------------------------------------------------------


def _apply_rule(
    c: float,
    event: EventType,
    severity: float = 1.0,
) -> float:
    """Return the new confidence value after applying one rule.

    severity is only consulted for 'drift_detected' — proposal text says
    β scales with the magnitude of the distributional shift. Other event
    types ignore it.
    """
    if event == "explicit_positive":
        return min(c + ALPHA_EXPLICIT_POSITIVE, 1.0)
    if event == "nl_positive":
        return min(c + ALPHA_NL_POSITIVE, 1.0)
    if event == "explicit_negative":
        return max(c * (1.0 - GAMMA_EXPLICIT_NEGATIVE), 0.0)
    if event == "nl_negative":
        # Symmetric with nl_positive — half the step of explicit_negative.
        # Not in proposal text explicitly; included for completeness so
        # NL classifier output can drive update independently of explicit.
        return max(c * (1.0 - GAMMA_EXPLICIT_NEGATIVE / 2.0), 0.0)
    if event == "drift_detected":
        return max(c * (1.0 - BETA_DRIFT * severity), 0.0)
    if event == "silence":
        # SACRED: silence never moves confidence. This is what
        # distinguishes Echo from agentmemory's frequency-based decay.
        # See DevPlan/proposal.tex.
        return c
    raise ValueError(f"Unknown event type: {event!r}")


def _next_status(current: str, new_confidence: float) -> str:
    """State machine transitions driven by confidence threshold crossings.

    Note: this is monotonic in one direction (active → pending_review →
    retired) once you actually retire a skill. Going back to active
    requires explicit reset_for_review() — not just an upward c bump.
    """
    if current == STATUS_RETIRED:
        return STATUS_RETIRED  # retirement is sticky; explicit reset needed

    if new_confidence < C_RETIRE:
        return STATUS_RETIRED

    if new_confidence < C_MIN:
        return STATUS_PENDING_REVIEW

    # Above c_min — recover to active from pending_review.
    # (Retired skills don't recover automatically; see above.)
    return STATUS_ACTIVE


# ---------------------------------------------------------------------------
# Main public API
# ---------------------------------------------------------------------------


def update_confidence(
    skill_id: str,
    event: EventType,
    severity: float = 1.0,
) -> UpdateResult:
    """Apply one confidence update to a single skill.

    Reads the skill's current row, computes the new confidence under
    the relevant rule, updates the row (status included), and returns
    an UpdateResult describing what changed.

    Locked skills (locked = 1) are no-ops — the row is read but not
    written, applied=False, reason='locked'.

    Unknown skills (no row in echo_skill_confidence) are also no-ops
    with reason='unknown_skill' — silently ignored rather than
    auto-creating, because confidence updates should only flow into
    skills Echo already knows about (i.e. has seen a bump_use for).
    """
    if _is_disabled():
        return UpdateResult(
            skill_id=skill_id,
            old_confidence=INITIAL_CONFIDENCE,
            new_confidence=INITIAL_CONFIDENCE,
            old_status=STATUS_ACTIVE,
            new_status=STATUS_ACTIVE,
            event=event,
            applied=False,
            reason="disabled_for_ablation",
        )

    conn = get_echo_conn()
    row = conn.execute(
        "SELECT confidence, status, locked FROM echo_skill_confidence "
        "WHERE skill_id = ?",
        (skill_id,),
    ).fetchone()

    if row is None:
        return UpdateResult(
            skill_id=skill_id,
            old_confidence=INITIAL_CONFIDENCE,
            new_confidence=INITIAL_CONFIDENCE,
            old_status=STATUS_ACTIVE,
            new_status=STATUS_ACTIVE,
            event=event,
            applied=False,
            reason="unknown_skill",
        )

    old_c = float(row["confidence"])
    old_status = str(row["status"])
    locked = int(row["locked"])

    if locked:
        return UpdateResult(
            skill_id=skill_id,
            old_confidence=old_c,
            new_confidence=old_c,
            old_status=old_status,
            new_status=old_status,
            event=event,
            applied=False,
            reason="locked",
        )

    new_c = _apply_rule(old_c, event, severity=severity)
    new_status = _next_status(old_status, new_c)

    now = time.time()
    if new_status == STATUS_RETIRED and old_status != STATUS_RETIRED:
        conn.execute(
            "UPDATE echo_skill_confidence "
            "SET confidence = ?, status = ?, retired_at = ?, updated_at = ? "
            "WHERE skill_id = ?",
            (new_c, new_status, now, now, skill_id),
        )
    else:
        conn.execute(
            "UPDATE echo_skill_confidence "
            "SET confidence = ?, status = ?, updated_at = ? "
            "WHERE skill_id = ?",
            (new_c, new_status, now, skill_id),
        )
    conn.commit()

    return UpdateResult(
        skill_id=skill_id,
        old_confidence=old_c,
        new_confidence=new_c,
        old_status=old_status,
        new_status=new_status,
        event=event,
        applied=True,
    )


def reset_for_review(skill_id: str) -> bool:
    """Move a retired skill back to pending_review with confidence = c_min.

    Used by future workflows (Layer C judge cleared the concern, user
    explicitly unretired the skill, etc.) to lift the sticky retired
    state. Returns True if the skill was retired and is now revived.
    """
    conn = get_echo_conn()
    row = conn.execute(
        "SELECT status, locked FROM echo_skill_confidence WHERE skill_id = ?",
        (skill_id,),
    ).fetchone()
    if row is None or row["status"] != STATUS_RETIRED or row["locked"]:
        return False
    now = time.time()
    conn.execute(
        "UPDATE echo_skill_confidence "
        "SET status = ?, confidence = ?, retired_at = NULL, updated_at = ? "
        "WHERE skill_id = ?",
        (STATUS_PENDING_REVIEW, C_MIN, now, skill_id),
    )
    conn.commit()
    return True


def set_locked(skill_id: str, locked: bool) -> bool:
    """Set or clear the lock flag on a skill.

    locked=True means downstream update_confidence() calls become no-ops.
    Intended to be called when Echo detects a user-authored modification
    to a SKILL.md (future filesystem-watch hook), or via an explicit
    user command.

    Returns True if the row exists and was updated.
    """
    conn = get_echo_conn()
    row = conn.execute(
        "SELECT skill_id FROM echo_skill_confidence WHERE skill_id = ?",
        (skill_id,),
    ).fetchone()
    if row is None:
        return False
    conn.execute(
        "UPDATE echo_skill_confidence SET locked = ?, updated_at = ? "
        "WHERE skill_id = ?",
        (1 if locked else 0, time.time(), skill_id),
    )
    conn.commit()
    return True
