"""Record-and-replay layer for Echo's auxiliary LLM calls.

Two channels in Echo invoke a separate LLM in fire-and-forget daemon
threads:

  * Layer B classifier — `nl_classifier._default_classifier_impl(text)`
  * Layer C judge     — `judge._default_judge_impl(skill_id, confidence)`

The evaluation harness needs reproducible runs without paying real API
cost on every iteration. This module records the (input, output) pairs
of those calls on the first run and serves them from disk thereafter.

Usage from an experiment driver:

    from plugins.echo_signals import llm_cache

    # First run — populate the cache:
    llm_cache.enable_record("/tmp/llm-cache.jsonl")
    run_simulator()
    llm_cache.disable()

    # Subsequent runs — deterministic replay:
    llm_cache.enable_replay("/tmp/llm-cache.jsonl", strict=True)
    run_simulator()
    llm_cache.disable()

Cache key is `sha256(task + "\\x00" + canonical_input)`. We do NOT
include the model name in the key — there is one auxiliary LLM per
task per process and we treat that binding as stable across a run.

Storage format is JSON Lines, one record per call:

    {"task": "classifier", "key": "<hex>", "input": {"text": "..."},
     "output": "positive"}
    {"task": "judge", "key": "<hex>",
     "input": {"skill_id": "...", "confidence": 0.17},
     "output": {"verdict": "ok", "reason": null, "context": null}}

Both record and replay are installed by replacing the module-level
`_default_*_impl` reference (the same path the unit tests use via
`set_classifier_impl` / `set_judge_impl`), so we compose cleanly with
the existing test injectors. `disable()` restores whatever impl was
active before `enable_*` ran.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from . import judge as _judge
from . import nl_classifier as _nl

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Cache state
# ----------------------------------------------------------------------

_lock = threading.Lock()
# Each entry: (task, key) -> output (already deserialised).
_memory: Dict[tuple, Any] = {}
_mode: Optional[str] = None  # "record" | "replay" | None
_path: Optional[Path] = None
_strict: bool = True
# Saved-impl handles so disable() can restore exact prior state.
_saved_classifier_impl: Optional[Callable[[str], _nl.Label]] = None
_saved_judge_impl: Optional[Callable[[str, float], _judge.JudgeVerdict]] = None


def _make_key(task: str, payload: str) -> str:
    h = hashlib.sha256()
    h.update(task.encode("utf-8"))
    h.update(b"\x00")
    h.update(payload.encode("utf-8"))
    return h.hexdigest()


def _canonical(obj: Any) -> str:
    """Deterministic JSON for hashing inputs. Sorted keys, no whitespace."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _verdict_to_dict(v: _judge.JudgeVerdict) -> Dict[str, Any]:
    return {"verdict": v.verdict, "reason": v.reason, "context": v.context}


def _dict_to_verdict(d: Dict[str, Any]) -> _judge.JudgeVerdict:
    return _judge.JudgeVerdict(
        verdict=d["verdict"],
        reason=d.get("reason"),
        context=d.get("context"),
    )


# ----------------------------------------------------------------------
# Append-on-record
# ----------------------------------------------------------------------

def _append(record: Dict[str, Any]) -> None:
    """Append a single JSONL record to the cache file under the lock."""
    assert _path is not None
    line = json.dumps(record, ensure_ascii=False) + "\n"
    with _lock:
        with _path.open("a", encoding="utf-8") as f:
            f.write(line)


def _load_into_memory(path: Path) -> int:
    """Read a JSONL cache file into `_memory`. Returns row count loaded."""
    if not path.exists():
        return 0
    n = 0
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning("llm_cache: bad line skipped (%s): %s", exc, line[:120])
                continue
            task = record.get("task")
            key = record.get("key")
            out = record.get("output")
            if not task or not key:
                continue
            _memory[(task, key)] = out
            n += 1
    return n


# ----------------------------------------------------------------------
# Recording wrappers
# ----------------------------------------------------------------------

def _record_classifier(real_impl: Callable[[str], _nl.Label]) -> Callable[[str], _nl.Label]:
    def wrapped(text: str) -> _nl.Label:
        key = _make_key("classifier", _canonical({"text": text}))
        out = real_impl(text)
        _append({"task": "classifier", "key": key,
                 "input": {"text": text}, "output": out})
        return out
    return wrapped


