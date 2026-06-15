"""Layer B+ — LLM scoring of the free-text *reason* a user attaches to a rating.

When a user thumbs-up / thumbs-down a skill AND writes a reason, the reason
carries far more signal than the ±1 click alone. Instead of treating the
reason as a mere boolean ("did they bother to explain?"), we run it through
an auxiliary LLM that reads the *words* and scores how strongly they endorse
or criticise the skill, on a signed integer scale −5..+5.

That score normalises straight onto confidence (see plugin_api.submit_feedback
+ confidence._apply_rule): the click applies its base α/β immediately, then this
score adds a graded same-direction step of magnitude ``|score|/5``. Because the
follow-up step follows the SCORE's sign — not the click's — a reason that
contradicts the button (a thumbs-up whose text is actually a complaint) pulls
confidence back proportionally. The user chose this "trust the words, correct
proportionally" behaviour explicitly.

Why a separate auxiliary LLM (echo_reason_score) and not the main agent?
Same reasoning as Layer B / Layer C (nl_classifier / judge): the judgment
surface is *the user's own text*, and scoring it on a cheap second model
keeps it external to the agent it grades. With no separate provider
configured, Hermes' auxiliary client falls back to the main provider — that
is the intended default for users who only set one key.

Fail-soft, exactly like its siblings: any error — no aux LLM reachable,
surprising response, channel disabled via echo.aux_mode — yields score 0
(no extra confidence movement; the base click still stands). Tests inject a
deterministic impl via ``set_reason_score_impl``.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


SCORE_MIN = -5
SCORE_MAX = 5


@dataclass
class ReasonScore:
    """Result of scoring one reason.

    score:     signed integer in [-5, +5]. >0 endorses the skill, <0 criticises,
               0 = no clear / off-topic / ambiguous signal.
    rationale: one short clause from the LLM (audit only).
    """

    score: int
    rationale: Optional[str] = None


REASON_SCORE_PROMPT = """\
You are scoring a user's written reason that accompanies their thumbs-up or \
thumbs-down on an AI assistant's *skill* — a reusable saved workflow the \
assistant can apply to similar future tasks.

Skill being rated: {skill_id}
The user's button: {direction}
The user's written reason:
\"\"\"
{reason}
\"\"\"

Your job: score how the user's WORDS judge THIS skill's usefulness / output \
quality, as an integer from -5 to +5. Score the words themselves — your score \
may legitimately DISAGREE with the button (a thumbs-up whose text is actually a \
complaint scores negative, and vice-versa).

Follow these THREE STEPS, then apply the rubric. Be consistent: the same kind \
of comment must always get the same score.

STEP 1 — POLARITY. Is the comment praising the output, criticising it, or \
neither? Lean toward detecting polarity the way a person naturally would: a \
casual or hedged evaluative remark STILL carries polarity. "不错", "还行", \
"画得还行", "凑合", "挺好", "fine", "not bad", "could be better", "一般" all \
EXPRESS an opinion about the output → they are mild praise or mild criticism, \
NOT "neither". Reserve "neither" (→ 0) ONLY for content with no evaluative \
lean at all: a new instruction/refinement ("再画一版", "make it shorter"), a \
question, a purely factual remark, or off-topic chatter. If — and only if — \
the comment genuinely passes no judgment on the output, score 0 and stop.

STEP 2 — STRENGTH. How forceful is the sentiment? Emphatic/superlative \
("太棒了", "完全没用", "perfect", "totally broken") = strong. Plain/hedged \
("不错", "一般", "还行", "not bad") = mild. Barely-there = faint.

STEP 3 — SPECIFICITY. Does it cite a concrete reason (names what was good/bad: \
"配色专业", "箭头画错了") or is it generic ("挺好的", "不太行")? Specific \
comments are stronger evidence — push one band further from 0.

RUBRIC (pick the band that matches polarity + strength + specificity):

  +5 / +4  Strong praise. Emphatic OR specific approval; clearly states the \
output was excellent / exactly right. e.g. "层次清晰、配色专业，正是我要的"; \
"夯爆了"; "perfect, exactly what I needed".
  +3 / +2  Mild praise. Generic or hedged positive. e.g. "不错"; "还行"; \
"挺好的"; "画得还行"; "现在还不错"; "凑合"; "looks fine"; "not bad".
  +1       Faint positive — barely a lean to approval.
   0       NO evaluative lean at all — a pure instruction, question, factual \
remark, or off-topic chatter. Do NOT use 0 just because a comment is vague or \
hedged: a hedged opinion is still ±2. Only score 0 when there is genuinely no \
judgment of the output.
  -1       Faint negative — barely a lean to disapproval.
  -2 / -3  Mild criticism. Generic or hedged negative. e.g. "不太好看"; \
"一般般"; "感觉不太对"; "could be better"; "meh".
  -4 / -5  Strong criticism. Emphatic OR specific disapproval; clearly states \
