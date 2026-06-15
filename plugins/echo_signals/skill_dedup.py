"""Skill-library deduplication check for M1 new-skill nomination.

Before Echo proposes turning a conversation into a brand-new skill, it asks
an auxiliary LLM whether the skill library *already* contains a skill that
would cover the same task. This prevents the nominator from suggesting
near-duplicates of skills the user already has.

Decision matrix (driven by m1_nomination):

  * user explicitly said "save as a skill" (save_intent) + a similar skill
    exists  → tell the user about the existing skill (don't silently create).
  * save_intent + nothing similar → nominate / create directly.
  * an implicit condition fired (recurrence / tool-count / modification) and
    a similar skill exists → silently skip (no question — the need is
    already met).
  * implicit + nothing similar → ask the user via the clarify tool.

The LLM lift is the same shape as judge.py / nl_classifier.py: a
fire-and-forget aux call behind the ``echo_skill_dedup`` task, gated by
aux_config so it never fires when no auxiliary model is configured, and
test-injectable via ``set_dedup_impl``. Fail-soft: any error returns "no
match" so a broken dedup check never blocks a legitimate nomination.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SkillInfo:
    name: str
    description: str


@dataclass
class DedupResult:
    """Outcome of the dedup check.

    ``match`` is the name of an existing skill that already covers the task,
    or None when the library has nothing comparable. ``reason`` is the LLM's
    one-line justification (shown to the user when we inform them).
    """
    match: Optional[str]
    reason: Optional[str] = None


# Hard cap on how many skills we enumerate into the prompt. The library is
# small in practice; this guards against a pathological install.
MAX_SKILLS_IN_PROMPT = 80


DEDUP_PROMPT = """\
You are the librarian of an AI agent's skill library. A new task pattern has \
been observed and someone is considering creating a NEW skill for it. Your job \
is to decide whether the library ALREADY contains a skill that would handle \
this task, so we don't create a near-duplicate.

The task pattern (what the user kept asking for):
---
{task_text}
---

Existing skills in the library (name — description):
{skill_list}

Decide: does an existing skill already cover this task well enough that \
creating a new one would be redundant? Be conservative — only report a match \
when the existing skill's PURPOSE genuinely overlaps the task, not merely \
because some words coincide. A skill that does a *different* job in the same \
domain is NOT a match.

Respond with ONLY a JSON object, no prose:
  {{"match": "<exact-skill-name>", "reason": "<one short sentence>"}}
or, if nothing in the library covers it:
  {{"match": null}}
"""


# ---------------------------------------------------------------------------
# Skill-library enumeration
# ---------------------------------------------------------------------------


def enumerate_skills() -> List[SkillInfo]:
    """Read every SKILL.md in the library and return (name, description).

    Walks all skill dirs (local ~/.hermes/skills + external) via Hermes'
    own skill_utils so Echo and Hermes agree on what counts as a skill.
    Fail-soft: any unreadable skill is skipped, never raised.
    """
    out: List[SkillInfo] = []
    try:
        from agent.skill_utils import (
            extract_skill_description,
            get_all_skills_dirs,
            iter_skill_index_files,
            parse_frontmatter,
        )
    except Exception as exc:  # pragma: no cover - import guard
        logger.debug("enumerate_skills: skill_utils import failed: %s", exc)
        return out

    seen: set[str] = set()
    for skills_dir in get_all_skills_dirs():
        try:
            for path in iter_skill_index_files(skills_dir, "SKILL.md"):
                try:
                    content = path.read_text(encoding="utf-8", errors="replace")
                    fm, _ = parse_frontmatter(content)
                    name = str(fm.get("name", "")).strip()
                    if not name or name in seen:
                        continue
                    seen.add(name)
                    desc = extract_skill_description(fm) or ""
                    out.append(SkillInfo(name=name, description=desc))
                except Exception as exc:
                    logger.debug("enumerate_skills: skip %s: %s", path, exc)
        except Exception as exc:
            logger.debug("enumerate_skills: skip dir %s: %s", skills_dir, exc)
    return out


# ---------------------------------------------------------------------------
# Dedup check (auxiliary LLM)
# ---------------------------------------------------------------------------


def _parse_dedup(text: str, valid_names: set[str]) -> DedupResult:
    """Tolerant JSON extraction; validates the match against real skill names."""
    if not isinstance(text, str):
        return DedupResult(match=None)
    candidate = text.strip()
    if candidate.startswith("```"):
        lines = candidate.split("\n")
        candidate = "\n".join(lines[1:-1]) if len(lines) >= 3 else candidate
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start < 0 or end < 0 or end < start:
        return DedupResult(match=None)
    try:
        obj = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError:
        return DedupResult(match=None)
    match = obj.get("match")
    if not isinstance(match, str) or not match.strip():
        return DedupResult(match=None)
    match = match.strip()
    # Guard against the LLM inventing a skill name that isn't in the library.
    if match not in valid_names:
        logger.debug("dedup: LLM named unknown skill %r; treating as no match", match)
        return DedupResult(match=None)
    reason = obj.get("reason")
    return DedupResult(match=match, reason=reason if isinstance(reason, str) else None)


def _default_dedup_impl(task_text: str, skills: List[SkillInfo]) -> DedupResult:
    """Run the dedup prompt through Hermes' auxiliary LLM (echo_skill_dedup)."""
    from agent.auxiliary_client import call_llm

    listed = skills[:MAX_SKILLS_IN_PROMPT]
    skill_list = "\n".join(
        f"- {s.name} — {s.description or '(no description)'}" for s in listed
    ) or "(the library is empty)"
    prompt = DEDUP_PROMPT.format(task_text=task_text.strip(), skill_list=skill_list)
    response = call_llm(
        task="echo_skill_dedup",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=120,
        temperature=0.0,
    )
    text = response.choices[0].message.content
    return _parse_dedup(text, {s.name for s in listed})


_dedup_impl: Callable[[str, List[SkillInfo]], DedupResult] = _default_dedup_impl


def set_dedup_impl(impl: Callable[[str, List[SkillInfo]], DedupResult]) -> None:
    """Test seam — inject a deterministic dedup result."""
    global _dedup_impl
    _dedup_impl = impl


def reset_dedup_impl() -> None:
    global _dedup_impl
    _dedup_impl = _default_dedup_impl


def check_duplicate(task_text: str) -> DedupResult:
    """Is there already a skill that covers ``task_text``?

    Gated by aux_config: when the echo_skill_dedup channel is off (no aux
    model), returns "no match" so nomination proceeds unguarded rather than
    being silently blocked. Fail-soft on every error.
    """
    if not task_text or not task_text.strip():
        return DedupResult(match=None)
    try:
        from . import aux_config
        if not aux_config.aux_enabled_for("echo_skill_dedup"):
            return DedupResult(match=None)
    except Exception as exc:
        logger.debug("check_duplicate: aux_config gate failed: %s", exc)
        return DedupResult(match=None)
    try:
        skills = enumerate_skills()
        if not skills:
            return DedupResult(match=None)
        return _dedup_impl(task_text, skills)
    except Exception as exc:
        logger.debug("check_duplicate failed: %s", exc, exc_info=True)
        return DedupResult(match=None)
