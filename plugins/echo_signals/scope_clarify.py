"""M2 scope confirmation via an in-conversation clarify question.

Replaces the old dashboard ThumbsBar scope widget (binary "reuse the whole
approach / only the general idea", which users found hard to read). Now, when
a skill is created, Echo:

  1. reads the new skill and asks its auxiliary LLM (echo_scope task) to
     summarize 2-4 concrete applicability scopes, ordered narrow → broad;
  2. nudges the agent (via the cache-safe inject channel) to ask the user with
     Hermes' clarify tool, using exactly those options;
  3. captures the user's pick from the clarify result that lands in the next
     turn's conversation_history (clarify bypasses post_tool_call, but its
     result IS appended to the message list), and stores it on echo_skill_scope.

State machine on echo_skill_scope.scope_state:
  pending → options_ready → asked → confirmed

Same aux pattern as judge / skill_dedup: fire-and-forget, aux_config-gated,
fail-soft, test-injectable via set_options_impl.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

from .db import get_echo_conn

logger = logging.getLogger(__name__)


# clarify caps choices at 4; we ask for 2-4 so the question stays readable.
MIN_OPTIONS = 2
MAX_OPTIONS = 4
_VALID_BREADTH = ("narrow", "medium", "broad")


@dataclass
class ScopeOption:
    label: str
    breadth: str  # narrow | medium | broad


SCOPE_OPTIONS_PROMPT = """\
A new reusable skill has just been created in an AI agent's skill library. We \
need to confirm with the user HOW BROADLY this skill should apply in the \
future — its applicability scope.

Skill name: {name}
Skill description: {description}
Skill body (excerpt):
---
{body}
---

Produce {min}–{max} concrete, mutually-distinct scope options for THIS skill, \
ordered from the NARROWEST (only the exact kind of task just done) to the \
BROADEST (a general methodology that transfers to many task types). Each \
option must be a short, plain-language phrase a non-technical user can \
instantly understand — NOT abstract jargon like "reuse the whole approach". \
Make them specific to this skill's actual subject matter.

Write the option labels in the SAME LANGUAGE as the skill above (e.g. if the \
skill is described in Chinese, the labels must be in Chinese).

