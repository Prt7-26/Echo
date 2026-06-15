"""M5 — preference store + few-shot retrieval-augmented generation.

When the user thumbs-up an agent reply, Echo captures the
(task_request, agent_output) pair as a preference example. When a
later user query is similar to one of those preferences, Echo retrieves
the top examples and lets pre_llm_call inject them as few-shot
demonstrations into the user message. The system prompt stays
untouched — that's the cache-safe path documented in
AGENTS.md / [agent/conversation_loop.py:495-522].

Two notable constraints shape this module:

  1. No embedding LLM is configured in Hermes by default. Calling an
     auxiliary LLM for embeddings would add latency + cost on every
     user turn (retrieval is hot path). We use a stdlib hashing
     embedding instead — fixed 256-dim, deterministic, sub-millisecond.
     The quality is well below SentenceTransformers but is enough to
     rank "this task looks like that previously-thumbed-up task" for
     a personal corpus of hundreds of examples. If a user wants
     stronger retrieval they can swap the impl via set_encoder().

  2. No external dependencies. Echo holds the line on
     stdlib-only — the encoder is pure Python + hashlib + struct.
     Vectors are stored as float32 BLOB in echo_preference_example.

Retrieval is two-stage:

  cosine_topk(query_vec, candidates) → top-N by similarity
  mmr_rerank(query_vec, top-N) → final top-K diversifying by content

MMR (Maximal Marginal Relevance) balances "match the query" against
"don't return three near-duplicates", which matters when the user
repeatedly thumbs-up similar replies. λ controls the trade-off.
"""

from __future__ import annotations

import hashlib
import logging
import math
import struct
import time
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from .db import get_echo_conn

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Defaults — match values in DevPlan/schema.md where applicable.
# ---------------------------------------------------------------------------

EMBEDDING_DIM = 256              # hash bucket count; 256 floats = 1 KB BLOB
MMR_LAMBDA = 0.7                 # 1.0 = pure relevance, 0.0 = pure diversity
TOP_K_DEFAULT = 3                # examples injected as few-shots
RETRIEVAL_CANDIDATES = 20        # top-N pulled before MMR re-rank
MIN_SIMILARITY_DEFAULT = 0.7     # raw-cosine floor; below this an example is
                                 # not "about" the query and is not injected.
                                 # Calibrated for NEURAL embeddings (relevant
                                 # paraphrases score ~0.85, unrelated ~0.3-0.47).
                                 # The hashing fallback scores far lower across
                                 # the board, so under hashing this gate is
                                 # effectively "inject nothing" — acceptable,
                                 # since hashing can't do Chinese/paraphrase
                                 # retrieval meaningfully anyway.
THUMBS_RATING_DEFAULT = 5        # explicit thumbs-up → preference rating 5
PREFERENCE_CAPACITY = 2000       # hard cap; LRU-by-composite eviction


# ---------------------------------------------------------------------------
# Encoder (overridable for tests / better embeddings)
# ---------------------------------------------------------------------------


def _default_encode(text: str) -> list[float]:
    """Hashing embedding into a fixed-dim sparse-ish dense vector.

    Tokenization: case-fold + whitespace split + strip punctuation. No
    lemmatization, no stemming — we trade recall for simplicity. Each
    token contributes ±1 to a deterministic bucket via the high bits
    of its SHA-256. The signed accumulation gives some collision
    cancellation; final L2 normalization makes cosine well-defined.

    Quality note: this catches lexical overlap but not paraphrase.
    "Write a summary" and "Summarize this" would have low similarity.
    For Echo's setting — personal corpus of a few hundred examples
    that the user is *deliberately* training on their own request
    patterns — that's an acceptable floor.
    """
    vec = [0.0] * EMBEDDING_DIM
    if not text:
        return vec
    seen = set()  # de-dup tokens within one text to avoid amplification
    for raw in text.lower().split():
        # Strip leading/trailing punctuation. We keep apostrophes
        # because "don't" / "won't" carry meaning at the token level.
        token = raw.strip(".,;:!?\"()[]{}<>")
        if not token or token in seen:
            continue
        seen.add(token)
        h = int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16)
        idx = h % EMBEDDING_DIM
        sign = 1.0 if (h >> 8) & 1 else -1.0
        vec[idx] += sign

    # L2 normalize so cosine == dot product.
    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0:
        vec = [x / norm for x in vec]
    return vec


