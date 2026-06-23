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
WEIGHT_RECURRENCE = 50
# Thresholds use >= comparison (maintainer's tuning — keep the nominator from
# firing on light tasks): ≥20 tool calls, ≥5 modification rounds.
THRESHOLD_TOOL_COUNT = 20
THRESHOLD_MODIF_ROUNDS = 5
SCORE_THRESHOLD = 30

# Semantic recurrence detection params. With neural embeddings (the configured
# DashScope text-embedding-v3) cosine runs high even for loosely related text,
# so 0.6 fired too eagerly (a fresh "weekly workout plan" matched earlier ones).
# 0.8 requires the new request to be genuinely the SAME kind of task before it
# counts as recurrence (maintainer tuning).
RECURRENCE_THRESHOLD = 0.8
RECURRENCE_LOOKBACK_DAYS = 30
# Recent self-correlation guard: don't match against turns from the
# same invocation or from the past 60 seconds (rapid re-asks in the
# same chat aren't recurrence, they're refinement).
RECURRENCE_SELF_WINDOW_SECONDS = 60.0


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
# M1 condition 4 — semantic task recurrence
# ---------------------------------------------------------------------------
#
# This is a LEXICAL recurrence detector, not a neural semantic one. The
# proposal says "embedding 余弦相似度" — strictly speaking that means a
# learned embedding (SentenceTransformers, OpenAI text-embedding-3, etc.).
# Hermes doesn't ship one; adding a per-turn LLM call goes against
# Echo's "near-zero daily cost" design point. So we reuse M5's hashing
# embedding here. It catches token-level repetition strongly ("write me
# a marketing email" / "marketing email for our launch" match) but
# misses paraphrase ("draft a promotional message" wouldn't match
# despite identical intent). Documented as a known proxy — sufficient
# for proposal sign-off as "condition 4 implemented" with the lexical
# caveat noted in CLAUDE.md and the m1_semantic_recurrence signal_type
# name reflecting the chosen interpretation.


def log_user_request(
    *,
    invocation_id: Optional[int],
    skill_id: Optional[str],
    session_id: Optional[str],
    user_message: str,
    save_intent: bool = False,
    recurrence_sim: Optional[float] = None,
) -> None:
    """Persist this turn's user_message + hashing-embedding for future
    recurrence checks.

    Cheap (microseconds + ~1KB BLOB per turn). The row is what
    detect_semantic_recurrence compares against on subsequent turns.

    ``save_intent`` / ``recurrence_sim`` are the per-turn M1 signals; for
    SKILL-LESS turns (invocation_id/skill_id both None) the row in this
    table is the ONLY place they can live — echo_signal_event requires a
    non-NULL invocation. list_session_candidates() reads them back.
    """
    if not user_message or not user_message.strip():
        return
    try:
        from .preference_rag import encode, vec_to_blob

        vec = encode(user_message)
        blob = vec_to_blob(vec)
        conn = get_echo_conn()
        conn.execute(
            "INSERT INTO echo_user_request_log "
            "(invocation_id, skill_id, session_id, user_message, embedding, ts, "
            " save_intent, recurrence_sim) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (invocation_id, skill_id, session_id, user_message, blob, time.time(),
             1 if save_intent else 0, recurrence_sim),
        )
        conn.commit()
    except Exception as exc:
        logger.debug("log_user_request failed: %s", exc, exc_info=True)