Respond with ONLY a JSON object, no prose:
{{"options": [
  {{"label": "<short phrase>", "breadth": "narrow"}},
  {{"label": "<short phrase>", "breadth": "medium"}},
  {{"label": "<short phrase>", "breadth": "broad"}}
]}}
"""


# ---------------------------------------------------------------------------
# Read one skill's content
# ---------------------------------------------------------------------------


def _read_skill(skill_id: str) -> Optional[tuple[str, str, str]]:
    """Return (name, description, body) for skill_id, or None if not found."""
    try:
        from agent.skill_utils import (
            extract_skill_description,
            get_all_skills_dirs,
            iter_skill_index_files,
            parse_frontmatter,
        )
    except Exception as exc:  # pragma: no cover
        logger.debug("_read_skill import failed: %s", exc)
        return None
    for skills_dir in get_all_skills_dirs():
        try:
            for path in iter_skill_index_files(skills_dir, "SKILL.md"):
                try:
                    content = path.read_text(encoding="utf-8", errors="replace")
                    fm, body = parse_frontmatter(content)
                    if str(fm.get("name", "")).strip() == skill_id:
                        return (skill_id, extract_skill_description(fm) or "", body or "")
                except Exception:
                    continue
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# Option generation (auxiliary LLM)
# ---------------------------------------------------------------------------


def _parse_options(text: str) -> List[ScopeOption]:
    if not isinstance(text, str):
        return []
    candidate = text.strip()
    if candidate.startswith("```"):
        lines = candidate.split("\n")
        candidate = "\n".join(lines[1:-1]) if len(lines) >= 3 else candidate
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start < 0 or end < 0 or end < start:
        return []
    try:
        obj = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError:
        return []
    raw = obj.get("options")
    if not isinstance(raw, list):
        return []
    out: List[ScopeOption] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label", "")).strip()
        if not label:
            continue
        breadth = str(item.get("breadth", "")).strip().lower()
        if breadth not in _VALID_BREADTH:
            breadth = "medium"
        out.append(ScopeOption(label=label, breadth=breadth))
    # Enforce the 2-4 bound (clarify itself caps at 4).
    return out[:MAX_OPTIONS]


def _default_options_impl(name: str, description: str, body: str) -> List[ScopeOption]:
    from agent.auxiliary_client import call_llm

    prompt = SCOPE_OPTIONS_PROMPT.format(
        name=name, description=description, body=(body or "")[:1500],
        min=MIN_OPTIONS, max=MAX_OPTIONS,
    )
    response = call_llm(
        task="echo_scope",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=300,
        temperature=0.2,
    )
    return _parse_options(response.choices[0].message.content)


_options_impl: Callable[[str, str, str], List[ScopeOption]] = _default_options_impl


def set_options_impl(impl: Callable[[str, str, str], List[ScopeOption]]) -> None:
    global _options_impl
    _options_impl = impl


def reset_options_impl() -> None:
    global _options_impl
    _options_impl = _default_options_impl


def generate_scope_options(skill_id: str) -> List[ScopeOption]:
    """Generate 2-4 applicability options for a skill. Gated + fail-soft."""
    try:
        from . import aux_config
        if not aux_config.aux_enabled_for("echo_scope"):
            return []
    except Exception:
        return []
    info = _read_skill(skill_id)
    if info is None:
        return []
    try:
        opts = _options_impl(*info)
    except Exception as exc:
        logger.debug("generate_scope_options failed: %s", exc, exc_info=True)
        return []
    return opts if len(opts) >= MIN_OPTIONS else []


# ---------------------------------------------------------------------------
# Async generation + storage
# ---------------------------------------------------------------------------


def start_scope_options_async(skill_id: str) -> None:
    """Test seam — spawn option generation on a daemon thread."""
    t = threading.Thread(target=generate_and_store, args=(skill_id,), daemon=True)
    t.start()


def generate_and_store(skill_id: str) -> int:
    """Generate options and stash them on the skill's scope row. Returns the
    option count (0 = nothing stored, scope stays 'pending')."""
    opts = generate_scope_options(skill_id)
    if len(opts) < MIN_OPTIONS:
        return 0
    try:
        conn = get_echo_conn()
        payload = json.dumps(
            [{"label": o.label, "breadth": o.breadth} for o in opts],
            ensure_ascii=False,
        )
        conn.execute(
            "UPDATE echo_skill_scope "
            "SET scope_options = ?, scope_state = 'options_ready', updated_at = ? "
            "WHERE skill_id = ?",
            (payload, time.time(), skill_id),
        )
        conn.commit()
        return len(opts)
    except Exception as exc:
        logger.debug("generate_and_store persist failed: %s", exc, exc_info=True)
        return 0


# ---------------------------------------------------------------------------
# Inject channel — nudge the agent to ask via clarify
# ---------------------------------------------------------------------------


def _load_options(raw: Optional[str]) -> List[ScopeOption]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    out = []
    for d in data if isinstance(data, list) else []:
        if isinstance(d, dict) and d.get("label"):
            out.append(ScopeOption(label=str(d["label"]),
                                   breadth=str(d.get("breadth", "medium"))))
    return out


def _build_scope_nudge(skill_id: str, options: List[ScopeOption]) -> str:
    choices = " / ".join(f"「{o.label}」" for o in options)
    quoted = "、".join(f'"{o.label}"' for o in options)
    return (
        f"[Echo 提示] 你刚刚创建了技能「{skill_id}」。请用 clarify 工具问用户："
        f"以后什么情况下应该复用这个技能（它的适用范围）？"
        f"请把下面这几个由窄到宽的选项**原样**作为 clarify 的 choices 提供给用户："
        f"{quoted}。问题可以是「这个技能以后适用到什么范围？」。候选范围：{choices}。"
        f"用户选择后正常继续即可，不需要额外解释。"
    )


def consume_scope_nudge(session_id: Optional[str]) -> Optional[str]:
    """Return the scope-question directive for this session and mark it asked.

    Fires for a skill whose options are ready (scope_state='options_ready')
    and whose creating session is this one. At most once (state→'asked').
    """
    if not session_id:
        return None
    try:
        conn = get_echo_conn()
        row = conn.execute(
            "SELECT skill_id, scope_options FROM echo_skill_scope "
            "WHERE session_id = ? AND scope_state = 'options_ready' "
            "ORDER BY updated_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        options = _load_options(row["scope_options"])
        if len(options) < MIN_OPTIONS:
            return None
        text = _build_scope_nudge(row["skill_id"], options)
        conn.execute(
            "UPDATE echo_skill_scope SET scope_state = 'asked', updated_at = ? "
            "WHERE skill_id = ?",
            (time.time(), row["skill_id"]),
        )
        conn.commit()
        return text
    except Exception as exc:
        logger.debug("consume_scope_nudge failed: %s", exc, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Capture the answer from conversation history
# ---------------------------------------------------------------------------


def _iter_clarify_results(conversation_history) -> List[dict]:
    """Yield parsed clarify result payloads found in the message list.

    clarify's result is appended to messages as a tool-result whose content is
    a JSON string with question / choices_offered / user_response.
    """
    found = []
    if not isinstance(conversation_history, (list, tuple)):
        return found
    for msg in conversation_history:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "tool":
            continue
        content = msg.get("content")
        if not isinstance(content, str) or "user_response" not in content:
            continue
        try:
            obj = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(obj, dict) and "user_response" in obj:
            found.append(obj)
    return found


def _match_choice(user_response: str, options: List[ScopeOption]) -> Optional[str]:
    """Best-effort map a free-text user_response to one of the options."""
    if not user_response:
        return None
    ur = user_response.strip()
    for o in options:
        if o.label.strip() == ur:
            return o.label
    # Looser containment match (the agent may prepend "A · " etc.).
    for o in options:
        if o.label.strip() and o.label.strip() in ur:
            return o.label
        if ur and ur in o.label.strip():
            return o.label
    return None


def capture_scope_from_history(session_id: Optional[str], conversation_history) -> bool:
    """If this session has a skill awaiting a scope answer, look for the
    clarify result in history and record the user's pick. Returns True on
    capture. Idempotent (only acts on scope_state='asked')."""
    if not session_id:
        return False
    try:
        conn = get_echo_conn()
        row = conn.execute(
            "SELECT skill_id, scope_options FROM echo_skill_scope "
            "WHERE session_id = ? AND scope_state = 'asked' "
            "ORDER BY updated_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if row is None:
            return False
        options = _load_options(row["scope_options"])
        clarifies = _iter_clarify_results(conversation_history)
        if not clarifies:
            return False
        # Prefer the clarify whose offered choices overlap our options; fall
        # back to the most recent clarify (scope is the latest question asked).
        option_labels = {o.label.strip() for o in options}
        chosen_payload = None
        for c in clarifies:
            offered = c.get("choices_offered") or []
            if isinstance(offered, list) and option_labels & {
                str(x).strip() for x in offered
            }:
                chosen_payload = c
        if chosen_payload is None:
            chosen_payload = clarifies[-1]

        user_response = str(chosen_payload.get("user_response") or "").strip()
        if not user_response:
            return False
        matched = _match_choice(user_response, options) or user_response

        # Map to the legacy scope_level enum when the chosen option's breadth
        # is unambiguous; otherwise leave it 'unknown'.
        level = "unknown"
        for o in options:
            if o.label == matched:
                if o.breadth == "broad":
                    level = "broad"
                elif o.breadth == "narrow":
                    level = "narrow"
                break

        conn.execute(
            "UPDATE echo_skill_scope "
            "SET scope_choice = ?, scope_level = ?, scope_state = 'confirmed', "
            "    user_confirmed_at = ?, updated_at = ? "
            "WHERE skill_id = ?",
            (matched, level, time.time(), time.time(), row["skill_id"]),
        )
        conn.commit()
        logger.info("Echo M2: captured scope %r for skill %r", matched, row["skill_id"])
        return True
    except Exception as exc:
        logger.debug("capture_scope_from_history failed: %s", exc, exc_info=True)
        return False