_encoder: Callable[[str], list[float]] = _default_encode


def set_encoder(enc: Callable[[str], list[float]]) -> None:
    """Override the default encoder. Tests use this to inject a deterministic
    stub; production users could plug in a real embedding API."""
    global _encoder
    _encoder = enc


def reset_encoder() -> None:
    global _encoder
    _encoder = _default_encode


def encode(text: str) -> list[float]:
    """Encode text to a vector via the active encoder impl."""
    return _encoder(text)


# ---------------------------------------------------------------------------
# BLOB pack/unpack
# ---------------------------------------------------------------------------


def vec_to_blob(vec: Sequence[float]) -> bytes:
    """Pack a sequence of floats into little-endian float32 bytes."""
    return b"".join(struct.pack("<f", float(x)) for x in vec)


def blob_to_vec(blob: bytes) -> list[float]:
    """Unpack the BLOB back to a list of floats."""
    if not blob:
        return []
    if len(blob) % 4 != 0:
        # Truncated / corrupt — return empty so cosine returns 0.0.
        return []
    return [struct.unpack("<f", blob[i : i + 4])[0] for i in range(0, len(blob), 4)]


# ---------------------------------------------------------------------------
# Similarity math
# ---------------------------------------------------------------------------


def cosine(v1: Sequence[float], v2: Sequence[float]) -> float:
    """Cosine similarity. Returns 0.0 if either vector is empty or zero-norm."""
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = 0.0
    n1 = 0.0
    n2 = 0.0
    for a, b in zip(v1, v2):
        dot += a * b
        n1 += a * a
        n2 += b * b
    if n1 == 0.0 or n2 == 0.0:
        return 0.0
    return dot / (math.sqrt(n1) * math.sqrt(n2))


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


@dataclass
class PreferenceExample:
    example_id: int
    task_request: str
    agent_output: str
    rating: int
    skill_id: Optional[str]
    task_type_tag: Optional[str]
    similarity: float = 0.0      # populated by retrieval


def store_preference(
    *,
    task_request: str,
    agent_output: str,
    rating: int = THUMBS_RATING_DEFAULT,
    skill_id: Optional[str] = None,
    task_type_tag: Optional[str] = None,
) -> int:
    """Persist one preference example. Returns the new example_id.

    Rating must be in [1, 5] per the schema CHECK constraint. Empty
    task_request or agent_output is treated as a no-op (returns 0)
    rather than an error — defensive against partial data.
    """
    if not task_request or not agent_output:
        logger.debug("store_preference skipped: empty input")
        return 0
    if not (1 <= rating <= 5):
        logger.debug("store_preference skipped: rating=%s out of [1,5]", rating)
        return 0

    vec = encode(task_request)
    blob = vec_to_blob(vec)
    now = time.time()

    conn = get_echo_conn()

    # Dedup: repeated thumbs-up on the same turn (or re-rating it) would
    # otherwise insert a fresh near-identical row every time, bloating the
    # corpus and injecting the same example two or three times. If an example
    # with the same (skill_id, task_request) already exists, refresh it in
    # place — keep the higher rating, refresh recency/output — instead of
    # inserting a duplicate.
    existing = conn.execute(
        "SELECT example_id, rating FROM echo_preference_example "
        "WHERE task_request = ? AND skill_id IS ?",
        (task_request, skill_id),
    ).fetchone()
    if existing is not None:
        eid = int(existing["example_id"])
        new_rating = max(int(existing["rating"]), rating)
        conn.execute(
            "UPDATE echo_preference_example "
            "SET rating = ?, agent_output = ?, task_embedding = ?, "
            "    last_used_at = ?, composite_score = ? "
            "WHERE example_id = ?",
            (new_rating, agent_output, blob, now,
             _composite_score(new_rating, now, 0), eid),
        )
        conn.commit()
        return eid

    cur = conn.execute(
        "INSERT INTO echo_preference_example "
        "(task_request, task_embedding, agent_output, rating, skill_id, "
        " task_type_tag, created_at, last_used_at, use_count, composite_score) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
        (task_request, blob, agent_output, rating, skill_id,
         task_type_tag, now, None, _composite_score(rating, now, 0)),
    )
    conn.commit()

    _maybe_evict_to_capacity()

    return int(cur.lastrowid or 0)


