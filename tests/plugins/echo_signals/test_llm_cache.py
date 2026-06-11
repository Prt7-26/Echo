"""Tests for the LLM record-and-replay layer (plugins.echo_signals.llm_cache)."""

from __future__ import annotations

from pathlib import Path

import pytest

from plugins.echo_signals import judge as _judge
from plugins.echo_signals import llm_cache
from plugins.echo_signals import nl_classifier as _nl


@pytest.fixture(autouse=True)
def _clean_state():
    """Make sure each test starts/ends with no cache wrappers installed."""
    llm_cache.disable()
    _nl.reset_classifier_impl()
    _judge.reset_judge_impl()
    yield
    llm_cache.disable()
    _nl.reset_classifier_impl()
    _judge.reset_judge_impl()


# ---------------------------------------------------------------------
# Record mode
# ---------------------------------------------------------------------


def test_record_writes_jsonl_for_classifier(tmp_path):
    cache_path = tmp_path / "cache.jsonl"
    calls = []

    def fake_impl(text: str):
        calls.append(text)
        return "positive"

    _nl.set_classifier_impl(fake_impl)
    llm_cache.enable_record(cache_path)

    label = _nl._classifier_impl("hello world")

    assert label == "positive"
    assert calls == ["hello world"]
    assert cache_path.exists()
    lines = cache_path.read_text().strip().splitlines()
    assert len(lines) == 1
    assert '"task": "classifier"' in lines[0]
    assert '"output": "positive"' in lines[0]
    assert "hello world" in lines[0]


def test_record_writes_jsonl_for_judge(tmp_path):
    cache_path = tmp_path / "cache.jsonl"

    def fake_judge(skill_id: str, confidence: float):
        return _judge.JudgeVerdict(verdict="degraded", reason="test reason")

    _judge.set_judge_impl(fake_judge)
    llm_cache.enable_record(cache_path)

    v = _judge._judge_impl("alpha", 0.2)

    assert v.verdict == "degraded"
    assert v.reason == "test reason"
    lines = cache_path.read_text().strip().splitlines()
    assert len(lines) == 1
    assert '"task": "judge"' in lines[0]
    assert '"verdict": "degraded"' in lines[0]


def test_record_appends_existing_file(tmp_path):
    cache_path = tmp_path / "cache.jsonl"
    cache_path.write_text('{"task": "old", "key": "x", "output": "y"}\n')

    _nl.set_classifier_impl(lambda t: "neutral")
    llm_cache.enable_record(cache_path)
    _nl._classifier_impl("hi")
    llm_cache.disable()

    lines = cache_path.read_text().strip().splitlines()
    assert len(lines) == 2  # existing line preserved, new line appended
    assert '"task": "old"' in lines[0]
    assert '"task": "classifier"' in lines[1]


# ---------------------------------------------------------------------
# Replay mode
# ---------------------------------------------------------------------


def test_replay_serves_classifier_from_disk(tmp_path):
    cache_path = tmp_path / "cache.jsonl"

    real_calls = []

    def fake_real(text: str):
        real_calls.append(text)
        return "positive"

    _nl.set_classifier_impl(fake_real)
    llm_cache.enable_record(cache_path)
    _nl._classifier_impl("first call")
    llm_cache.disable()

    assert real_calls == ["first call"]  # recorded once

    # New session — strict replay must NOT touch the real impl.
    sentinel_calls = []

    def sentinel_impl(text: str):
        sentinel_calls.append(text)
        return "negative"

    _nl.set_classifier_impl(sentinel_impl)
    llm_cache.enable_replay(cache_path, strict=True)

    out = _nl._classifier_impl("first call")
    assert out == "positive"  # served from cache, not from sentinel
    assert sentinel_calls == []