it failed / is wrong. e.g. "箭头全错了，根本没用"; "完全不对"; "this is broken".

EDGE RULES:
  * Mixed praise + criticism → net it out: if balanced, score near 0; \
otherwise take the dominant side at reduced strength.
  * Profanity/slang used as emphatic praise ("牛逼", "夯爆了") → strong positive.
  * Length is not strength — a short "完美" is still strong praise.

Respond with ONLY a JSON object and nothing else (rationale ≤ 15 words, in the \
same language as the reason, naming the band you chose):
  {{"score": <integer from -5 to 5>, "rationale": "<band + why>"}}
"""


# ---------------------------------------------------------------------------
# The actual scoring call — overridable for tests
# ---------------------------------------------------------------------------


def _default_reason_score_impl(
    direction: str, skill_id: str, reason: str
) -> ReasonScore:
    """Run the prompt through Hermes' auxiliary LLM client.

    task="echo_reason_score" lets a user point a separate (cheaper / different
    family) model at this via auxiliary.echo_reason_score.* in config.yaml.
    Unconfigured → Hermes' auxiliary client falls back to the global aux
    defaults, then to the main agent provider — the intended single-key default.
    """
    from agent.auxiliary_client import call_llm

    prompt = REASON_SCORE_PROMPT.format(
        skill_id=skill_id,
        direction=("thumbs-up (👍)" if direction == "up" else "thumbs-down (👎)"),
        reason=reason,
    )
    response = call_llm(
        task="echo_reason_score",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=80,
        # Deterministic: this is a single scoring call, not a vote.
        temperature=0.0,
    )
    text = response.choices[0].message.content
    if not isinstance(text, str):
        return ReasonScore(score=0)
    return _parse_score(text)


def _parse_score(text: str) -> ReasonScore:
    """Tolerant JSON extraction; clamp the score to [-5, 5]. Any failure → 0."""
    candidate = text.strip()
    if candidate.startswith("```"):
        lines = candidate.split("\n")
        candidate = "\n".join(lines[1:-1]) if len(lines) >= 3 else candidate
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start < 0 or end < 0 or end < start:
        return ReasonScore(score=0)
    try:
        obj = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError:
        return ReasonScore(score=0)
    raw = obj.get("score")
    try:
        score = int(round(float(raw)))
    except (TypeError, ValueError):
        return ReasonScore(score=0)
    score = max(SCORE_MIN, min(SCORE_MAX, score))
    rationale = obj.get("rationale")
    if rationale is not None and not isinstance(rationale, str):
        rationale = None
    return ReasonScore(score=score, rationale=rationale)


# Module-level handle; tests overwrite this with a deterministic stub.
_reason_score_impl: Callable[[str, str, str], ReasonScore] = _default_reason_score_impl


def set_reason_score_impl(impl: Callable[[str, str, str], ReasonScore]) -> None:
    """Test/integration hook to inject a fake reason scorer."""
    global _reason_score_impl
    _reason_score_impl = impl


def reset_reason_score_impl() -> None:
    global _reason_score_impl
    _reason_score_impl = _default_reason_score_impl


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def score_reason(direction: str, skill_id: str, reason: str) -> ReasonScore:
    """Synchronous score with broad exception swallowing.

    Returns score 0 on any failure — no LLM configured, transient error,
    surprising response, channel disabled. A 0 means "no extra confidence
    movement"; the base thumb click still stands.

    Honours echo.aux_mode via aux_config: if the reason-score channel is
    disabled (mode "off", or "separate" with no separate config) this is a
    no-op returning 0 — no LLM call, no API credit spent.
    """
    if not reason or not reason.strip():
        return ReasonScore(score=0)
    try:
        from . import aux_config

        if not aux_config.reason_scorer_enabled():
            return ReasonScore(score=0)
    except Exception as exc:
        logger.debug("Echo aux_config check failed: %s", exc, exc_info=True)
        return ReasonScore(score=0)
    try:
        return _reason_score_impl(direction, skill_id, reason)
    except Exception as exc:
        logger.debug("Echo reason_scorer failed: %s", exc, exc_info=True)
        return ReasonScore(score=0)


def score_reason_async(
    direction: str,
    skill_id: str,
    reason: str,
    on_result: Callable[[ReasonScore], None],
) -> Optional[threading.Thread]:
    """Fire-and-forget reason scoring on a daemon thread.

    Returns the thread (useful in tests for ``.join()``); the caller — the
    dashboard /feedback handler — never blocks on the LLM round-trip. on_result
    is invoked with the ReasonScore; a raising callback is logged and swallowed.
    """
    if not reason or not reason.strip():
        return None

    def _worker() -> None:
        result = score_reason(direction, skill_id, reason)
        try:
            on_result(result)
        except Exception as exc:
            logger.debug(
                "Echo reason_scorer callback failed: %s", exc, exc_info=True
            )

    t = threading.Thread(target=_worker, name="echo_reason_scorer", daemon=True)
    t.start()
    return t
