"""Tests for the Layer B/C runtime gate (plugins.echo_signals.aux_config)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from plugins.echo_signals import aux_config, judge, nl_classifier


# ---------------------------------------------------------------------
# get_aux_mode
# ---------------------------------------------------------------------


def test_default_mode_is_shared_when_no_config():
    with patch.object(aux_config, "_load_config", return_value={}):
        assert aux_config.get_aux_mode() == "shared"


def test_mode_separate():
    cfg = {"echo": {"aux_mode": "separate"}}
    with patch.object(aux_config, "_load_config", return_value=cfg):
        assert aux_config.get_aux_mode() == "separate"


def test_mode_off():
    cfg = {"echo": {"aux_mode": "off"}}
    with patch.object(aux_config, "_load_config", return_value=cfg):
        assert aux_config.get_aux_mode() == "off"


def test_unknown_mode_falls_back_to_default():
    cfg = {"echo": {"aux_mode": "garbage"}}
    with patch.object(aux_config, "_load_config", return_value=cfg):
        assert aux_config.get_aux_mode() == "shared"


# ---------------------------------------------------------------------
# aux_enabled_for
# ---------------------------------------------------------------------


def test_off_disables_everything():
    cfg = {"echo": {"aux_mode": "off"}}
    with patch.object(aux_config, "_load_config", return_value=cfg):
        assert not aux_config.aux_enabled_for("echo_classifier")
        assert not aux_config.aux_enabled_for("echo_judge")


def test_shared_enables_everything():
    cfg = {"echo": {"aux_mode": "shared"}}
    with patch.object(aux_config, "_load_config", return_value=cfg):
        assert aux_config.aux_enabled_for("echo_classifier")
        assert aux_config.aux_enabled_for("echo_judge")


def test_separate_requires_per_task_config():
    cfg = {"echo": {"aux_mode": "separate"}}
    with patch.object(aux_config, "_load_config", return_value=cfg):
        # No auxiliary.echo_* configured → disabled.
        assert not aux_config.aux_enabled_for("echo_classifier")
        assert not aux_config.aux_enabled_for("echo_judge")


def test_separate_with_provider_only_enables():
    cfg = {
        "echo": {"aux_mode": "separate"},
        "auxiliary": {"echo_classifier": {"provider": "openai"}},
    }
    with patch.object(aux_config, "_load_config", return_value=cfg):
        assert aux_config.aux_enabled_for("echo_classifier")
        assert not aux_config.aux_enabled_for("echo_judge")  # not configured


def test_separate_with_base_url_and_api_key_enables():
    cfg = {
        "echo": {"aux_mode": "separate"},
        "auxiliary": {
            "echo_judge": {
                "base_url": "https://api.openai.com/v1",
                "api_key": "sk-test",
            },
        },
    }
    with patch.object(aux_config, "_load_config", return_value=cfg):
        assert aux_config.aux_enabled_for("echo_judge")


def test_separate_with_base_url_only_does_not_enable():
    cfg = {
        "echo": {"aux_mode": "separate"},
        "auxiliary": {"echo_judge": {"base_url": "https://api.openai.com/v1"}},
    }
    with patch.object(aux_config, "_load_config", return_value=cfg):
        # base_url without an api_key is incomplete — must not pretend to be configured.
        assert not aux_config.aux_enabled_for("echo_judge")


# ---------------------------------------------------------------------
# Runtime guards
# ---------------------------------------------------------------------


def test_classify_returns_neutral_when_disabled():
    nl_classifier.set_classifier_impl(lambda _t: pytest.fail("must not be called"))
    cfg = {"echo": {"aux_mode": "off"}}
    with patch.object(aux_config, "_load_config", return_value=cfg):
        assert nl_classifier.classify("any text") == "neutral"
    nl_classifier.reset_classifier_impl()


def test_classify_calls_impl_when_enabled():
    nl_classifier.set_classifier_impl(lambda _t: "positive")
    cfg = {"echo": {"aux_mode": "shared"}}
    with patch.object(aux_config, "_load_config", return_value=cfg):
        assert nl_classifier.classify("happy text") == "positive"
    nl_classifier.reset_classifier_impl()


def test_run_judge_returns_ok_when_disabled():
    judge.set_judge_impl(lambda *_: pytest.fail("must not be called"))
    cfg = {"echo": {"aux_mode": "off"}}
    with patch.object(aux_config, "_load_config", return_value=cfg):
        v = judge.run_judge("some-skill", 0.05)
    assert v.verdict == "ok"
    judge.reset_judge_impl()


def test_run_judge_calls_impl_when_enabled():
    judge.set_judge_impl(lambda s, c: judge.JudgeVerdict(verdict="degraded", reason="ok"))
    cfg = {"echo": {"aux_mode": "shared"}}
    with patch.object(aux_config, "_load_config", return_value=cfg):
        v = judge.run_judge("some-skill", 0.05)
    assert v.verdict == "degraded"
    judge.reset_judge_impl()


def test_separate_mode_without_config_short_circuits_run_judge():
    judge.set_judge_impl(lambda *_: pytest.fail("must not be called"))
    cfg = {"echo": {"aux_mode": "separate"}}  # no auxiliary.echo_judge
    with patch.object(aux_config, "_load_config", return_value=cfg):
        v = judge.run_judge("some-skill", 0.05)
    assert v.verdict == "ok"
    judge.reset_judge_impl()