def test_replay_serves_judge_with_correct_verdict_fields(tmp_path):
    cache_path = tmp_path / "cache.jsonl"

    def real(skill_id, confidence):
        return _judge.JudgeVerdict(
            verdict="exclusion",
            context="don't use for ad-hoc shell tasks",
        )

    _judge.set_judge_impl(real)
    llm_cache.enable_record(cache_path)
    _judge._judge_impl("beta", 0.15)
    llm_cache.disable()

    _judge.set_judge_impl(lambda *_: pytest.fail("real should not be hit on replay"))
    llm_cache.enable_replay(cache_path, strict=True)

    v = _judge._judge_impl("beta", 0.15)
    assert v.verdict == "exclusion"
    assert v.context == "don't use for ad-hoc shell tasks"
    assert v.reason is None


def test_replay_strict_miss_raises(tmp_path):
    cache_path = tmp_path / "cache.jsonl"
    cache_path.write_text("")  # empty cache

    _nl.set_classifier_impl(lambda t: "positive")
    llm_cache.enable_replay(cache_path, strict=True)

    with pytest.raises(llm_cache.CacheMiss):
        _nl._classifier_impl("never recorded")


def test_replay_nonstrict_miss_falls_through(tmp_path):
    cache_path = tmp_path / "cache.jsonl"
    cache_path.write_text("")

    real_calls = []

    def real(text):
        real_calls.append(text)
        return "negative"

    _nl.set_classifier_impl(real)
    llm_cache.enable_replay(cache_path, strict=False)

    out = _nl._classifier_impl("never recorded")
    assert out == "negative"
    assert real_calls == ["never recorded"]


def test_replay_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        llm_cache.enable_replay(tmp_path / "does-not-exist.jsonl")


# ---------------------------------------------------------------------
# disable() restores
# ---------------------------------------------------------------------


def test_disable_restores_prior_classifier_impl(tmp_path):
    cache_path = tmp_path / "cache.jsonl"

    def custom(text: str):
        return "neutral"

    _nl.set_classifier_impl(custom)
    assert _nl._classifier_impl is custom

    llm_cache.enable_record(cache_path)
    assert _nl._classifier_impl is not custom  # wrapped

    llm_cache.disable()
    assert _nl._classifier_impl is custom  # restored exactly


def test_disable_restores_prior_judge_impl(tmp_path):
    cache_path = tmp_path / "cache.jsonl"
    cache_path.touch()

    def custom_judge(skill_id, confidence):
        return _judge.JudgeVerdict(verdict="ok")

    _judge.set_judge_impl(custom_judge)
    llm_cache.enable_replay(cache_path, strict=False)
    assert _judge._judge_impl is not custom_judge

    llm_cache.disable()
    assert _judge._judge_impl is custom_judge


def test_status_reports_active_mode(tmp_path):
    cache_path = tmp_path / "cache.jsonl"
    assert llm_cache.status()["mode"] is None

    llm_cache.enable_record(cache_path)
    s = llm_cache.status()
    assert s["mode"] == "record"
    assert s["path"] == str(cache_path)

    llm_cache.disable()
    assert llm_cache.status()["mode"] is None


# ---------------------------------------------------------------------
# Round-trip: record then replay
# ---------------------------------------------------------------------


def test_round_trip_multi_call(tmp_path):
    """Recording N distinct calls and replaying serves all N deterministically."""
    cache_path = tmp_path / "cache.jsonl"

    def real(text):
        return {"hi": "positive", "bye": "negative", "meh": "neutral"}[text]

    _nl.set_classifier_impl(real)
    llm_cache.enable_record(cache_path)
    for t in ("hi", "bye", "meh", "hi"):  # repeats included
        _nl._classifier_impl(t)
    llm_cache.disable()

    # 4 records written (cache appends every call — dedup happens on read).
    assert len(cache_path.read_text().splitlines()) == 4

    # On replay, last-write-wins per key is fine because the value is the same.
    _nl.set_classifier_impl(lambda t: pytest.fail("real should not run on replay"))
    llm_cache.enable_replay(cache_path, strict=True)

    assert _nl._classifier_impl("hi") == "positive"
    assert _nl._classifier_impl("bye") == "negative"
    assert _nl._classifier_impl("meh") == "neutral"
    assert _nl._classifier_impl("hi") == "positive"
