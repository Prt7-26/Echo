"""Layer C — independent LLM judge for skills entering review.

When the confidence-update engine transitions a skill from ``active`` to
``pending_review`` (proposal §M3 Layer C), we kick off a fire-and-forget
diagnostic via Hermes' auxiliary LLM. The judge looks at the skill's
recent behavior and decides one of three verdicts:

  * ``ok``         — the dip is likely noise; no further action.
  * ``degraded``   — the skill itself has quality problems; apply
                     another drift_detected step so confidence keeps
                     falling toward retirement.
  * ``exclusion``  — the skill is fine in general but mis-applied to
                     a context it shouldn't cover. Append the named
                     context to echo_skill_scope.exclusion_conditions
                     so future routing avoids it.

Why a separate LLM call instead of reusing the main agent / classifier?
proposal §M3 Layer C explicitly calls out the same-source bias risk —
Echo's whole point is to escape it. Hermes' auxiliary task system lets
users point ``echo_judge`` at a different model family than the main
agent (e.g. main = local model, judge = Anthropic API). If they don't
configure that explicitly, we still get a meaningful second opinion via
``temperature=0`` and structured-JSON output, which dampens the
sycophancy mode of the same family.

Same fail-soft pattern as Layer B: any error returns ``ok`` (no action)
so a broken judge can't make confidence worse. Same test-injection
pattern: ``set_judge_impl(...)`` overrides the real LLM call.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Literal, Optional

logger = logging.getLogger(__name__)

Verdict = Literal["ok", "degraded", "exclusion"]


@dataclass
class JudgeVerdict:
    verdict: Verdict
    reason: Optional[str] = None       # populated for "degraded"
    context: Optional[str] = None      # populated for "exclusion"


# Number of recent invocations summarized for the judge prompt.
JUDGE_HISTORY_WINDOW = 8


JUDGE_PROMPT = """\
You are an independent quality auditor for an AI skill management system. \
A skill named "{skill_id}" has dropped below the confidence threshold \
for active use, so it has been routed to you for review.

Recent behavior signals (last {n} invocations):
{signal_summary}

Confidence is now {confidence:.3f} (threshold for review: 0.30).

Choose EXACTLY one verdict:

  1. ok — the dip is most likely noise from natural workflow variation.
     The skill itself is fine; no further action.

  2. degraded — the skill has quality problems that the recent signals
     are accurately revealing. Recommend further confidence reduction.

  3. exclusion — the skill is fine for its ORIGINAL purpose but recent
     signals show it's being applied in a context it wasn't designed
     for. Name the specific context to exclude.

Respond as a single JSON object on one line, no markdown:

  {{"verdict": "ok"}}
  {{"verdict": "degraded", "reason": "<one-sentence reason>"}}
  {{"verdict": "exclusion", "context": "<short context description>"}}