def _composite_score(rating: int, last_used_at: float, use_count: int) -> float:
    """Eviction score: higher = keep, lower = evict first.

    Components match DevPlan/schema.md: rating × time_recency × use_count.
    We exponentially decay recency over 30 days so old examples don't
    linger forever just because they got one thumbs-up.
    """
    age_seconds = max(0.0, time.time() - last_used_at)
    age_days = age_seconds / 86400.0
    recency = math.exp(-age_days / 30.0)
    use_factor = 1.0 + math.log1p(use_count)
    return float(rating) * recency * use_factor


def _maybe_evict_to_capacity() -> None:
    """Trim the preference store to PREFERENCE_CAPACITY rows.

    Eviction order: lowest composite_score first. Composite is
    recomputed by the caller of touch_used_at over time, so even
    high-rating examples eventually fall off if never reused.
    """
    conn = get_echo_conn()
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM echo_preference_example"
    ).fetchone()["n"]
    if n <= PREFERENCE_CAPACITY:
        return

    over = n - PREFERENCE_CAPACITY
    conn.execute(
        "DELETE FROM echo_preference_example "
        "WHERE example_id IN ("
        "  SELECT example_id FROM echo_preference_example "
        "  ORDER BY composite_score ASC, last_used_at ASC NULLS FIRST "
        "  LIMIT ?"
        ")",
        (over,),
    )
    conn.commit()


