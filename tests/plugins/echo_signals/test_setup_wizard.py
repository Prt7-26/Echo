"""Tests for plugins.echo_signals.setup_wizard.

We don't drive Hermes' real interactive setup here — we just stub the
prompt helpers and assert that the wizard writes the right shape into
the config dict.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from plugins.echo_signals import setup_wizard


def _stub_helpers(*, choice: int, same_for_both: bool = True,
                  endpoint=("https://x", "sk-x", "model-x")):
    """Build a dict of stubbed prompt helpers that the wizard will use."""
    captured = {"warnings": [], "successes": []}

    def prompt(question, default=None, password=False):
        if "Base URL" in question:
            return endpoint[0]
        if "API key" in question:
            return endpoint[1]
        if "Model name" in question:
            return endpoint[2]
        return default or ""

    def prompt_yes_no(question, default=True):
        return same_for_both

    def prompt_choice(question, choices, default=0, description=None):
        return choice

    helpers = {
        "print_header": lambda _t: None,
        "print_info": lambda *_a, **_kw: None,
        "print_warning": lambda msg: captured["warnings"].append(msg),
        "print_success": lambda msg: captured["successes"].append(msg),
        "prompt": prompt,
        "prompt_choice": prompt_choice,
        "prompt_yes_no": prompt_yes_no,
    }
    return helpers, captured


def test_shared_mode_writes_only_aux_mode():
    helpers, captured = _stub_helpers(choice=1)
    with patch.object(setup_wizard, "_prompt_helpers", return_value=helpers):
        config = {}
        setup_wizard.run_aux_provider_setup(config)
    assert config["echo"]["aux_mode"] == "shared"
    assert "auxiliary" not in config        # didn't touch per-task config
    assert any("share" in w.lower() for w in captured["warnings"])


def test_off_mode_writes_only_aux_mode():
    helpers, captured = _stub_helpers(choice=2)
    with patch.object(setup_wizard, "_prompt_helpers", return_value=helpers):
        config = {}
        setup_wizard.run_aux_provider_setup(config)
    assert config["echo"]["aux_mode"] == "off"
    assert "auxiliary" not in config
    assert any("off" in w.lower() for w in captured["warnings"])


def test_separate_same_endpoint_writes_both_tasks():
    helpers, captured = _stub_helpers(
        choice=0,
        same_for_both=True,
        endpoint=("https://x", "sk-x", "model-x"),
    )
    with patch.object(setup_wizard, "_prompt_helpers", return_value=helpers):
        config = {}
        setup_wizard.run_aux_provider_setup(config)
    assert config["echo"]["aux_mode"] == "separate"
    aux = config["auxiliary"]
    for task in ("echo_classifier", "echo_judge"):
        assert aux[task]["base_url"] == "https://x"
        assert aux[task]["api_key"] == "sk-x"
        assert aux[task]["model"] == "model-x"
    assert captured["successes"]  # success printed


def test_separate_distinct_endpoints_writes_each():
    # Stub returns different endpoints per call. We track which task is being
    # set up via the print_info message.
    state = {"task": None}

    def prompt(question, default=None, password=False):
        if "Base URL" in question:
            return f"https://{state['task']}"
        if "API key" in question:
            return f"sk-{state['task']}"
        if "Model name" in question:
            return f"model-{state['task']}"
        return default or ""

    def print_info(msg, *a, **kw):
        if "echo_classifier" in str(msg):
            state["task"] = "classifier"
        elif "echo_judge" in str(msg):
            state["task"] = "judge"

    helpers = {
        "print_header": lambda _t: None,
        "print_info": print_info,
        "print_warning": lambda _m: None,
        "print_success": lambda _m: None,
        "prompt": prompt,
        "prompt_choice": lambda *a, **kw: 0,
        "prompt_yes_no": lambda *a, **kw: False,  # distinct endpoints
    }
    with patch.object(setup_wizard, "_prompt_helpers", return_value=helpers):
        config = {}
        setup_wizard.run_aux_provider_setup(config)

    aux = config["auxiliary"]
    assert aux["echo_classifier"]["base_url"] == "https://classifier"
    assert aux["echo_judge"]["base_url"] == "https://judge"
    assert aux["echo_classifier"]["api_key"] == "sk-classifier"
    assert aux["echo_judge"]["api_key"] == "sk-judge"
