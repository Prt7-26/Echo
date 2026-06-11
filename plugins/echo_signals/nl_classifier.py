"""Layer B — natural-language sentiment classification.

When the user replies to an agent turn, we want to know whether that
reply expresses *satisfaction* with the previous output, *dissatisfaction*,
or neither. The classification is the Layer B signal that drives the
``nl_positive`` and ``nl_negative`` rules in confidence.py — half the
step of explicit thumbs (which makes sense; ambient inference is less
reliable than an explicit click).

How is this Layer B (external signal) and not Layer A self-evaluation?
Because the LLM here is classifying *the user's words*, not judging the
agent's own previous output. The judgment surface is user-authored text,
which is genuinely external to Echo. The cost: one short auxiliary LLM
call per user turn — small but not zero, so this runs fire-and-forget
on a daemon thread so it can't block the main agent loop.

Two design points from DevPlan/proposal.tex §Challenge 2:

  1. **Conservative by default**: ambiguous user replies (e.g. "again",
     "different angle", "actually") are not negative feedback in the
     classification sense — they're new instructions. The prompt biases
     the classifier toward `neutral` so a borderline phrase doesn't
     unfairly hit confidence.
  2. **Failure → neutral**: any error (LLM call fails, response shape
     surprising, no auxiliary configured) returns "neutral" rather than
     a default that has signal direction. Layer B silence isn't a vote.

Tests can override _classifier_impl to inject a deterministic answer
without touching the real LLM.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Literal, Optional

logger = logging.getLogger(__name__)

Label = Literal["positive", "negative", "neutral"]


CLASSIFIER_PROMPT = """\
You are classifying user feedback to an AI assistant. The user just \
replied to the assistant's previous turn. Decide whether the user's \
message expresses satisfaction or dissatisfaction with the *previous output*.

Three labels:

  positive: User expresses clear satisfaction with the previous output.
            Examples: "perfect", "thanks, that's great", "exactly what
            I needed", "looks good".

  negative: User expresses clear dissatisfaction with the previous
            output's quality. Examples: "no, that's wrong", "this
            doesn't work", "you misunderstood", "that's not right".

  neutral:  Everything else. Including new requests, follow-up
            instructions, refinements, clarifications, questions, and
            anything ambiguous. "Try again", "different angle", "make
            it shorter" are NEUTRAL — they're directing further work,
            not judging quality. When in doubt, return neutral.

Respond with exactly one word: positive, negative, or neutral.

USER MESSAGE:
{message}

LABEL:"""


# ---------------------------------------------------------------------------
# User message → text extraction
# ---------------------------------------------------------------------------


def extract_user_text(user_message: Any) -> Optional[str]:
    """Normalize Hermes' user_message kwarg into a plain string.

    Hermes' pre_llm_call passes user_message in several shapes depending
    on which branch and which platform: a raw string, a dict like
    {"role": "user", "content": "..."}, or sometimes a list of content
    parts. We accept all three and bail (return None) on anything
    surprising — calling code treats None as "skip classification".
    """
    if user_message is None:
        return None
    if isinstance(user_message, str):
        text = user_message.strip()
        return text or None

    if isinstance(user_message, dict):
        content = user_message.get("content")
        if isinstance(content, str):
            text = content.strip()
            return text or None
        if isinstance(content, list):
            # OpenAI multi-modal: pluck out text parts and concatenate.
            parts = []
            for p in content:
                if isinstance(p, dict) and p.get("type") == "text":
                    t = p.get("text")
                    if isinstance(t, str):
                        parts.append(t)
                elif isinstance(p, str):
                    parts.append(p)
            text = " ".join(parts).strip()
            return text or None

    return None


# ---------------------------------------------------------------------------
# The actual classify call — overridable for tests
# ---------------------------------------------------------------------------


def _default_classifier_impl(text: str) -> Label:
    """Run the prompt through Hermes' auxiliary LLM client.

    task="echo_classifier" lets a user point a cheap model at this work
    via auxiliary.echo_classifier.{provider, model, max_tokens} in
    config.yaml. If unconfigured, Hermes' auxiliary client falls back
    to the global auxiliary defaults, then to the main agent provider.
    No config at all → RuntimeError → "neutral" via the wrapper below.
    """
    from agent.auxiliary_client import call_llm

    response = call_llm(
        task="echo_classifier",
        messages=[
            {"role": "user", "content": CLASSIFIER_PROMPT.format(message=text)},
        ],
        max_tokens=8,
        temperature=0.0,
    )
    # OpenAI-style response shape.
    out = response.choices[0].message.content
    if not isinstance(out, str):
        return "neutral"
    out = out.strip().lower()
    # The model occasionally embeds the label in a sentence or wraps it.
    # Look for whole-word matches, not substring, to avoid "this is
    # positive feedback" being labeled positive when the model meant to
    # describe what positive means.
    for label in ("positive", "negative", "neutral"):
        if out == label or out.startswith(label + " ") or out.startswith(label + ".") or out.startswith(label + ","):
            return label  # type: ignore[return-value]
    return "neutral"


# Module-level handle; tests overwrite this with a deterministic stub.
_classifier_impl: Callable[[str], Label] = _default_classifier_impl


def set_classifier_impl(impl: Callable[[str], Label]) -> None:
    """Test/integration hook to inject a fake classifier."""
    global _classifier_impl
    _classifier_impl = impl


def reset_classifier_impl() -> None:
    global _classifier_impl
    _classifier_impl = _default_classifier_impl


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify(text: str) -> Label:
    """Synchronous classify with broad exception swallowing.

    Returns "neutral" on any failure — no LLM configured, transient
    error, surprising response, prompt-cache wonk, anything. Layer B
    should never throw an error up into the Hermes pre_llm_call path.

    Also honours the user's choice via ``aux_config``: if Layer B is
    disabled (echo.aux_mode = "off", or "separate" with no separate
    config), classify becomes a no-op that returns "neutral" — sacred
    invariant preserved, no LLM call made, no API credit spent.
    """
    if not text or not text.strip():
        return "neutral"
    try:
        from . import aux_config
        if not aux_config.classifier_enabled():
            return "neutral"
    except Exception as exc:
        logger.debug("Echo aux_config check failed: %s", exc, exc_info=True)
        # On any check failure default to OFF (safer than burning credit).
        return "neutral"
    try:
        return _classifier_impl(text)
    except Exception as exc:
        logger.debug("Echo nl_classifier failed: %s", exc, exc_info=True)
        return "neutral"


def classify_async(
    text: str,
    on_result: Callable[[Label], None],
) -> Optional[threading.Thread]:
    """Fire-and-forget classification.

    Returns the daemon thread (useful in tests for ``.join()``); the
    main agent loop never waits on it. on_result is invoked with the
    final label; if on_result itself raises, the exception is logged
    and swallowed so a broken downstream callback can't be observed
    by the worker.
    """
    if not text or not text.strip():
        return None

    def _worker():
        label = classify(text)
        try:
            on_result(label)
        except Exception as exc:
            logger.debug("Echo nl_classifier callback failed: %s", exc, exc_info=True)

    t = threading.Thread(target=_worker, name="echo_nl_classifier", daemon=True)
    t.start()
    return t