def detect_semantic_recurrence(
    user_message: str,
    *,
    current_invocation_id: Optional[int] = None,
    lookback_days: float = RECURRENCE_LOOKBACK_DAYS,
    threshold: float = RECURRENCE_THRESHOLD,
) -> tuple[bool, float]:
    """Return (hit, top_similarity) for whether ``user_message`` matches
    any past request within the lookback window above the threshold.

    Excludes turns from the current invocation and anything within the
    last RECURRENCE_SELF_WINDOW_SECONDS so rapid re-asks don't
    self-trigger. ``threshold`` is what the caller treats as "match";
    we return the top similarity regardless so callers can log /
    display "matched at 0.71".
    """
    if not user_message or not user_message.strip():
        return (False, 0.0)
    try:
        from .preference_rag import cosine, encode

        now = time.time()
        cutoff_old = now - (lookback_days * 86400.0)
        cutoff_recent = now - RECURRENCE_SELF_WINDOW_SECONDS

        query_vec = encode(user_message)

        conn = get_echo_conn()
        if current_invocation_id is None:
            rows = conn.execute(
                "SELECT embedding, ts FROM echo_user_request_log "
                "WHERE ts >= ? AND ts <= ?",
                (cutoff_old, cutoff_recent),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT embedding, ts FROM echo_user_request_log "
                "WHERE ts >= ? AND ts <= ? "
                "  AND (invocation_id IS NULL OR invocation_id != ?)",
                (cutoff_old, cutoff_recent, current_invocation_id),
            ).fetchall()

        from .preference_rag import blob_to_vec

        top = 0.0
        for r in rows:
            vec = blob_to_vec(r["embedding"])
            sim = cosine(query_vec, vec)
            if sim > top:
                top = sim
        return (top >= threshold, top)
    except Exception as exc:
        logger.debug("detect_semantic_recurrence failed: %s", exc, exc_info=True)
        return (False, 0.0)


def record_semantic_recurrence_signal(invocation_id: int, skill_id: str,
                                      similarity: float) -> None:
    """Layer B signal: matched a prior request above threshold."""
    if not skill_id:
        return
    try:
        conn = get_echo_conn()
        ts = time.time()
        conn.execute(
            "INSERT INTO echo_signal_event "
            "(invocation_id, skill_id, layer, signal_type, value_real, ts) "
            "VALUES (?, ?, 'B', 'm1_semantic_recurrence', ?, ?)",
            (invocation_id, skill_id, similarity, ts),
        )
        conn.execute(
            "UPDATE echo_skill_confidence "
            "SET n_signals = n_signals + 1, updated_at = ? "
            "WHERE skill_id = ?",
            (ts, skill_id),
        )
        conn.commit()
    except Exception as exc:
        logger.debug(
            "record_semantic_recurrence_signal failed: %s", exc, exc_info=True,
        )


def record_session_tool_call(session_id: Optional[str]) -> None:
    """Increment a SKILL-LESS conversation's tool-call counter.

    Called from signals.on_post_tool_call when no skill invocation is
    active. Tool calls made *during* a skilled invocation are already
    counted per-invocation in echo_signal_event, so this counter only
    accrues for conversations with no active skill — exactly the ones
    list_session_candidates() nominates.
    """
    if not session_id:
        return
    try:
        conn = get_echo_conn()
        now = time.time()
        conn.execute(
            "INSERT INTO echo_session_tool_count (session_id, tool_calls, updated_at) "
            "VALUES (?, 1, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET "
            "  tool_calls = tool_calls + 1, "
            "  updated_at = excluded.updated_at",
            (session_id, now),
        )
        conn.commit()
    except Exception as exc:
        logger.debug("record_session_tool_call failed: %s", exc, exc_info=True)


def set_session_tool_count(session_id: Optional[str], count: int) -> None:
    """Set a SKILL-LESS conversation's tool-call counter to at least ``count``.

    The increment-on-each-post_tool_call path (record_session_tool_call) relies
    on the session contextvar being set when the tool finishes — but Hermes runs
    tools in worker threads where that contextvar isn't propagated, so most
    calls are lost. conversation_history (passed to pre_llm_call) carries every
    tool-result message authoritatively, so signals.on_pre_llm_call counts those
    and calls this. Monotonic (max) so a later turn never shrinks the count.
    """
    if not session_id or count <= 0:
        return
    try:
        conn = get_echo_conn()
        now = time.time()
        conn.execute(
            "INSERT INTO echo_session_tool_count (session_id, tool_calls, updated_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET "
            "  tool_calls = MAX(tool_calls, excluded.tool_calls), "
            "  updated_at = excluded.updated_at",
            (session_id, count, now),
        )
        conn.commit()
    except Exception as exc:
        logger.debug("set_session_tool_count failed: %s", exc, exc_info=True)