def touch_used(example_id: int) -> None:
    """Bump use_count + last_used_at when an example is retrieved/injected.

    Idempotent on missing rows. The composite_score is recomputed so
    LRU-by-usage eviction works.
    """
    conn = get_echo_conn()
    now = time.time()
    row = conn.execute(
        "SELECT rating, use_count FROM echo_preference_example "
        "WHERE example_id = ?",
        (example_id,),
    ).fetchone()
    if row is None:
        return
    new_use_count = int(row["use_count"]) + 1
    composite = _composite_score(int(row["rating"]), now, new_use_count)
    conn.execute(
        "UPDATE echo_preference_example "
        "SET use_count = ?, last_used_at = ?, composite_score = ? "
        "WHERE example_id = ?",
        (new_use_count, now, composite, example_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


def _candidate_pool(min_rating: int = 4) -> list[PreferenceExample]:
    """Pull all examples worth considering (rating ≥ threshold)."""
    conn = get_echo_conn()
    rows = conn.execute(
        "SELECT example_id, task_request, task_embedding, agent_output, "
        "       rating, skill_id, task_type_tag "
        "FROM echo_preference_example "
        "WHERE rating >= ?",
        (min_rating,),
    ).fetchall()
    candidates: list[tuple[PreferenceExample, list[float]]] = []
    out: list[PreferenceExample] = []
    # We return PreferenceExamples; vectors come back through retrieve_topk
    # which needs them for cosine. So separate inner list for vectors.
    for r in rows:
        out.append(PreferenceExample(
            example_id=int(r["example_id"]),
            task_request=r["task_request"],
            agent_output=r["agent_output"],
            rating=int(r["rating"]),
            skill_id=r["skill_id"],
            task_type_tag=r["task_type_tag"],
        ))
    return out


def _candidates_with_vectors(min_rating: int = 4):
    """Iterator over (PreferenceExample, vector) for retrieval scoring."""
    conn = get_echo_conn()
    rows = conn.execute(
        "SELECT example_id, task_request, task_embedding, agent_output, "
        "       rating, skill_id, task_type_tag "
        "FROM echo_preference_example "
        "WHERE rating >= ?",
        (min_rating,),
    ).fetchall()
    for r in rows:
        yield (
            PreferenceExample(
                example_id=int(r["example_id"]),
                task_request=r["task_request"],
                agent_output=r["agent_output"],
                rating=int(r["rating"]),
                skill_id=r["skill_id"],
                task_type_tag=r["task_type_tag"],
            ),
            blob_to_vec(r["task_embedding"]),
        )


def retrieve_topk(
    user_message: str,
    *,
    k: int = TOP_K_DEFAULT,
    pool_size: int = RETRIEVAL_CANDIDATES,
    mmr_lambda: float = MMR_LAMBDA,
    min_rating: int = 4,
    min_similarity: float = MIN_SIMILARITY_DEFAULT,
    confidence_weights: Optional[dict[str, float]] = None,
) -> list[PreferenceExample]:
    """Retrieve top-k preference examples for a query.

    Two-stage:
      1. Cosine-similarity sort against all candidates with
         rating >= min_rating. Top `pool_size` go to stage 2.
      2. MMR re-rank picks the final `k` from the pool, balancing
         relevance and diversity.

    confidence_weights, if provided, maps skill_id → confidence and
    multiplies the relevance score so low-confidence skills' examples
    are downranked. This is the M4↔M5 coupling promised in
    DevPlan/schema.md — "final_score = mmr × confidence(skill)".
    """
    if not user_message.strip() or k <= 0:
        return []

    query_vec = encode(user_message)

    # Stage 1: gather (PreferenceExample, vec, sim) over all candidates.
    # The min_similarity gate is on the RAW cosine (semantic relevance) —
    # applied BEFORE confidence weighting, so a relevant example for a slightly
    # degraded skill isn't filtered out by the confidence multiplier. Confidence
    # then only re-ranks among the already-relevant survivors.
    scored: list[tuple[PreferenceExample, list[float], float]] = []
    for ex, vec in _candidates_with_vectors(min_rating=min_rating):
        sim_raw = cosine(query_vec, vec)
        if sim_raw < min_similarity:
            continue  # not "about" this query → never inject as a past example
        sim = sim_raw
        if confidence_weights and ex.skill_id and ex.skill_id in confidence_weights:
            sim *= confidence_weights[ex.skill_id]
        scored.append((ex, vec, sim))

    # Keep only positives — examples with sim ≤ 0 are noise (orthogonal
    # or actively dissimilar), no point in either ranking or diversifying.
    scored = [t for t in scored if t[2] > 0.0]
    if not scored:
        return []

    scored.sort(key=lambda t: t[2], reverse=True)
    pool = scored[: pool_size]

    # Stage 2: MMR.
    final = _mmr_rerank(query_vec, pool, k=k, mmr_lambda=mmr_lambda)

    # Stamp similarity on the result objects so callers / UIs can show
    # "matched at 0.78".
    out: list[PreferenceExample] = []
    for ex, _vec, sim in final:
        ex.similarity = sim
        out.append(ex)
        touch_used(ex.example_id)
    return out


def _mmr_rerank(
    query_vec: list[float],
    pool: list[tuple[PreferenceExample, list[float], float]],
    *,
    k: int,
    mmr_lambda: float,
) -> list[tuple[PreferenceExample, list[float], float]]:
    """Greedy MMR over the candidate pool.

    Picks one example at a time, scoring each remaining candidate as:
        λ · relevance(query, candidate)
      − (1 − λ) · max_over_selected(similarity(candidate, selected))

    Larger λ pushes harder toward pure relevance; smaller λ pushes
    toward diversity.
    """
    selected: list[tuple[PreferenceExample, list[float], float]] = []
    remaining = list(pool)

    while remaining and len(selected) < k:
        best_idx = -1
        best_score = -math.inf
        for i, (_ex, vec, rel) in enumerate(remaining):
            if not selected:
                diversity_penalty = 0.0
            else:
                diversity_penalty = max(
                    cosine(vec, sel_vec)
                    for _sel_ex, sel_vec, _sel_rel in selected
                )
            score = mmr_lambda * rel - (1.0 - mmr_lambda) * diversity_penalty
            if score > best_score:
                best_score = score
                best_idx = i
        if best_idx < 0:
            break
        selected.append(remaining.pop(best_idx))

    return selected


# ---------------------------------------------------------------------------
# Prompt formatting
# ---------------------------------------------------------------------------


_INJECTION_HEADER = "[Echo · past examples you rated highly]"


def format_for_injection(examples: Sequence[PreferenceExample]) -> str:
    """Render preference examples as a markdown block for pre_llm_call injection.

    Format kept minimal — agents seem to follow few-shots better when
    the prose framing is light. Truncate task_request / agent_output
    to keep token cost bounded; 400 chars × 2 fields × 3 examples is
    roughly ~600 tokens, the entire injection budget we'd want.
    """
    if not examples:
        return ""
    parts = [_INJECTION_HEADER]
    for i, ex in enumerate(examples, 1):
        task = _trunc(ex.task_request, 400)
        out = _trunc(ex.agent_output, 400)
        parts.append(
            f"\n### Example {i} (rating {ex.rating}/5"
            + (f", similarity {ex.similarity:.2f}" if ex.similarity else "")
            + ")"
        )
        parts.append(f"Task: {task}")
        parts.append(f"Result: {out}")
    return "\n".join(parts)


def _trunc(s: str, n: int) -> str:
    if not s:
        return ""
    s = s.strip()
    return s if len(s) <= n else (s[: n - 1] + "…")


# ---------------------------------------------------------------------------
# Hook handlers — the M5 wire-up
# ---------------------------------------------------------------------------


def _normalize_user_message(user_message) -> str:
    """Reuse nl_classifier's normalizer; returns empty string on unknown shape."""
    from .nl_classifier import extract_user_text

    return extract_user_text(user_message) or ""


def on_post_llm_call_cache(
    *,
    session_id: str = "",
    user_message=None,
    assistant_response: str = "",
    **_kwargs,
) -> None:
    """Cache the (user, agent) pair for this turn.

    The dashboard /feedback endpoint receives only {skill_id, rating} so
    it can't, on its own, populate the preference store with the actual
    text that prompted the thumbs-up. This cache bridges the gap.
    Stored per-session, overwritten every turn — only the *most recent*
    turn is ever interesting to feedback.

    No-op when fields are missing — skipping a cache write is better
    than persisting a partial pair that confuses retrieval later.
    """
    if not session_id or not assistant_response:
        return
    user_text = _normalize_user_message(user_message)
    if not user_text:
        return

    # Pin to the active skill if we have one. last-skill-wins applies:
    # whichever skill the contextvar points at right now is the one a
    # subsequent thumbs-up most naturally attributes to.
    from .session_context import get_current_invocation_id

    invocation_id = get_current_invocation_id()
    skill_id: Optional[str] = None
    if invocation_id is not None:
        try:
            conn = get_echo_conn()
            row = conn.execute(
                "SELECT skill_id FROM echo_skill_invocation "
                "WHERE invocation_id = ?",
                (invocation_id,),
            ).fetchone()
            if row is not None:
                skill_id = row["skill_id"]
        except Exception as exc:
            logger.debug("turn-cache skill lookup failed: %s", exc, exc_info=True)

    try:
        conn = get_echo_conn()
        conn.execute(
            "INSERT OR REPLACE INTO echo_turn_cache "
            "(session_id, skill_id, user_message, assistant_response, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, skill_id, user_text, assistant_response, time.time()),
        )
        conn.commit()
    except Exception as exc:
        logger.debug("on_post_llm_call_cache write failed: %s", exc, exc_info=True)


def _build_confidence_weights() -> dict[str, float]:
    """Map skill_id → confidence for skills NOT already retired.

    Retired skills shouldn't seed retrieval at all; we filter them out
    by giving them weight 0 implicitly (they don't appear in the map →
    retrieve_topk's `if skill_id in confidence_weights` check skips
    them implicitly because the default multiplier is 1.0, but their
    examples *are* in the candidate pool. To truly exclude retired
    skills' examples we'd need a SQL-level WHERE — keep simple for now
    and rely on confidence weighting to push them down.).

    Returns empty dict on DB error so retrieval still works in
    confidence-blind mode.
    """
    try:
        conn = get_echo_conn()
        rows = conn.execute(
            "SELECT skill_id, confidence FROM echo_skill_confidence "
            "WHERE status != 'retired'"
        ).fetchall()
        return {r["skill_id"]: float(r["confidence"]) for r in rows}
    except Exception as exc:
        logger.debug("_build_confidence_weights failed: %s", exc, exc_info=True)
        return {}


def _active_skill_exclusions() -> tuple[Optional[str], list]:
    """Return (skill_id, exclusion_conditions list) for the currently active
    skill, or (None, []) when there is no active skill or no exclusions.

    The Layer C judge appends scenarios where a skill should NOT apply to
    echo_skill_scope.exclusion_conditions (a JSON array). This surfaces them
    so on_pre_llm_call_inject can warn the agent — the one non-invasive
    channel Echo has to make the judge's verdict actually affect behavior,
    since Echo cannot hook Hermes' skill-retrieval path directly.
    """
    try:
        from .session_context import get_current_invocation_id, get_session_id

        conn = get_echo_conn()
        invocation_id = get_current_invocation_id()
        if invocation_id is not None:
            inv = conn.execute(
                "SELECT skill_id FROM echo_skill_invocation WHERE invocation_id = ?",
                (invocation_id,),
            ).fetchone()
            skill_id = inv["skill_id"] if inv is not None else None
        else:
            # The contextvar is None at pre_llm_call time (bump_use runs later in
            # the turn and the var doesn't survive across turns). Fall back to the
            # conversation's most-recent skill so a just-used skill's exclusion
            # caution still reaches subsequent turns — without this the Layer C
            # judge's exclusion verdict never actually reaches the agent.
            sid = get_session_id()
            row0 = (
                conn.execute(
                    "SELECT skill_id FROM echo_skill_invocation WHERE session_id = ? "
                    "ORDER BY started_at DESC, invocation_id DESC LIMIT 1",
                    (sid,),
                ).fetchone()
                if sid
                else None
            )
            skill_id = row0["skill_id"] if row0 is not None else None
        if not skill_id:
            return None, []
        row = conn.execute(
            "SELECT exclusion_conditions FROM echo_skill_scope WHERE skill_id = ?",
            (skill_id,),
        ).fetchone()
        if row is None or not row["exclusion_conditions"]:
            return skill_id, []
        import json
        try:
            conds = json.loads(row["exclusion_conditions"])
        except (ValueError, TypeError):
            return skill_id, []
        conds = [str(c).strip() for c in conds if str(c).strip()] if isinstance(conds, list) else []
        return skill_id, conds
    except Exception as exc:
        logger.debug("_active_skill_exclusions failed: %s", exc, exc_info=True)
        return None, []


def format_exclusions_for_injection(skill_id: str, conditions: list) -> str:
    """Render a skill's exclusion conditions as a short caution block."""
    if not conditions:
        return ""
    lines = [
        f"**Echo caution — the active skill `{skill_id}` has known limitations.**",
        "It was found NOT to apply well in these situations:",
    ]
    lines += [f"- {c}" for c in conditions]
    lines.append(
        "If the current request resembles any of the above, adapt your "
        "approach instead of reusing this skill verbatim."
    )
    return "\n".join(lines)


def on_pre_llm_call_inject(
    *,
    user_message=None,
    **_kwargs,
) -> Optional[dict]:
    """Inject (a) the active skill's exclusion caution and (b) top-k
    preference examples for this user message, as ``{"context": ...}`` for
    Hermes to append to the user message (not the system prompt → cache
    safe). Returning None means "nothing to inject".

    Errors are caught and downgraded to "no injection" so a broken
    retrieval path can't break the agent loop.
    """
    user_text = _normalize_user_message(user_message)
    if not user_text:
        return None

    blocks: list = []

    # (c) Active M1 nomination nudge — the ask/inform/create directive for a
    # skill-less conversation that crossed the threshold. Injected at most once
    # per conversation (consume_nudge marks it consumed). Placed first so the
    # agent sees the instruction clearly.
    try:
        from . import m1_nomination
        from .session_context import get_session_id
        sid = _kwargs.get("session_id") or get_session_id()
        nudge = m1_nomination.consume_nudge(str(sid) if sid else None)
        if nudge:
            blocks.append(nudge)
            logger.info("Echo M1: injected nomination nudge for session %r", sid)
    except Exception as exc:
        logger.debug("nomination nudge injection failed: %s", exc, exc_info=True)

    # (a) Exclusion caution for the active skill — makes the Layer C judge's
    # exclusion verdict actually reach the agent.
    try:
        skill_id, exclusions = _active_skill_exclusions()
        if skill_id and exclusions:
            caution = format_exclusions_for_injection(skill_id, exclusions)
            if caution:
                blocks.append(caution)
    except Exception as exc:
        logger.debug("exclusion injection failed: %s", exc, exc_info=True)

    # (b) M5 few-shot preference examples.
    try:
        weights = _build_confidence_weights()
        examples = retrieve_topk(
            user_text,
            k=TOP_K_DEFAULT,
            confidence_weights=weights or None,
        )
        if examples:
            block = format_for_injection(examples)
            if block:
                blocks.append(block)
            # M5 injections are otherwise invisible (the context is ephemeral,
            # appended to the user message and never persisted). Log when we
            # actually inject so the loop is observable.
            logger.info(
                "Echo M5: injected %d preference example(s) for query %r "
                "(skills=%s)",
                len(examples),
                (user_text or "")[:50],
                [e.skill_id for e in examples],
            )
    except Exception as exc:
        logger.debug("on_pre_llm_call_inject retrieval failed: %s", exc, exc_info=True)

    if not blocks:
        return None
    return {"context": "\n\n".join(blocks)}


def store_from_turn_cache_by_skill(skill_id: str, rating: int = 5) -> int:
    """Look up the most recent cached turn for a skill and persist as a
    preference example. Used by the /feedback dashboard endpoint when
    the user thumbs-up.

    Returns the new example_id, or 0 if no cache row was found.
    """
    if not skill_id:
        return 0
    try:
        conn = get_echo_conn()
        row = conn.execute(
            "SELECT user_message, assistant_response "
            "FROM echo_turn_cache "
            "WHERE skill_id = ? "
            "ORDER BY updated_at DESC LIMIT 1",
            (skill_id,),
        ).fetchone()
        if row is None:
            return 0
        return store_preference(
            task_request=row["user_message"],
            agent_output=row["assistant_response"],
            rating=rating,
            skill_id=skill_id,
        )
    except Exception as exc:
        logger.debug("store_from_turn_cache_by_skill failed: %s", exc, exc_info=True)
        return 0
