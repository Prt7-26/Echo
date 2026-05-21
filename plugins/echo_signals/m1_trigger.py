"""M1 — adaptive skill-creation trigger.

Echo's role here is *nominator*, not decider. Hermes' own curator
remains in charge of physically writing SKILL.md. Echo identifies
invocations that look like skill-worthy patterns and surfaces them
to the user in the dashboard. The user (or future curator integration)
decides whether to promote a candidate into a real skill.

Proposal §M1 lists four parallel OR conditions for nomination. Three
are implemented here using already-collected signals:

  1. Explicit user request — "save this", "remember this format", etc.
     Detected by a small regex set against incoming user messages.
     One signal event per turn that matches.

  2. High modification investment — modification_round_count ≥ 3.
     Proxy for "the user iterated on this output a lot, so the result
     embodies an effortful template worth saving". Already collected
     by signals.on_pre_llm_call.

  3. Tool-call complexity — tool_call_count ≥ 5. Hermes' existing
     guidance for skill-worthiness. Already collected by
     signals.on_post_tool_call.

The proposal's fourth condition — "task similarity recurrence over
the last N days" — requires semantic-embedding infrastructure that
Hermes does not currently ship with. Echo's hashing embedding (used
for M5 RAG) is too coarse for the cross-session, time-windowed
clustering this would need. Documented as a known limitation here;
adding a proper embedding provider would unblock this fourth condition.

Scoring (weights chosen to keep explicit intent dominant):

  save_intent      → 100  (single biggest weight; the user *told* us)
  tool_count ≥ 5   → 30
  modif_rounds ≥ 3 → 30

Threshold to appear in the candidate list: total ≥ 30. The dashboard
exposes the list; each candidate carries its score breakdown so the
user understands *why* Echo nominated it.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from .db import get_echo_conn

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scoring constants
# ---------------------------------------------------------------------------

WEIGHT_SAVE_INTENT = 100
WEIGHT_TOOL_COUNT = 30
WEIGHT_MODIF_ROUNDS = 30
THRESHOLD_TOOL_COUNT = 5
THRESHOLD_MODIF_ROUNDS = 3
SCORE_THRESHOLD = 30


# ---------------------------------------------------------------------------
# Save-intent detection
# ---------------------------------------------------------------------------

# Pattern set kept tight to avoid false positives. We match on whole-
# phrase intent, not single words — "save" alone shows up in too many
# legitimate non-skill-creation contexts ("save the file at...").
#
# English patterns are anchored with negative-lookahead where useful;
# Chinese patterns use direct substring match because there's no
# word-boundary semantics. Compile once at import time.

_SAVE_INTENT_PATTERNS = [
    # English explicit save. We allow optional intervening words between
    # the demonstrative and the preposition so phrases like "save that
    # workflow to a skill" or "save this approach as a template" match.
    # \W is a generous separator class; we don't want to be picky about
    # punctuation here.
    re.compile(
        r"\bsave\s+(this|that|the)\b(?:[\w\s]{0,40})\b(as|to|for)\b\s+(?:a\s+)?"
        r"(skill|template|workflow|recipe|procedure|process|later|"
        r"future\s+use|future|reuse|next\s+time)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bremember\s+(this|that|how)\b", re.IGNORECASE),
    re.compile(r"\bturn\s+(this|that)\s+into\s+a\s+skill\b", re.IGNORECASE),
    re.compile(r"\bmake\s+(this|that)\s+a\s+(skill|template)\b", re.IGNORECASE),
    re.compile(r"\bstore\s+(this|that)\s+(as|for)\b", re.IGNORECASE),
    re.compile(
        r"\b(do|use)\s+(this|that)\s+(every\s+time|from\s+now\s+on)\b",
        re.IGNORECASE,
    ),
    # Chinese. 允许 "这个/这/这种/这样的/那个/那" 系列; 动词侧允许 "存/记/保留/保存".
    re.compile(r"把(这个|这|这种|这样的|那个|那)(流程|步骤|做法|方法|写法).*?(存|记|保留|保存)"),
    re.compile(r"(下次|以后|之后).*?(就|也|都)(这样|这么)"),
    re.compile(r"记住(这个|这种|这样的)"),
    re.compile(r"(保存|存)\s*为\s*(技能|skill)", re.IGNORECASE),
]


def detect_save_intent(text: str) -> bool:
    """Return True if the message reads like a "save this as a skill" intent.

    Empty / None text → False. The matchers are case-insensitive in
    English and direct in Chinese. No tokenization — we just scan the
    raw text, which is robust to punctuation and word ordering.
    """
    if not text or not text.strip():
        return False
    for pat in _SAVE_INTENT_PATTERNS:
        if pat.search(text):
            return True
    return False


# ---------------------------------------------------------------------------
# Signal recording — called from signals.on_pre_llm_call
# ---------------------------------------------------------------------------


def record_save_intent_signal(invocation_id: int, skill_id: str) -> None:
    """Append a Layer B m1_save_intent event to echo_signal_event.

    Layer B because it's user-message-derived (external evidence), not
    behavioral count. Idempotency NOT required — multiple positive
    matches in the same invocation just bump the score (capped by the
    candidate_score below).
    """
    if not skill_id:
        return
    try:
        conn = get_echo_conn()
        ts = time.time()
        conn.execute(
            "INSERT INTO echo_signal_event "
            "(invocation_id, skill_id, layer, signal_type, value_int, ts) "
            "VALUES (?, ?, 'B', 'm1_save_intent', 1, ?)",
            (invocation_id, skill_id, ts),
        )
        conn.execute(
            "UPDATE echo_skill_confidence "
            "SET n_signals = n_signals + 1, updated_at = ? "
            "WHERE skill_id = ?",
            (ts, skill_id),
        )
        conn.commit()
    except Exception as exc:
        logger.debug("record_save_intent_signal failed: %s", exc, exc_info=True)


# ---------------------------------------------------------------------------
# Per-invocation scoring
# ---------------------------------------------------------------------------


@dataclass
class CandidateScore:
    invocation_id: int
    skill_id: Optional[str]
    score: int
    reasons: list[str]
    user_turns: int
    tool_calls: int
    has_save_intent: bool


def _score_one(row) -> CandidateScore:
    """Compose a CandidateScore from an echo_skill_invocation row joined
    with its event counts (passed through ``row`` as a dict-like)."""
    has_save = bool(row["save_intent_count"])
    user_turns = int(row["user_turns"] or 0)
    tool_calls = int(row["tool_calls"] or 0)

    score = 0
    reasons: list[str] = []
    if has_save:
        score += WEIGHT_SAVE_INTENT
        reasons.append("user expressed save intent")
    if tool_calls >= THRESHOLD_TOOL_COUNT:
        score += WEIGHT_TOOL_COUNT
        reasons.append(f"high tool-call complexity ({tool_calls} calls)")
    if user_turns >= THRESHOLD_MODIF_ROUNDS:
        score += WEIGHT_MODIF_ROUNDS
        reasons.append(f"high modification investment ({user_turns} turns)")

    return CandidateScore(
        invocation_id=int(row["invocation_id"]),
        skill_id=row["skill_id"],
        score=score,
        reasons=reasons,
        user_turns=user_turns,
        tool_calls=tool_calls,
        has_save_intent=has_save,
    )


def list_candidates(
    *,
    limit: int = 20,
    only_finalized: bool = True,
    min_score: int = SCORE_THRESHOLD,
) -> list[CandidateScore]:
    """Return invocations that scored above the threshold, most recent first.

    Joins echo_skill_invocation with per-invocation event counts in a
    single query; cheap enough at our expected scale (a few hundred
    invocations total).

    ``only_finalized=True`` filters to invocations that finalize_invocation
    has already processed — open invocations can't be candidates yet
    because their signal counts are still moving.
    """
    finalized_clause = "AND i.finished_at IS NOT NULL" if only_finalized else ""
    sql = f"""
        SELECT
            i.invocation_id,
            i.skill_id,
            i.started_at,
            i.finished_at,
            (SELECT COUNT(*) FROM echo_signal_event e
             WHERE e.invocation_id = i.invocation_id
               AND e.signal_type = 'user_turn') AS user_turns,
            (SELECT COUNT(*) FROM echo_signal_event e
             WHERE e.invocation_id = i.invocation_id
               AND e.signal_type = 'tool_call') AS tool_calls,
            (SELECT COUNT(*) FROM echo_signal_event e
             WHERE e.invocation_id = i.invocation_id
               AND e.signal_type = 'm1_save_intent') AS save_intent_count
        FROM echo_skill_invocation i
        WHERE 1=1
        {finalized_clause}
        ORDER BY i.started_at DESC
        LIMIT ?
    """
    conn = get_echo_conn()
    rows = conn.execute(sql, (max(limit * 4, 100),)).fetchall()

    candidates: list[CandidateScore] = []
    for r in rows:
        cs = _score_one(r)
        if cs.score >= min_score:
            candidates.append(cs)
        if len(candidates) >= limit:
            break
    return candidates