def gc_old_requests(retention_days: float = RECURRENCE_LOOKBACK_DAYS * 2,
                    conn=None) -> int:
    """Delete user_request_log rows older than ``retention_days``.

    Default retention is twice the recurrence lookback so we never lose
    rows that could still match. Caller (e.g. cron, plugin lifecycle)
    decides when to invoke. Returns row-delete count.

    ``conn``: pass a caller-owned connection when running off a background
    thread (the GC daemon does) so we never share the module-level cached
    connection across threads. Defaults to the shared connection.
    """
    try:
        if conn is None:
            conn = get_echo_conn()
        cutoff = time.time() - (retention_days * 86400.0)
        cur = conn.execute(
            "DELETE FROM echo_user_request_log WHERE ts < ?",
            (cutoff,),
        )
        conn.commit()
        return cur.rowcount
    except Exception as exc:
        logger.debug("gc_old_requests failed: %s", exc, exc_info=True)
        return 0


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
    has_recurrence: bool = False


def _score_one(row) -> CandidateScore:
    """Compose a CandidateScore from an echo_skill_invocation row joined
    with its event counts (passed through ``row`` as a dict-like)."""
    has_save = bool(row["save_intent_count"])
    has_recurrence = bool(row["recurrence_count"])
    user_turns = int(row["user_turns"] or 0)
    tool_calls = int(row["tool_calls"] or 0)

    score = 0
    reasons: list[str] = []
    if has_save:
        score += WEIGHT_SAVE_INTENT
        reasons.append("user expressed save intent")
    if has_recurrence:
        score += WEIGHT_RECURRENCE
        reasons.append("task pattern recurred from past sessions (lexical match)")
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
        has_recurrence=has_recurrence,
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
               AND e.signal_type = 'm1_save_intent') AS save_intent_count,
            (SELECT COUNT(*) FROM echo_signal_event e
             WHERE e.invocation_id = i.invocation_id
               AND e.signal_type = 'm1_semantic_recurrence') AS recurrence_count
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


# ---------------------------------------------------------------------------
# Session-level nomination — SKILL-LESS conversations (proposal §M1's
# "孵化全新技能" intent). The invocation-scoped list_candidates above can only
# flag uses of EXISTING skills; a conversation that never loaded any skill has
# no echo_skill_invocation row, so it can never appear there. This path scores
# such conversations straight from echo_user_request_log (the one signal store
# that allows NULL invocation/skill), so a user who drafts an email over many
# turns — or says "保存为技能" mid-chat — gets nominated to create a NEW skill.
#
# All four proposal conditions apply here. Tool-call complexity uses the
# echo_session_tool_count counter (record_session_tool_call), which
# on_post_tool_call bumps for skill-less turns — the per-invocation tool count
# in echo_signal_event only exists while a skill is active.
# ---------------------------------------------------------------------------


@dataclass
class SessionCandidate:
    session_id: str
    score: int
    reasons: list[str]
    user_turns: int
    tool_calls: int
    has_save_intent: bool
    has_recurrence: bool
    top_similarity: float
    first_message: str
    first_ts: float
    last_ts: float


def list_session_candidates(
    *,
    limit: int = 20,
    min_score: int = SCORE_THRESHOLD,
    recurrence_threshold: float = RECURRENCE_THRESHOLD,
) -> list[SessionCandidate]:
    """Nominate SKILL-LESS conversations as worth turning into a new skill.

    Aggregates echo_user_request_log grouped by session, restricted to
    sessions that have NO echo_skill_invocation row (a skill having run at
    any point means the invocation-scoped list_candidates already covers
    it — we don't want to double-count). Scored with the same weights as
    the invocation path minus tool complexity.
    """
    sql = """
        SELECT
            r.session_id                              AS session_id,
            COUNT(*)                                  AS user_turns,
            MAX(r.save_intent)                        AS has_save_intent,
            MAX(COALESCE(r.recurrence_sim, 0.0))      AS top_similarity,
            MIN(r.ts)                                 AS first_ts,
            MAX(r.ts)                                 AS last_ts,
            COALESCE(
                (SELECT tc.tool_calls FROM echo_session_tool_count tc
                 WHERE tc.session_id = r.session_id), 0
            )                                         AS tool_calls
        FROM echo_user_request_log r
        WHERE r.session_id IS NOT NULL
          AND r.session_id NOT IN (
              SELECT session_id FROM echo_skill_invocation
              WHERE session_id IS NOT NULL
          )
        GROUP BY r.session_id
        ORDER BY MAX(r.ts) DESC
        LIMIT ?
    """
    conn = get_echo_conn()
    rows = conn.execute(sql, (max(limit * 4, 100),)).fetchall()

    candidates: list[SessionCandidate] = []
    for r in rows:
        cand = _build_session_candidate(conn, r, recurrence_threshold, min_score)
        if cand is not None:
            candidates.append(cand)
        if len(candidates) >= limit:
            break
    return candidates