"""


# ---------------------------------------------------------------------------
# Signal summary builder
# ---------------------------------------------------------------------------


def _summarize_recent_signals(
    skill_id: str,
    n_invocations: int = JUDGE_HISTORY_WINDOW,
) -> str:
    """Render the last N invocations' aggregate signal counts as plain text.

    Format is intentionally LLM-friendly: one line per invocation, with
    counts of each signal type. We don't include free-form value_text
    here to keep the prompt cheap; the judge decides based on shape, not
    content.
    """
    from .db import get_echo_conn

    conn = get_echo_conn()
    invs = conn.execute(
        "SELECT invocation_id, started_at, finished_at "
        "FROM echo_skill_invocation "
        "WHERE skill_id = ? "
        "ORDER BY started_at DESC LIMIT ?",
        (skill_id, n_invocations),
    ).fetchall()

    if not invs:
        return "(no invocations recorded)"

    lines = []
    for i, inv in enumerate(invs, 1):
        events = conn.execute(
            "SELECT signal_type, COUNT(*) AS n "
            "FROM echo_signal_event "
            "WHERE invocation_id = ? "
            "GROUP BY signal_type",
            (inv["invocation_id"],),
        ).fetchall()
        counts = {r["signal_type"]: r["n"] for r in events}
        # Stable column order in the output makes the prompt cache-friendly.
        summary = ", ".join(
            f"{t}={counts.get(t, 0)}"
            for t in (
                "user_turn",
                "tool_call",
                "session_ended",
                "explicit_positive",
                "explicit_negative",
                "nl_positive",
                "nl_negative",
            )
        )
        lines.append(f"  #{i}: {summary}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Default judge implementation
# ---------------------------------------------------------------------------


def _default_judge_impl(
    skill_id: str,
    confidence: float,
) -> JudgeVerdict:
    """Run the prompt through Hermes' auxiliary LLM client.

    task='echo_judge' lets users point a different model family at this
    work via config.yaml (auxiliary.echo_judge.{provider,model}). If
    unconfigured, falls back through Hermes' standard resolution chain.
    """
    from agent.auxiliary_client import call_llm

    summary = _summarize_recent_signals(skill_id)
    prompt = JUDGE_PROMPT.format(
        skill_id=skill_id,
        n=JUDGE_HISTORY_WINDOW,
        signal_summary=summary,
        confidence=confidence,
    )
    response = call_llm(
        task="echo_judge",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=120,
        temperature=0.0,
    )
    text = response.choices[0].message.content
    if not isinstance(text, str):
        return JudgeVerdict(verdict="ok")
    return _parse_verdict(text)


def _parse_verdict(text: str) -> JudgeVerdict:
    """Tolerant JSON extraction. Models sometimes wrap output in prose."""
    candidate = text.strip()
    # If the model wrapped JSON in code fences, strip them.
    if candidate.startswith("```"):
        # Remove first line and last fence.
        lines = candidate.split("\n")
        candidate = "\n".join(lines[1:-1]) if len(lines) >= 3 else candidate
    # Find the JSON object by braces.
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start < 0 or end < 0 or end < start:
        return JudgeVerdict(verdict="ok")
    try:
        obj = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError:
        return JudgeVerdict(verdict="ok")

    verdict_raw = obj.get("verdict")
    if verdict_raw not in ("ok", "degraded", "exclusion"):
        return JudgeVerdict(verdict="ok")
    return JudgeVerdict(
        verdict=verdict_raw,  # type: ignore[arg-type]
        reason=obj.get("reason") if verdict_raw == "degraded" else None,
        context=obj.get("context") if verdict_raw == "exclusion" else None,
    )


_judge_impl: Callable[[str, float], JudgeVerdict] = _default_judge_impl


def set_judge_impl(impl: Callable[[str, float], JudgeVerdict]) -> None:
    global _judge_impl
    _judge_impl = impl


def reset_judge_impl() -> None:
    global _judge_impl
    _judge_impl = _default_judge_impl


# ---------------------------------------------------------------------------
# Verdict handling — writes back to the DB
# ---------------------------------------------------------------------------


def process_verdict(skill_id: str, verdict: JudgeVerdict) -> None:
    """Apply the judge's decision to Echo's state.

    Three paths:

      * ok        → no-op. (We could optionally bump confidence back up
                    here to "clear" the pending_review state, but that
                    feels too autocratic for a single judge call — leave
                    recovery to user feedback signals.)
      * degraded  → another drift_detected step pushes confidence further
                    down, accelerating retirement.
      * exclusion → append context to echo_skill_scope.exclusion_conditions.
                    Per AGENTS.md prompt-cache invariant, this change is
                    *deferred* — it lands in the DB now but the in-flight
                    session's system prompt is not edited mid-flight.
                    Next session's prompt builder will see it.
    """
    if verdict.verdict == "ok":
        return

    if verdict.verdict == "degraded":
        from . import confidence as conf_mod

        try:
            conf_mod.update_confidence(skill_id, "drift_detected", severity=2.0)
        except Exception as exc:
            logger.debug(
                "judge degraded follow-up failed for %s: %s",
                skill_id, exc, exc_info=True,
            )
        return

    if verdict.verdict == "exclusion":
        ctx = (verdict.context or "").strip()
        if not ctx:
            return
        try:
            from .db import get_echo_conn

            conn = get_echo_conn()
            now = time.time()
            # Ensure scope row exists.
            row = conn.execute(
                "SELECT exclusion_conditions FROM echo_skill_scope "
                "WHERE skill_id = ?",
                (skill_id,),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO echo_skill_scope "
                    "(skill_id, scope_level, exclusion_conditions, "
                    " created_at, updated_at) "
                    "VALUES (?, 'unknown', ?, ?, ?)",
                    (skill_id, json.dumps([ctx]), now, now),
                )
            else:
                existing_raw = row["exclusion_conditions"] or "[]"
                try:
                    existing = json.loads(existing_raw)
                    if not isinstance(existing, list):
                        existing = []
                except json.JSONDecodeError:
                    existing = []
                if ctx not in existing:
                    existing.append(ctx)
                conn.execute(
                    "UPDATE echo_skill_scope "
                    "SET exclusion_conditions = ?, updated_at = ? "
                    "WHERE skill_id = ?",
                    (json.dumps(existing), now, skill_id),
                )
            conn.commit()
        except Exception as exc:
            logger.debug(
                "judge exclusion write failed for %s: %s",
                skill_id, exc, exc_info=True,
            )


# ---------------------------------------------------------------------------
# Public entry — fire-and-forget
# ---------------------------------------------------------------------------


def run_judge(skill_id: str, confidence: float) -> JudgeVerdict:
    """Synchronous judge run with broad exception swallowing.

    Returns a "ok" verdict on any error. Caller treats ok as "no
    action", so a broken judge degrades gracefully into "skill stays in
    pending_review until signals push it further".

    Also honours ``aux_config``: when Layer C is disabled (echo.aux_mode
    = "off", or "separate" with no separate config), returns "ok" without
    making any LLM call.
    """
    try:
        from . import aux_config
        if not aux_config.judge_enabled():
            return JudgeVerdict(verdict="ok")
    except Exception as exc:
        logger.debug("Echo aux_config check failed: %s", exc, exc_info=True)
        return JudgeVerdict(verdict="ok")
    try:
        return _judge_impl(skill_id, confidence)
    except Exception as exc:
        logger.debug("Echo judge failed for %s: %s", skill_id, exc, exc_info=True)
        return JudgeVerdict(verdict="ok")


def start_judge_async(
    skill_id: str,
    confidence: float,
    on_done: Optional[Callable[[JudgeVerdict], None]] = None,
) -> threading.Thread:
    """Fire-and-forget judge. on_done (optional) is called with the
    verdict after process_verdict has already run; useful in tests."""

    def _worker():
        verdict = run_judge(skill_id, confidence)
        try:
            process_verdict(skill_id, verdict)
        except Exception as exc:
            logger.debug(
                "process_verdict failed for %s: %s", skill_id, exc, exc_info=True,
            )
        if on_done is not None:
            try:
                on_done(verdict)
            except Exception as exc:
                logger.debug(
                    "judge on_done callback failed: %s", exc, exc_info=True,
                )

    t = threading.Thread(target=_worker, name="echo_judge", daemon=True)
    t.start()
    return t
