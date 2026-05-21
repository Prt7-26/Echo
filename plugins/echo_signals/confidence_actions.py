"""Side-effectful wrapper around confidence.update_confidence.

The pure-logic engine in confidence.py knows how to apply one rule and
return a result. Most callers in Echo also want a side effect: if the
update caused a transition into ``pending_review`` (the "this skill
needs a closer look" state), start an async Layer C judge.

Keeping that side effect *out* of confidence.py preserves its
testability (no LLM dependencies on the test path) and its module-
boundary cleanliness. Callers that want the loop go through here;
unit tests of the pure rules go through confidence.update_confidence
directly.

Two paths trigger the judge:

  active          → pending_review   (the canonical "watching" trigger)
  pending_review  → pending_review   (already watching, don't re-fire)

Retirement is sticky — once a skill is retired we don't keep running
the judge on it; recovery requires explicit reset_for_review.
"""

from __future__ import annotations

import logging
from typing import Optional

from . import confidence as conf_mod
from .confidence import EventType, UpdateResult

logger = logging.getLogger(__name__)


def apply_signal_event(
    skill_id: str,
    event: EventType,
    severity: float = 1.0,
) -> UpdateResult:
    """Apply one confidence update + fire judge on entry into pending_review.

    The judge runs fire-and-forget on a daemon thread, so this call
    returns synchronously once the DB is updated. The caller does not
    block on the LLM round-trip.
    """
    result = conf_mod.update_confidence(skill_id, event, severity=severity)

    if (
        result.applied
        and result.old_status == conf_mod.STATUS_ACTIVE
        and result.new_status == conf_mod.STATUS_PENDING_REVIEW
    ):
        # Late import: judge pulls in the LLM client. Keeping it out of
        # module-import time keeps confidence_actions cheap to import.
        try:
            from . import judge

            judge.start_judge_async(skill_id, result.new_confidence)
        except Exception as exc:
            # As elsewhere, a broken Echo path must not break the caller.
            logger.debug(
                "start_judge_async failed for %s: %s",
                skill_id, exc, exc_info=True,
            )

    return result