def _build_session_candidate(conn, r, recurrence_threshold: float,
                             min_score: int) -> Optional[SessionCandidate]:
    """Score one aggregate row (from the session-candidate query) into a
    SessionCandidate, or None if it falls below ``min_score``."""
    session_id = r["session_id"]
    user_turns = int(r["user_turns"] or 0)
    tool_calls = int(r["tool_calls"] or 0)
    has_save = bool(r["has_save_intent"])
    top_sim = float(r["top_similarity"] or 0.0)
    has_recurrence = top_sim >= recurrence_threshold

    score = 0
    reasons: list[str] = []
    if has_save:
        score += WEIGHT_SAVE_INTENT
        reasons.append("user expressed save intent")
    if has_recurrence:
        score += WEIGHT_RECURRENCE
        reasons.append(f"task pattern recurred (lexical match {top_sim:.2f})")
    if tool_calls >= THRESHOLD_TOOL_COUNT:
        score += WEIGHT_TOOL_COUNT
        reasons.append(f"high tool-call complexity ({tool_calls} calls)")
    if user_turns >= THRESHOLD_MODIF_ROUNDS:
        score += WEIGHT_MODIF_ROUNDS
        reasons.append(f"high modification investment ({user_turns} turns)")

    if score < min_score:
        return None

    # First user message of the conversation — the dashboard shows it as the
    # candidate's human-readable label, and the nomination flow uses it as the
    # representative task text for dedup + the clarify question.
    first_row = conn.execute(
        "SELECT user_message FROM echo_user_request_log "
        "WHERE session_id = ? ORDER BY ts ASC LIMIT 1",
        (session_id,),
    ).fetchone()
    first_message = (first_row["user_message"] if first_row else "") or ""

    return SessionCandidate(
        session_id=session_id,
        score=score,
        reasons=reasons,
        user_turns=user_turns,
        tool_calls=tool_calls,
        has_save_intent=has_save,
        has_recurrence=has_recurrence,
        top_similarity=top_sim,
        first_message=first_message,
        first_ts=float(r["first_ts"] or 0.0),
        last_ts=float(r["last_ts"] or 0.0),
    )


def evaluate_session(
    session_id: str,
    *,
    min_score: int = SCORE_THRESHOLD,
    recurrence_threshold: float = RECURRENCE_THRESHOLD,
) -> Optional[SessionCandidate]:
    """Score a SINGLE skill-less conversation. None if it doesn't qualify.

    Used by the active-nomination path (m1_nomination) to decide, right
    after a turn is logged, whether this conversation has crossed the
    nomination threshold. Returns None for a session that has invoked any
    skill (covered by the invocation-scoped path) or scores below threshold.
    """
    if not session_id:
        return None
    conn = get_echo_conn()
    skilled = conn.execute(
        "SELECT 1 FROM echo_skill_invocation WHERE session_id = ? LIMIT 1",
        (session_id,),
    ).fetchone()
    if skilled is not None:
        return None
    r = conn.execute(
        """
        SELECT
            ? AS session_id,
            COUNT(*) AS user_turns,
            MAX(save_intent) AS has_save_intent,
            MAX(COALESCE(recurrence_sim, 0.0)) AS top_similarity,
            MIN(ts) AS first_ts,
            MAX(ts) AS last_ts,
            COALESCE(
                (SELECT tool_calls FROM echo_session_tool_count tc
                 WHERE tc.session_id = ?), 0
            ) AS tool_calls
        FROM echo_user_request_log
        WHERE session_id = ?
        """,
        (session_id, session_id, session_id),
    ).fetchone()
    if r is None or int(r["user_turns"] or 0) == 0:
        return None
    return _build_session_candidate(conn, r, recurrence_threshold, min_score)