def _record_judge(real_impl: Callable[[str, float], _judge.JudgeVerdict]
                  ) -> Callable[[str, float], _judge.JudgeVerdict]:
    def wrapped(skill_id: str, confidence: float) -> _judge.JudgeVerdict:
        # Round confidence to 6 dp for stable hashing — keeps tiny float
        # noise from re-running prior steps from causing a cache miss.
        c_norm = round(float(confidence), 6)
        key = _make_key("judge", _canonical({"skill_id": skill_id, "confidence": c_norm}))
        verdict = real_impl(skill_id, confidence)
        _append({"task": "judge", "key": key,
                 "input": {"skill_id": skill_id, "confidence": c_norm},
                 "output": _verdict_to_dict(verdict)})
        return verdict
    return wrapped


# ----------------------------------------------------------------------
# Replay wrappers
# ----------------------------------------------------------------------

class CacheMiss(LookupError):
    """Raised in strict replay when a call has no cached entry."""


def _replay_classifier(fallback_impl: Callable[[str], _nl.Label]
                       ) -> Callable[[str], _nl.Label]:
    def wrapped(text: str) -> _nl.Label:
        key = _make_key("classifier", _canonical({"text": text}))
        with _lock:
            hit = _memory.get(("classifier", key))
        if hit is not None:
            return hit  # type: ignore[return-value]
        if _strict:
            raise CacheMiss(f"classifier({text!r})")
        return fallback_impl(text)
    return wrapped


def _replay_judge(fallback_impl: Callable[[str, float], _judge.JudgeVerdict]
                  ) -> Callable[[str, float], _judge.JudgeVerdict]:
    def wrapped(skill_id: str, confidence: float) -> _judge.JudgeVerdict:
        c_norm = round(float(confidence), 6)
        key = _make_key("judge", _canonical({"skill_id": skill_id, "confidence": c_norm}))
        with _lock:
            hit = _memory.get(("judge", key))
        if hit is not None:
            return _dict_to_verdict(hit)
        if _strict:
            raise CacheMiss(f"judge({skill_id!r}, {confidence!r})")
        return fallback_impl(skill_id, confidence)
    return wrapped


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------

def enable_record(path: str | Path) -> None:
    """Start recording every classifier/judge call to `path` (JSONL append).

    Wraps the CURRENT active impl, not the module default — so this composes
    with anything an outer caller has already installed via set_*_impl.
    `disable()` puts the prior impl back.
    The file is created if absent; existing lines are preserved.
    """
    global _mode, _path, _saved_classifier_impl, _saved_judge_impl
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.touch(exist_ok=True)

    _saved_classifier_impl = _nl._classifier_impl
    _saved_judge_impl = _judge._judge_impl

    _nl.set_classifier_impl(_record_classifier(_nl._classifier_impl))
    _judge.set_judge_impl(_record_judge(_judge._judge_impl))

    _mode = "record"
    _path = p
    logger.info("llm_cache: recording to %s", p)


def enable_replay(path: str | Path, *, strict: bool = True) -> None:
    """Serve all classifier/judge calls from `path`.

    If `strict=True` (default), a cache miss raises CacheMiss. If False,
    falls through to whatever impl was active before enable_replay ran.
    """
    global _mode, _path, _strict, _memory, _saved_classifier_impl, _saved_judge_impl
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"llm cache file not found: {p}")

    _memory = {}
    loaded = _load_into_memory(p)

    _saved_classifier_impl = _nl._classifier_impl
    _saved_judge_impl = _judge._judge_impl

    _nl.set_classifier_impl(_replay_classifier(_nl._classifier_impl))
    _judge.set_judge_impl(_replay_judge(_judge._judge_impl))

    _mode = "replay"
    _path = p
    _strict = strict
    logger.info("llm_cache: replaying from %s (%d entries, strict=%s)", p, loaded, strict)


def disable() -> None:
    """Restore the original impls. Safe to call when not active."""
    global _mode, _path, _strict, _memory, _saved_classifier_impl, _saved_judge_impl

    if _saved_classifier_impl is not None:
        _nl.set_classifier_impl(_saved_classifier_impl)
    else:
        _nl.reset_classifier_impl()

    if _saved_judge_impl is not None:
        _judge.set_judge_impl(_saved_judge_impl)
    else:
        _judge.reset_judge_impl()

    _saved_classifier_impl = None
    _saved_judge_impl = None
    _mode = None
    _path = None
    _strict = True
    _memory = {}


def status() -> Dict[str, Any]:
    """Inspection helper for tests + the evaluation driver."""
    return {
        "mode": _mode,
        "path": str(_path) if _path else None,
        "strict": _strict,
        "entries": len(_memory),
    }
